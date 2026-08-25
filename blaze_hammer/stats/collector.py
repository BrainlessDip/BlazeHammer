"""Request statistics collector.

Pure Python and UI-independent: the Rich dashboard merely polls
:meth:`StatsCollector.snapshot`. All methods run on the event loop's
single thread; the lock only guards snapshot() against a concurrent
exporter thread.
"""

from __future__ import annotations

import threading
import time
from collections import Counter

from blaze_hammer.stats.models import LatencySummary, RunStats, percentile

MAX_ERROR_SAMPLES_PER_CATEGORY = 5


class StatsCollector:
    """Accumulates request outcomes into structured counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.perf_counter()
        self._latencies: list[float] = []
        self._status_codes: Counter[int] = Counter()
        self._error_counts: Counter[str] = Counter()
        self._error_samples: dict[str, list[str]] = {}
        self.success = 0
        self.failed = 0
        self.completed = 0
        self.retries = 0

    def record_success(self, status_code: int, latency_s: float) -> None:
        with self._lock:
            self.completed += 1
            self.success += 1
            self._latencies.append(latency_s)
            self._status_codes[status_code] += 1

    def record_failure(
        self,
        category: str,
        message: str,
        *,
        latency_s: float | None = None,
    ) -> None:
        with self._lock:
            self.completed += 1
            self.failed += 1
            if latency_s is not None:
                self._latencies.append(latency_s)
            self._error_counts[category] += 1
            samples = self._error_samples.setdefault(category, [])
            if len(samples) < MAX_ERROR_SAMPLES_PER_CATEGORY and message not in samples:
                samples.append(message)

    def record_retry(self) -> None:
        with self._lock:
            self.retries += 1

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self._started

    def latency_summary(self) -> LatencySummary:
        return summarize(sorted(self._latencies))

    def error_snapshot(self) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
        counts = dict(self._error_counts)
        samples = {cat: tuple(msgs) for cat, msgs in self._error_samples.items()}
        return counts, samples

    def status_snapshot(self) -> dict[int, int]:
        return dict(self._status_codes)

    def snapshot(
        self,
        *,
        target: str,
        method: str,
        requested: int,
        duration_s: float | None = None,
        interrupted: bool = False,
    ) -> RunStats:
        """Build a consistent :class:`RunStats` snapshot."""
        with self._lock:
            latencies = sorted(self._latencies)
            status_codes = dict(self._status_codes)
            error_counts = dict(self._error_counts)
            error_samples = {cat: tuple(msgs) for cat, msgs in self._error_samples.items()}
            success, failed, completed, retries = (
                self.success,
                self.failed,
                self.completed,
                self.retries,
            )
        return RunStats(
            target=target,
            method=method,
            requested=requested,
            completed=completed,
            success=success,
            failed=failed,
            retries=retries,
            duration_s=self.elapsed_s if duration_s is None else duration_s,
            interrupted=interrupted,
            latency=summarize(latencies),
            status_codes=status_codes,
            error_counts=error_counts,
            error_samples=error_samples,
        )


def summarize(latencies_sorted: list[float]) -> LatencySummary:
    if not latencies_sorted:
        return EMPTY_SUMMARY
    total = sum(latencies_sorted)
    count = len(latencies_sorted)
    median = percentile(latencies_sorted, 50)
    return LatencySummary(
        count=count,
        min_s=latencies_sorted[0],
        max_s=latencies_sorted[-1],
        mean_s=total / count,
        median_s=median,
        p50_s=median,
        p90_s=percentile(latencies_sorted, 90),
        p95_s=percentile(latencies_sorted, 95),
        p99_s=percentile(latencies_sorted, 99),
    )


EMPTY_SUMMARY = LatencySummary(count=0)


def reset_start(self: StatsCollector) -> None:
    """Restart the internal clock (used by tests)."""
    self._started = time.perf_counter()
