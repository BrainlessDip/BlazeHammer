"""Run routes: start/stop/list/get, log, preview — all via the shared core."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from blaze_hammer.engine.planner import RequestTemplates
from blaze_hammer.errors import BlazeHammerError
from blaze_hammer.services import build_run_config
from blaze_hammer.web.dependencies import WebState, get_state, require_session, require_session_csrf
from blaze_hammer.web.models import (
    OkResponse,
    PreviewPlan,
    PreviewRequest,
    PreviewResponse,
    RunListResponse,
    RunStartRequest,
    RunSummary,
)

router = APIRouter(prefix="/api")


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
        raise HTTPException(status_code=400, detail=exc.message) from exc
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


@router.get("/runs/{run_id}/log")
async def run_log(run_id: str, request: Request, offset: int = 0) -> dict[str, Any]:
    require_session(request)
    state: WebState = get_state(request)
    assert state.manager is not None
    entries = state.manager.log_entries(run_id, offset=max(0, offset))
    return {"entries": entries}


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
        raise HTTPException(status_code=400, detail=exc.message) from exc

    try:
        sensitive = prepared.planner.sensitive_names
        plans_out = []
        for plan in prepared.planner.preview_plans(body.count):
            plans_out.append(
                PreviewPlan(
                    index=plan.index,
                    url=plan.url,
                    method=plan.method,
                    headers=redact_mapping(plan.headers or {}, sensitive),
                    body=redact_mapping(plan.body_preview or {}, sensitive),
                )
            )
    finally:
        await prepared.aclose()
    return PreviewResponse(plans=plans_out, sensitive_names=sensitive)
