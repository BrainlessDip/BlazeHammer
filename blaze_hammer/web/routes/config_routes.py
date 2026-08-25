"""Config routes: resolved view, inline file contents, explicit save."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from blaze_hammer.config.models import RunConfig
from blaze_hammer.config.project import render_template_yaml
from blaze_hammer.services import build_run_config
from blaze_hammer.web.dependencies import WebState, get_state, require_session, require_session_csrf
from blaze_hammer.web.models import ConfigView, OkResponse, SaveConfigRequest

router = APIRouter(prefix="/api")


def _build_base_cfg(state: WebState) -> RunConfig:
    return build_run_config({}, profile=None, config_path=state.config_path())


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
    )


@router.get("/config/templates")
async def get_templates(request: Request) -> dict[str, Any]:
    """Raw payload/headers JSON text for the editors (authenticated)."""
    require_session(request)
    state: WebState = get_state(request)
    cfg = _build_base_cfg(state)

    def _read(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    return {
        "payload_text": _read(cfg.payload_file),
        "headers_text": _read(cfg.headers_file),
        "payload_file": str(cfg.payload_file) if cfg.payload_file else None,
        "headers_file": str(cfg.headers_file) if cfg.headers_file else None,
    }


@router.post("/config/save", response_model=OkResponse)
async def save_config(body: SaveConfigRequest, request: Request) -> OkResponse:
    require_session_csrf(request)
    state: WebState = get_state(request)
    if state.project_file is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No blazehammer.yaml found; run 'bh init' first",
        )
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Saving regenerates blazehammer.yaml; resend with confirm=true",
        )
    cfg = _build_base_cfg(state)
    yaml_text = render_template_yaml(
        target=body.target or cfg.target,
        method=(body.method or cfg.method.value).upper(),
        requests=body.requests or cfg.requests,
        concurrency=body.concurrency or cfg.concurrency,
    )
    tmp_path = state.project_file.with_suffix(".yaml.tmp")
    try:
        tmp_path.write_text(yaml_text, encoding="utf-8")
        os.replace(tmp_path, state.project_file)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Cannot write config: {exc}") from exc
    return OkResponse()


@router.get("/config/project")
async def project_info(request: Request) -> dict[str, Any]:
    require_session(request)
    state: WebState = get_state(request)
    return {
        "project_dir": str(state.project_dir),
        "project_file": str(state.project_file) if state.project_file else None,
    }
