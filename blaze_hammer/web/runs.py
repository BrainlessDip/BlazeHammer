"""Run registry: drives the shared core (prepare_run/execute_prepared).

One :class:`RunManager` per server process. Runs execute as asyncio tasks
on the uvicorn loop using exactly the CLI's pipeline
(``services.prepare_run`` → ``services.execute_prepared``); this module
only adds identity, status tracking and event fan-out.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import traceback
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from blaze_hammer.errors import BlazeHammerError
from blaze_hammer.security.redaction import redact_mapping

if TYPE_CHECKING:
    from collections.abc import Callable

    from blaze_hammer.config.models import RunConfig

HISTORY_LIMIT = 20


def _now_ms() -> int:
    return int(time.time() * 1000)


#: Above this size JSON excerpts are left as raw text (perf guard).
_MAX_REDACT_PARSE_BYTES = 65_536


def response_snapshot_fields(
    outcome: Any, *, response_logging: Any, sensitive_names: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Build the response-snapshot portion of a log entry.

    Pure function over a ``RequestOutcome`` — no I/O, safe under concurrency.
    Storage of the excerpt follows the configured mode:

        none   → never stored
        errors → stored for failed/non-success responses
        all    → always stored (still byte-capped)

    When a body exists and mode is not ``none``, the excerpt is always
    populated so the log endpoint can serve it regardless of storage mode.
    """
    snap = outcome.body
    content_type = snap.content_type if snap is not None else None
    body_size = (snap.total_bytes or 0) if snap is not None else 0

    fields: dict[str, Any] = {
        "content_type": content_type,
        "body_size": body_size,
        "response_headers": dict(snap.headers) if snap is not None else {},
        "response_body_excerpt": None,
        "response_body_truncated": False,
    }

    mode = str(response_logging.mode)
    if mode == "none":
        return fields

    # Always populate the excerpt when a body exists (mode != none).
    # The mode controls *storage gating* (what persists to disk exporters),
    # but the log endpoint should always serve available body data.
    if snap is None:
        return fields

    redact_keys = tuple(response_logging.redact_keys or ())
    text = snap.text
    if text is not None:
        small_enough = len(text) <= _MAX_REDACT_PARSE_BYTES
        if redact_keys and content_type and "json" in content_type.lower() and small_enough:
            try:
                parsed = json.loads(text)
                masked: Any = None
                if isinstance(parsed, dict):
                    masked = redact_mapping(parsed, redact_keys)
                elif isinstance(parsed, list):
                    masked = [
                        redact_mapping(item, redact_keys) if isinstance(item, dict) else item
                        for item in parsed
                    ]
                if masked is not None:
                    text = json.dumps(masked, ensure_ascii=False, separators=(",", ":"))
            except ValueError:
                pass  # not really JSON; keep the raw excerpt

    fields["response_body_excerpt"] = text
    fields["response_body_truncated"] = bool(snap.truncated)
    return fields


