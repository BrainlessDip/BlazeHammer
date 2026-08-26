"""Async execution engine.

Design invariants:

- **Bounded work**: exactly ``concurrency`` worker tasks consume a
  ``Queue(maxsize=concurrency)``; the scheduler blocks on backpressure.
  Never one task per request.
- **Deterministic generation**: only the scheduler coroutine resolves
  templates, sequentially, so ``--seed`` reproduces the exact request
  sequence regardless of network timing.
- **Graceful shutdown**: Ctrl+C/SIGTERM stops scheduling, lets in-flight
  requests finish (bounded by a grace period), discards queued work and
  returns partial statistics with ``interrupted=True``.
- **UI independence**: this module never imports Rich; output observers
  are injected callables.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from blaze_hammer.engine.client import (
    BodySnapshot,
    read_body_snapshot,
    select_header_allowlist,
)
from blaze_hammer.engine.errors import classify_exception, describe_exception
from blaze_hammer.engine.planner import RequestPlan, RequestPlanner
from blaze_hammer.engine.rate_limiter import TokenBucket
from blaze_hammer.engine.retry import retry_delay
from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.stats.collector import StatsCollector

if TYPE_CHECKING:
    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.stats.models import RunStats

Observer = Callable[["RequestOutcome"], None]

_MAX_MESSAGE_LEN = 300


@dataclass
class RequestOutcome:
    index: int
    url: str
    method: str
    ok: bool
    status_code: int | None
    latency_s: float
    attempts: int
    error_category: str | None = None
    error_message: str | None = None
    resolved_payload: dict | None = None
    resolved_headers: dict | None = None
    body: BodySnapshot | None = None


class LoadTestRunner:
    """Executes a configured run against the shared HTTP client."""

    def __init__(
        self,
        cfg: RunConfig,
        planner: RequestPlanner,
        client: httpx.AsyncClient,
        collector: StatsCollector,
        *,
        bucket: TokenBucket | None = None,
        observers: Sequence[Observer] = (),
    ) -> None:
        self._cfg = cfg
        self._planner = planner
        self._client = client
        self._collector = collector
        self._bucket = bucket
        self._observers = tuple(observers)
        self.stop_event = asyncio.Event()
        self._scheduled = 0
        self._response_header_allowlist = select_header_allowlist(
            cfg.response_logging.allow_headers
        )

    def request_stop(self) -> None:
        """Signal graceful shutdown (signal handlers / interactive)."""
        self.stop_event.set()

    async def run(self) -> RunStats:
        started = time.perf_counter()
        queue: asyncio.Queue[RequestPlan | None] = asyncio.Queue(maxsize=self._cfg.concurrency)
        workers = [asyncio.create_task(self._worker(queue)) for _ in range(self._cfg.concurrency)]
        scheduler = asyncio.create_task(self._schedule(queue))

        interrupted = False
        try:
            await scheduler
        except (asyncio.CancelledError, KeyboardInterrupt):
            interrupted = True

        if interrupted:
            self.stop_event.set()
            scheduler.cancel()

        # Sentinels unblock idle workers; stop_event makes busy workers
        # discard anything still queued instead of executing it.
        for _worker in workers:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover - transient backpressure
                await queue.put(None)

        await asyncio.gather(*workers)
        duration = time.perf_counter() - started

        return self._collector.snapshot(
            target=self._cfg.target,
            method=self._cfg.method.value,
            requested=self._cfg.requests,
            duration_s=duration,
            # A set stop_event means the run ended early on purpose
            # (signal handler / request_stop), even when the scheduler's
            # loop noticed it without being cancelled.
            interrupted=interrupted or self.stop_event.is_set(),
        )

    async def _schedule(self, queue: asyncio.Queue[RequestPlan | None]) -> None:
        """Generate request plans sequentially and feed the work queue."""
        cfg = self._cfg
        for index in range(cfg.requests):
            if self.stop_event.is_set():
                break
            if cfg.delay > 0:
                await self._interruptible_sleep(cfg.delay)
                if self.stop_event.is_set():
                    break
            try:
                plan = self._planner.next_plan(index)
            except TemplateResolutionError as exc:
                self._collector.record_failure("template", describe_exception(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one bad plan must not kill a run
                self._collector.record_failure("template", f"plan generation failed: {exc}")
                continue
            if self._bucket is not None:
                await self._bucket.acquire()
                if self.stop_event.is_set():
                    break
            await queue.put(plan)
            self._scheduled += 1

    async def _worker(self, queue: asyncio.Queue[RequestPlan | None]) -> None:
        while True:
            plan = await queue.get()
            try:
                if plan is None:
                    return
                if self.stop_event.is_set():
                    continue  # discard remaining queued work quickly
                outcome = await self._execute(plan)
                self._record(outcome)
                for observer in self._observers:
                    try:
                        observer(outcome)
                    except Exception as exc:  # noqa: BLE001 - output must not kill runs
                        self._collector.record_failure("other", f"observer error: {exc}")
            finally:
                queue.task_done()

    async def _execute(self, plan: RequestPlan) -> RequestOutcome:
        policy = self._cfg.retries
        files = self._planner.httpx_files()
        attempts = 0
        while True:
            start = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {}
                if plan.method == "POST":
                    if files:
                        kwargs["files"] = files
                    if plan.post_type == "json":
                        kwargs["json"] = plan.json_body
                    else:
                        kwargs["data"] = plan.form_data
                response = await self._client.request(
                    plan.method,
                    plan.url,
                    headers=plan.headers,
                    **kwargs,
                )
                latency = time.perf_counter() - start
                delay = retry_delay(policy, attempts, None, response)
                if delay is None:
                    body = (
                        await read_body_snapshot(
                            response,
                            self._body_cap(),
                            header_allowlist=self._response_header_allowlist,
                        )
                        if self._cfg.needs_bodies
                        else None
                    )
                    await response.aclose()
                    return RequestOutcome(
                        index=plan.index,
                        url=plan.url,
                        method=plan.method,
                        ok=response.is_success,
                        status_code=response.status_code,
                        latency_s=latency,
                        attempts=attempts + 1,
                        resolved_payload=plan.body_preview,
                        resolved_headers=plan.headers,
                        body=body,
                    )
                self._collector.record_retry()
                await response.aclose()
                await asyncio.sleep(delay)
                attempts += 1
            except TemplateResolutionError as exc:
                return self._failure(plan, attempts, start, exc)
            except (TimeoutError, httpx.HTTPError, OSError) as exc:
                delay = retry_delay(policy, attempts, exc, None)
                if delay is not None:
                    self._collector.record_retry()
                    await asyncio.sleep(delay)
                    attempts += 1
                    continue
                return self._failure(plan, attempts, start, exc)

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that wakes immediately on graceful shutdown."""
        stop_wait = asyncio.create_task(self.stop_event.wait())
        timer = asyncio.create_task(asyncio.sleep(seconds))
        done, _pending = await asyncio.wait({stop_wait, timer}, return_when=asyncio.FIRST_COMPLETED)
        for task in (stop_wait, timer):
            if task not in done:
                task.cancel()

    def _failure(
        self,
        plan: RequestPlan,
        attempts: int,
        start: float,
        exc: BaseException,
    ) -> RequestOutcome:
        category = classify_exception(exc)
        return RequestOutcome(
            index=plan.index,
            url=plan.url,
            method=plan.method,
            ok=False,
            status_code=None,
            latency_s=time.perf_counter() - start,
            attempts=attempts + 1,
            error_category=category.value,
            error_message=describe_exception(exc)[:_MAX_MESSAGE_LEN],
            resolved_payload=plan.body_preview,
            resolved_headers=plan.headers,
        )

    def _record(self, outcome: RequestOutcome) -> None:
        if outcome.ok and outcome.status_code is not None:
            self._collector.record_success(outcome.status_code, outcome.latency_s)
        else:
            self._collector.record_failure(
                outcome.error_category or "other",
                outcome.error_message or "unknown failure",
                latency_s=outcome.latency_s,
            )

    def _body_cap(self) -> int | None:
        out = self._cfg.output
        if out.save_responses_dir is not None:
            return out.max_response_size * 4  # keep raw bytes generous on disk
        return out.max_response_size
