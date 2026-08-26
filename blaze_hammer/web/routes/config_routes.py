"""Config routes: resolved view, inline file contents, explicit save."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from blaze_hammer.config import editor as config_editor
from blaze_hammer.config.models import RunConfig
from blaze_hammer.config.project import (
    _normalize as project_normalize,
)
from blaze_hammer.config.project import (
    load_project_yaml,
)
from blaze_hammer.errors import ConfigurationError
from blaze_hammer.files import templates as template_files
from blaze_hammer.services import build_run_config
from blaze_hammer.web.dependencies import (
    WebState,
    get_state,
    require_session,
    require_session_csrf,
)
from blaze_hammer.web.models import (
    ConfigSaveResponse,
    ConfigView,
    SaveConfigRequest,
    SaveTemplatesRequest,
    SaveTemplatesResponse,
    TemplatesResponse,
)

router = APIRouter(prefix="")


def _build_base_cfg(state: WebState) -> RunConfig:
    """Resolved config for display/preview — tolerant of custom YAML keys."""
    import os

    from blaze_hammer.config.loader import collect_env_overrides

    if state.project_file is None:
        # Preserve the friendly zero-config error for bare servers.
        return build_run_config({}, profile=None, config_path=None)

    merged: dict[str, Any] = {}
    try:
        merged.update(load_project_yaml(state.project_file, strict=False))
    except ConfigurationError as exc:
        # Structural problems (bad mapping / broken YAML) still surface;
        # unknown-key noise is tolerated here by construction.
        if "must be a mapping" in str(exc.reason) or "Invalid YAML" in exc.message:
            raise
    merged.update(collect_env_overrides(os.environ))
    return RunConfig(**merged)


@router.get("/config", response_model=ConfigView)
async def get_config(request: Request) -> ConfigView:
    require_session(request)
    state: WebState = get_state(request)
    cfg = _build_base_cfg(state)

    def _p(value: Path | None) -> str | None:
        return str(value) if value is not None else None

    return ConfigView(
        target=cfg.target,
        method=cfg.method.value,
        requests=cfg.requests,
        concurrency=cfg.concurrency,
        delay=cfg.delay,
        timeout=cfg.timeout,
        retries=cfg.retries.max_retries,
        rate=cfg.rate,
        seed=cfg.seed,
        faker_locale=cfg.faker_locale,
        post_type=cfg.post_type.value,
        payload_file=_p(cfg.payload_file),
        headers_file=_p(cfg.headers_file),
        web_enabled=state.settings.enabled,
        auth_enabled=state.settings.auth.enabled,
        config_revision=(
            config_editor.read_config(state.project_file)[1]
            if state.project_file is not None
            else None
        ),
    )


@router.get("/config/templates", response_model=TemplatesResponse)
async def get_templates(request: Request) -> TemplatesResponse:
    """Editor text + revisions for payload/headers (authenticated).

    Revisions are SHA-256 of the exact file contents; send them back with
    ``POST /config/templates/save`` for optimistic-concurrency checking.
    Paths are reported relative to the project directory when possible.
    """
    require_session(request)
    state: WebState = get_state(request)
    cfg = _build_base_cfg(state)

    def _display(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.resolve().relative_to(state.project_dir.resolve()))
        except ValueError:  # outside the project dir; fall back to full path
            return str(path)

    payload_text, payload_rev = template_files.read_template(cfg.payload_file)
    headers_text, headers_rev = template_files.read_template(cfg.headers_file)
    return TemplatesResponse(
        payload_text=payload_text,
        headers_text=headers_text,
        payload=payload_text,
        headers=headers_text,
        payload_revision=payload_rev,
        headers_revision=headers_rev,
        payload_file=_display(cfg.payload_file),
        headers_file=_display(cfg.headers_file),
    )


@router.post(
    "/config/templates/save",
    response_model=SaveTemplatesResponse,
    responses={
        409: {
            "description": "Template was modified since it was loaded "
            "(optimistic-concurrency conflict)",
            "content": {
                "application/json": {
                    "example": {
                        "ok": False,
                        "error": {
                            "code": "TEMPLATE_CONFLICT",
                            "file": "payload",
                            "message": "Payload was modified since it was loaded.",
                            "current_revision": "<sha256>",
                        },
                    }
                }
            },
        },
        422: {"description": "Invalid JSON syntax or nothing to save"},
    },
)
async def save_templates(body: SaveTemplatesRequest, request: Request) -> SaveTemplatesResponse:
    """Persist edited payload/header templates.

    Flow: authenticate → resolve project files → validate JSON syntax
    (placeholders are plain strings, not validated here) → check supplied
    revisions (409 on conflict) → atomic writes → broadcast ``config.changed``
    → return the new revisions. Omitted fields are never written.
    """
    require_session_csrf(request)
    state: WebState = get_state(request)
    if state.project_file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PROJECT_NOT_FOUND",
                "message": "No blazehammer.yaml found; run 'bh init' first",
            },
        )
    cfg = _build_base_cfg(state)

    targets: dict[str, tuple[Path, str, str | None]] = {}
    if body.payload is not None:
        if cfg.payload_file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "TEMPLATE_NOT_CONFIGURED",
                    "file": "payload",
                    "message": "No payload file is configured in blazehammer.yaml",
                },
            )
        targets["payload"] = (Path(cfg.payload_file), body.payload, body.payload_revision)
    if body.headers is not None:
        if cfg.headers_file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "TEMPLATE_NOT_CONFIGURED",
                    "file": "headers",
                    "message": "No headers file is configured in blazehammer.yaml",
                },
            )
        targets["headers"] = (Path(cfg.headers_file), body.headers, body.headers_revision)

    if not targets:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "NOTHING_TO_SAVE",
                "message": "Provide 'payload' and/or 'headers' to save",
            },
        )

    # 1. Validate JSON syntax for every provided field before touching disk.
    for name, (_path, text, _rev) in targets.items():
        try:
            template_files.validate_json(text, name)
        except template_files.TemplateValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc

    # 2. Optimistic concurrency: supplied revision must match current content.
    for name, (path, _text, rev) in targets.items():
        if rev is None:
            continue
        _current_text, current_rev = template_files.read_template(path)
        if rev != current_rev:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "TEMPLATE_CONFLICT",
                    "file": name,
                    "message": f"{name.capitalize()} was modified since it was loaded.",
                    "current_revision": current_rev,
                },
            )

    # 3. Atomic writes (tmp + fsync + os.replace).
    saved: list[str] = []
    new_revisions: dict[str, str] = {}
    try:
        for name, (path, text, _rev) in targets.items():
            new_revisions[name] = template_files.atomic_write_text(path, text)
            saved.append(name)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "TEMPLATE_WRITE_FAILED",
                "message": f"Failed to write template file: {exc}",
            },
        ) from exc

    # 4. Notify connected clients (names only — never file contents).
    hub = state.hub
    if hub is not None:
        await hub.publish({"type": "config.changed", "changed": list(saved)})

    return SaveTemplatesResponse(
        saved=saved,
        payload_revision=new_revisions.get("payload"),
        headers_revision=new_revisions.get("headers"),
    )


_SAVE_SUPPORTED_FIELDS = frozenset(
    {
        "target",
        "method",
        "requests",
        "concurrency",
        "delay",
        "rate",
        "timeout",
        "retries",
        "seed",
        "faker_locale",
        "post_type",
        "web",
        "response_logging",
    }
)


def _apply_updates_to_fragment(fragment: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge PATCH values into the normalized fragment for validation."""
    merged = {**fragment}
    for key, value in updates.items():
        if key == "retries" and isinstance(value, int) and not isinstance(value, bool):
            nested = dict(merged.get("retries") or {})
            nested["max_retries"] = value
            merged["retries"] = nested
        elif isinstance(value, dict):
            base = dict(merged.get(key) or {})  # e.g. web:
            base.update(value)
            merged[key] = base
        else:
            merged[key] = value
    return merged