@dataclass
class RunHandle:
    run_id: str
    cfg: RunConfig
    requested: int
    status: str = "running"  # running|completed|cancelled|error
    error: str | None = None
    created_ms: int = field(default_factory=_now_ms)
    finished_ms: int | None = None
    prepared: Any = None  # services.PreparedRun (avoids import cycle)
    task: asyncio.Task[Any] | None = None
    #: Redacted per-request log entries (ring buffer).
    log: deque[dict[str, Any]] = field(default_factory=lambda: deque())
    total_logged: int = 0
    sample_store: Any = field(default=None, init=False, repr=False)

    def live_stats(self) -> dict[str, Any]:
        if self.prepared is None or self.prepared.collector is None:
            return {
                "elapsed_s": 0.0,
                "rps": 0.0,
                "completed": 0,
                "requested": self.requested,
                "success": 0,
                "failed": 0,
                "retries": 0,
                "latency_ms": {},
                "status_codes": {},
                "error_counts": {},
            }
        snapshot = self.prepared.collector.snapshot(
            target=self.cfg.target,
            method=self.cfg.method.value,
            requested=self.requested,
            interrupted=(self.status == "cancelled"),
        )
        latency = {
            key: (round(value * 1000, 1) if value is not None else None)
            for key, value in (
                ("min", snapshot.latency.min_s),
                ("mean", snapshot.latency.mean_s),
                ("p50", snapshot.latency.p50_s),
                ("p90", snapshot.latency.p90_s),
                ("p95", snapshot.latency.p95_s),
                ("p99", snapshot.latency.p99_s),
                ("max", snapshot.latency.max_s),
            )
        }
        return {
            "elapsed_s": round(snapshot.duration_s, 2),
            "rps": round(snapshot.rps, 1),
            "completed": snapshot.completed,
            "requested": snapshot.requested,
            "success": snapshot.success,
            "failed": snapshot.failed,
            "retries": snapshot.retries,
            "interrupted": snapshot.interrupted,
            "latency_ms": latency,
            "status_codes": {str(k): v for k, v in sorted(snapshot.status_codes.items())},
            "error_counts": dict(snapshot.error_counts),
        }

    def summary(self) -> dict[str, Any]:
        stats = self.live_stats()
        latency = stats.get("latency_ms") or {}
        sample_count = len(self.sample_store.samples) if self.sample_store is not None else 0
        return {
            "run_id": self.run_id,
            "status": self.status,
            "target": self.cfg.target,
            "method": self.cfg.method.value,
            "requested": self.requested,
            "completed": stats["completed"],
            "success": stats["success"],
            "failed": stats["failed"],
            "average_response_time_ms": latency.get("mean"),
            "min_response_time_ms": latency.get("min"),
            "max_response_time_ms": latency.get("max"),
            "status_codes": stats.get("status_codes", {}),
            "error": self.error,
            "sample_count": sample_count,
        }


