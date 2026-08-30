"""Run routes: start/stop/list/get, log, preview — all via the shared core."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from blaze_hammer.engine.planner import RequestTemplates
from blaze_hammer.errors import BlazeHammerError
from blaze_hammer.services import build_run_config
from blaze_hammer.web.dependencies import (
    WebState,
    blaze_to_http,
    get_state,
    require_session,
    require_session_csrf,
)
from blaze_hammer.web.models import (
    OkResponse,
    PreviewPlan,
    PreviewRequest,
    PreviewResponse,
    RequestSample,
    ResponseSnapshot,
    RunListResponse,
    RunStartRequest,
    RunSummary,
    ValidationResponse,
)

router = APIRouter()


def _parse_inline(text: str | None, label: str) -> dict[str, Any] | None:
    if text is None or not text.strip():
        return None
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} JSON invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail=f"{label} must be a JSON object")
    return parsed


@router.post("/runs", response_model=RunSummary)
async def start_run(body: RunStartRequest, request: Request) -> RunSummary:
    state: WebState = get_state(request)
    require_session_csrf(request)
    assert state.manager is not None
    headers_obj = _parse_inline(body.headers_text, "headers")
    payload_obj = _parse_inline(body.payload_text, "payload")

    overrides = body.to_overrides()
    # Editor/transport fields are not RunConfig keys.
    profile = overrides.pop("profile", None)
    overrides.pop("headers_text", None)
    overrides.pop("payload_text", None)
    try:
        cfg = build_run_config(
            {**overrides, "inline_templates": True},
            profile=profile,
            config_path=state.config_path(),
        )
        handle = await state.manager.start(cfg, headers_obj=headers_obj, payload_obj=payload_obj)
    except BlazeHammerError as exc:
        raise blaze_to_http(exc) from exc
    return RunSummary(**handle.summary())


@router.get("/runs", response_model=RunListResponse)
async def list_runs(request: Request) -> RunListResponse:
    require_session(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    return RunListResponse(runs=[RunSummary(**s) for s in state.manager.list_summaries()])


@router.get("/runs/{run_id}", response_model=RunSummary)
async def get_run(run_id: str, request: Request) -> RunSummary:
    require_session(request)
    state: WebState = get_state(request)
    handle = state.manager.get(run_id) if state.manager else None
    if handle is None:
        raise HTTPException(status_code=404, detail="Unknown run")
    return RunSummary(**handle.summary())


@router.post("/runs/{run_id}/stop", response_model=OkResponse)
async def stop_run(run_id: str, request: Request) -> OkResponse:
    require_session_csrf(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    if not state.manager.stop(run_id):
        raise HTTPException(status_code=404, detail="No running run with that id")
    return OkResponse()


@router.delete("/runs", response_model=OkResponse)
async def clear_history(request: Request) -> OkResponse:
    require_session_csrf(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    cleared = state.manager.clear_history()
    return OkResponse(ok=bool(cleared >= 0))


@router.get("/runs/{run_id}/log", response_model=list[ResponseSnapshot])
async def run_log(run_id: str, request: Request, offset: int = 0) -> list[ResponseSnapshot]:
    """Request-level response snapshots (bounded ring buffer, newest first)."""
    require_session(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    entries = state.manager.log_entries(run_id, offset=max(0, offset))
    out: list[ResponseSnapshot] = []
    for e in entries:
        out.append(
            ResponseSnapshot(
                request_index=e.get("index", 0),
                status_code=e.get("status"),
                response_time_ms=e.get("latency_ms"),
                content_type=e.get("content_type"),
                body_size=e.get("body_size", 0),
                response_body_excerpt=e.get("response_body_excerpt"),
                response_body_truncated=e.get("response_body_truncated", False),
                response_headers=e.get("response_headers") or e.get("headers") or {},
                error=e.get("error") or e.get("response_error"),
                ok=e.get("ok", True),
                attempts=e.get("attempts", 1),
                timestamp_ms=e.get("ts"),
                error_category=e.get("error_category"),
                request_headers=e.get("request_headers"),
                request_body=e.get("request_body"),
                request_cookies=e.get("request_cookies"),
            )
        )
    return out


@router.get("/runs/{run_id}/samples", response_model=list[RequestSample])
async def run_samples(run_id: str, request: Request) -> list[RequestSample]:
    """Representative request/response samples (bounded, not every request)."""
    require_session(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    entries = state.manager.sample_entries(run_id)
    return [RequestSample.model_validate(e) for e in entries]


@router.post("/preview", response_model=PreviewResponse)
async def preview(body: PreviewRequest, request: Request) -> PreviewResponse:
    """Resolve N sample requests through the real planner (never sends)."""

    from blaze_hammer.security.redaction import redact_mapping
    from blaze_hammer.services import prepare_run

    require_session_csrf(request)
    state: WebState = get_state(request)
    headers_obj = _parse_inline(body.headers_text, "headers")
    payload_obj = _parse_inline(body.payload_text, "payload")

    overrides = body.to_overrides()
    # Editor/transport fields are not RunConfig keys.
    profile = overrides.pop("profile", None)
    for transport in ("headers_text", "payload_text", "count"):
        overrides.pop(transport, None)
    templates = None
    if headers_obj is not None or payload_obj is not None:
        templates = RequestTemplates(headers=headers_obj, payload=payload_obj)
        overrides["inline_templates"] = True
    try:
        cfg = build_run_config(overrides, profile=profile, config_path=state.config_path())
        prepared = prepare_run(cfg, with_runner=False, templates=templates)
    except BlazeHammerError as exc:
        raise blaze_to_http(exc) from exc

    try:
        sensitive = prepared.planner.sensitive_names
        plans_out = []
        for plan in prepared.planner.preview_plans(body.count):
            raw = plan.body_preview
            if isinstance(raw, dict):
                body_dict: dict[str, Any] = redact_mapping(raw, sensitive)
            elif isinstance(raw, str):
                body_dict = {"_raw": raw}
            else:
                body_dict = {}
            plans_out.append(
                PreviewPlan(
                    index=plan.index,
                    url=plan.url,
                    method=plan.method,
                    headers=redact_mapping(plan.headers or {}, sensitive),
                    body=body_dict,
                )
            )
    finally:
        await prepared.aclose()
    return PreviewResponse(plans=plans_out, sensitive_names=sensitive)


@router.post("/validate", response_model=ValidationResponse)
async def validate(body: RunStartRequest, request: Request) -> ValidationResponse:
    """Full pre-flight check (config, files, placeholders) without sending."""
    from blaze_hammer.config.validation import ensure_config_valid
    from blaze_hammer.errors import PlaceholderError
    from blaze_hammer.templating import build_default_registry
    from blaze_hammer.templating.validation import TemplateValidator

    state: WebState = get_state(request)
    require_session_csrf(request)
    headers_obj = _parse_inline(body.headers_text, "headers")
    payload_obj = _parse_inline(body.payload_text, "payload")

    overrides = body.to_overrides()
    profile = overrides.pop("profile", None)
    for transport in ("headers_text", "payload_text", "count"):
        overrides.pop(transport, None)
    if headers_obj is not None or payload_obj is not None:
        overrides["inline_templates"] = True

    try:
        cfg = build_run_config(overrides, profile=profile, config_path=state.config_path())
        ensure_config_valid(cfg)

        issues: list[dict[str, str]] = []
        pairs: dict[str, tuple[Any, str]] = {}
        if payload_obj is not None:
            pairs["payload"] = (payload_obj, "")
        elif cfg.payload_file is not None and Path(str(cfg.payload_file)).is_file():
            raw = Path(str(cfg.payload_file)).read_text(encoding="utf-8")
            import json as _json

            pairs["payload"] = (_json.loads(raw), raw)
        if headers_obj is not None:
            pairs["headers"] = (headers_obj, "")
        elif cfg.headers_file is not None and Path(str(cfg.headers_file)).is_file():
            raw = Path(str(cfg.headers_file)).read_text(encoding="utf-8")
            import json as _json

            pairs["headers"] = (_json.loads(raw), raw)
        validator = TemplateValidator(build_default_registry(), faker_locale=cfg.faker_locale)
        report = validator.validate(pairs)
        for issue in report.issues:
            issues.append(
                {
                    "location": issue.location,
                    "token": issue.token,
                    "problem": issue.problem,
                    **({"suggestions": "|".join(issue.suggestions)} if issue.suggestions else {}),
                }
            )
    except PlaceholderError as exc:
        return ValidationResponse(ok=False, errors=[{"message": exc.message}])
    except BlazeHammerError as exc:
        raise blaze_to_http(exc) from exc
    return ValidationResponse(ok=not issues, issues=issues)