@router.post(
    "/config/save",
    response_model=ConfigSaveResponse,
    responses={
        409: {
            "description": "Configuration was modified since it was loaded",
            "content": {
                "application/json": {
                    "example": {
                        "ok": False,
                        "error": {
                            "code": "CONFIG_CONFLICT",
                            "message": "Configuration was modified since it was loaded.",
                            "current_revision": "<sha256>",
                        },
                    }
                }
            },
        },
    },
)
async def save_config(body: SaveConfigRequest, request: Request) -> ConfigSaveResponse:
    """PATCH-style configuration update (never a full-file rewrite).

    Only explicitly provided fields change; comments, ordering, quoting,
    blank lines and unknown/custom keys are preserved via round-trip YAML.
    An empty patch returns the current revision without touching the file.
    """
    require_session_csrf(request)
    state: WebState = get_state(request)
    if state.project_file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PROJECT_NOT_FOUND",
                "message": "No blazehammer.yaml found; run 'bh init' first",
            },
        )
    assert state.project_file is not None

    provided = body.model_fields_set - {"config_revision"}
    unsupported = sorted(provided - _SAVE_SUPPORTED_FIELDS)
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "UNSUPPORTED_FIELD",
                "message": f"Field(s) cannot be saved here: {', '.join(unsupported)}",
            },
        )

    updates: dict[str, Any] = {}
    for field in sorted(provided):
        value = getattr(body, field)
        if field == "web":
            if body.web is not None:
                web_updates = body.web.model_dump(exclude_unset=True)
                if web_updates:
                    updates["web"] = web_updates
        elif field == "response_logging":
            if body.response_logging is not None:
                rl_updates = body.response_logging.model_dump(exclude_unset=True)
                if rl_updates:
                    updates["response_logging"] = rl_updates
        elif field == "method" and value is not None:
            updates[field] = str(value).upper()
        elif field == "post_type" and value is not None:
            updates[field] = str(value).lower()
        else:
            updates[field] = value  # explicit nulls included on purpose

    # Optimistic concurrency against the file's current contents.
    _text_now, current_rev = config_editor.read_config(state.project_file)
    if body.config_revision is not None and body.config_revision != current_rev:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFIG_CONFLICT",
                "message": "Configuration was modified since it was loaded.",
                "current_revision": current_rev,
            },
        )

    # Validate the RESULTING configuration through the normal pipeline.
    import yaml as pyyaml

    try:
        current_raw = pyyaml.safe_load(_text_now) or {}
    except pyyaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CONFIGURATION",
                "message": f"Existing blazehammer.yaml is not valid YAML: {exc}",
            },
        ) from exc
    fragment = project_normalize(current_raw, base_dir=state.project_dir, strict=False)

    from pydantic import ValidationError

    from blaze_hammer.config.models import RunConfig

    candidate = _apply_updates_to_fragment(fragment, updates)
    try:
        RunConfig(**candidate)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(p) for p in error["loc"]) or "<config>"
            lines.append(f"- {location}: {error['msg']}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CONFIGURATION",
                "message": "Resulting configuration would be invalid:\n" + "\n".join(lines),
            },
        ) from exc

    # Persist atomically; no-op patches leave the file byte-identical.
    try:
        changed, new_revision = config_editor.update_fields(state.project_file, updates)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CONFIG_WRITE_FAILED",
                "message": f"Failed to write blazehammer.yaml: {exc}",
            },
        ) from exc

    if changed:
        hub = state.hub
        if hub is not None:
            await hub.publish({"type": "config.changed", "changed": list(changed)})

    return ConfigSaveResponse(changed=changed, config_revision=new_revision)


@router.get("/config/project")
async def project_info(request: Request) -> dict[str, Any]:
    require_session(request)
    state: WebState = get_state(request)
    return {
        "project_dir": str(state.project_dir),
        "project_file": str(state.project_file) if state.project_file else None,
    }
