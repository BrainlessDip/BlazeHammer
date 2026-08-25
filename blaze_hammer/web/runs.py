"""Run registry: drives the shared core (prepare_run/execute_prepared).

One :class:`RunManager` per server process. Runs execute as asyncio tasks
on the uvicorn loop using exactly the CLI's pipeline
(``services.prepare_run`` → ``services.execute_prepared``); this module
only adds identity, status tracking and event fan-out.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from blaze_hammer.errors import BlazeHammerError, ConfigurationError
from blaze_hammer.security.redaction import redact_mapping

if TYPE_CHECKING:
    from collections.abc import Callable

    from blaze_hammer.config.models import RunConfig

HISTORY_LIMIT = 20
LOG_BUFFER = 200


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class RunHandle:
    run_id: str
    cfg: RunConfig
    requested: int
    status: str = "running"  # running|completed|stopped|error
    error: str | None = None
    created_ms: int = field(default_factory=_now_ms)
    finished_ms: int | None = None
    prepared: Any = None  # services.PreparedRun (avoids import cycle)
    task: asyncio.Task[Any] | None = None
    #: Redacted per-request log entries (ring buffer).
    log: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=LOG_BUFFER))
    total_logged: int = 0

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
            interrupted=(self.status == "stopped"),
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
        return {
            "run_id": self.run_id,
            "status": self.status,
            "target": self.cfg.target,
            "method": self.cfg.method.value,
            "requested": self.requested,
            "completed": stats["completed"],
            "success": stats["success"],
            "failed": stats["failed"],
            "error": self.error,
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

        if any(h.status == "running" for h in self._runs.values()):
            raise ConfigurationError(
                "A run is already in progress",
                hint="stop it first or wait for completion",
            )

        templates = None
        if headers_obj is not None or payload_obj is not None:
            templates = RequestTemplates(headers=headers_obj, payload=payload_obj)

        run_id = secrets.token_urlsafe(8)
        handle = RunHandle(run_id=run_id, cfg=cfg, requested=cfg.requests)

        def bridge(outcome: Any) -> None:  # RequestOutcome; sync, loop thread
            self._record_outcome(handle, outcome)

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
                handle.status = "stopped" if stats.interrupted else "completed"
                handle.finished_ms = _now_ms()
                await self._publish(
                    {"type": "run.completed", "run_id": run_id, **handle.live_stats()}
                )
            except asyncio.CancelledError:
                handle.status = "stopped"
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

    # -- internals ----------------------------------------------------------

    def _record_outcome(self, handle: RunHandle, outcome: Any) -> None:
        planner = getattr(handle.prepared, "planner", None)
        sensitive = planner.sensitive_names if planner is not None else ()
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
            entry["request_body"] = redact_mapping(outcome.resolved_payload, sensitive)
        body = outcome.body.text if outcome.body else None
        if body is not None:
            entry["response_body_excerpt"] = body[:2000]
        handle.log.append(entry)
        handle.total_logged += 1

        event: dict[str, Any] = {
            "type": "request.completed",
            "run_id": handle.run_id,
            "index": outcome.index,
            "ok": outcome.ok,
            "status": outcome.status_code,
            "latency_ms": entry["latency_ms"],
            "seq": handle.total_logged,
        }
        if outcome.error_category is not None:
            event["error_category"] = outcome.error_category
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