class RunManager:
    """Owns active/finished runs; publishes events to subscribers."""

    def __init__(self, publish: Callable[[dict[str, Any]], Any]) -> None:
        self._publish = publish
        self._runs: OrderedDict[str, RunHandle] = OrderedDict()

    # -- lifecycle ----------------------------------------------------------

    async def start(
        self,
        cfg: RunConfig,
        *,
        headers_obj: dict[str, Any] | None = None,
        payload_obj: dict[str, Any] | None = None,
    ) -> RunHandle:
        from blaze_hammer import services
        from blaze_hammer.engine.planner import RequestTemplates

        templates = None
        if headers_obj is not None or payload_obj is not None:
            templates = RequestTemplates(headers=headers_obj, payload=payload_obj)

        run_id = secrets.token_urlsafe(8)
        handle = RunHandle(run_id=run_id, cfg=cfg, requested=cfg.requests)

        def bridge(outcome: Any) -> None:  # RequestOutcome; sync, loop thread
            try:
                self._record_outcome(handle, outcome)
            except Exception:
                traceback.print_exc()

        try:
            prepared = services.prepare_run(cfg, observers=[bridge], templates=templates)
        except BlazeHammerError:
            raise
        handle.prepared = prepared

        async def _runner() -> None:
            try:
                await self._publish(
                    {"type": "run.started", "run_id": run_id, "requested": cfg.requests}
                )
                stats = await services.execute_prepared(prepared)
                handle.status = "cancelled" if stats.interrupted else "completed"
                handle.finished_ms = _now_ms()
                event_type = "run.cancelled" if stats.interrupted else "run.completed"
                await self._publish({"type": event_type, "run_id": run_id, **handle.live_stats()})
            except asyncio.CancelledError:
                handle.status = "cancelled"
                handle.finished_ms = _now_ms()
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to clients
                handle.status = "error"
                handle.error = str(exc)[:500]
                handle.finished_ms = _now_ms()
                await self._publish(
                    {"type": "run.error", "run_id": run_id, "message": handle.error}
                )
            finally:
                prepared.close_resources()
                self._trim_history()

        handle.task = asyncio.create_task(_runner())
        self._runs[run_id] = handle
        return handle

    def stop(self, run_id: str) -> bool:
        handle = self._runs.get(run_id)
        if handle is None or handle.prepared is None or handle.prepared.runner is None:
            return False
        if handle.task is not None and not handle.task.done():
            handle.prepared.runner.request_stop()
            return True
        return False

    def get(self, run_id: str) -> RunHandle | None:
        return self._runs.get(run_id)

    def running_handles(self) -> list[RunHandle]:
        return [h for h in self._runs.values() if h.status == "running"]

    def list_summaries(self) -> list[dict[str, Any]]:
        return [h.summary() for h in reversed(self._runs.values())]

    def clear_history(self) -> int:
        finished = [rid for rid, h in self._runs.items() if h.status != "running"]
        for rid in finished:
            del self._runs[rid]
        return len(finished)

    def log_entries(self, run_id: str, *, offset: int = 0) -> list[dict[str, Any]]:
        handle = self._runs.get(run_id)
        if handle is None:
            return []
        return list(handle.log)[offset:]

    def sample_entries(self, run_id: str) -> list[dict[str, Any]]:
        handle = self._runs.get(run_id)
        if handle is None or handle.sample_store is None:
            return []
        return handle.sample_store.samples

    # -- internals ----------------------------------------------------------

    def _record_outcome(self, handle: RunHandle, outcome: Any) -> None:
        planner = getattr(handle.prepared, "planner", None)
        sensitive = planner.sensitive_names if planner is not None else ()

        # Lazily initialise the sample store on first outcome.
        if handle.sample_store is None and handle.cfg.samples.enabled:
            from blaze_hammer.web.samples import SampleStore

            handle.sample_store = SampleStore(handle.cfg.samples, sensitive)
        if handle.sample_store is not None:
            handle.sample_store.record(outcome)

        entry: dict[str, Any] = {
            "index": outcome.index,
            "ok": outcome.ok,
            "status": outcome.status_code,
            "latency_ms": round(outcome.latency_s * 1000, 1),
            "attempts": outcome.attempts,
            "ts": _now_ms(),
        }
        if outcome.error_category is not None:
            entry["error_category"] = outcome.error_category
            entry["error"] = outcome.error_message
        if outcome.resolved_headers:
            entry["request_headers"] = redact_mapping(outcome.resolved_headers, sensitive)
        if outcome.resolved_payload is not None:
            if isinstance(outcome.resolved_payload, dict):
                entry["request_body"] = redact_mapping(outcome.resolved_payload, sensitive)
            else:
                entry["request_body"] = outcome.resolved_payload
        if outcome.resolved_cookies:
            entry["request_cookies"] = redact_mapping(outcome.resolved_cookies, sensitive)

        snap = response_snapshot_fields(outcome, response_logging=handle.cfg.response_logging)
        entry.update(snap)
        handle.log.append(entry)
        handle.total_logged += 1

        event: dict[str, Any] = {
            "type": "request.completed",
            "run_id": handle.run_id,
            "index": outcome.index,
            "method": outcome.method,
            "ok": outcome.ok,
            "status": outcome.status_code,
            "latency_ms": entry["latency_ms"],
            "seq": handle.total_logged,
            "request_headers": entry.get("request_headers"),
            "request_body": entry.get("request_body"),
            "request_cookies": entry.get("request_cookies"),
            "response_headers": snap.get("response_headers", snap.get("headers")),
        }
        if outcome.error_category is not None:
            event["error_category"] = outcome.error_category
        if entry.get("content_type") is not None:
            event["content_type"] = entry["content_type"]
        event["body_size"] = entry.get("body_size", 0)
        excerpt = entry.get("response_body_excerpt")
        if excerpt is not None:
            # Compact copy for the wire; the full (capped) excerpt lives in
            # the run log endpoint.
            event["response_body_excerpt"] = str(excerpt)[:1024]
            event["response_body_truncated"] = entry.get(
                "response_body_truncated",
                False,
            )
        asyncio.get_running_loop().create_task(self._publish(event))

    def _trim_history(self) -> None:
        finished = [rid for rid, h in self._runs.items() if h.status != "running"]
        excess = len(finished) - HISTORY_LIMIT
        for rid in finished[: max(0, excess)]:
            self._runs.pop(rid, None)

    async def shutdown(self) -> None:
        """Cooperatively stop everything; used by graceful shutdown."""
        for handle in list(self._runs.values()):
            if handle.status == "running":
                self.stop(handle.run_id)
        tasks = [h.task for h in self._runs.values() if h.task is not None and not h.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=5)
