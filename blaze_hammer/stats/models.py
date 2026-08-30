"""Structured statistics models — no UI dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LatencySummary:
    """Latency aggregates in seconds."""

    count: int
    min_s: float | None = None
    max_s: float | None = None
    mean_s: float | None = None
    median_s: float | None = None
    p50_s: float | None = None
    p90_s: float | None = None
    p95_s: float | None = None
    p99_s: float | None = None


EMPTY_LATENCY = LatencySummary(count=0)


@dataclass(frozen=True)
class RunStats:
    """Immutable snapshot of a run, produced by ``StatsCollector.snapshot``."""

    target: str
    method: str
    requested: int
    completed: int
    success: int
    failed: int
    retries: int
    duration_s: float
    interrupted: bool
    latency: LatencySummary = EMPTY_LATENCY
    status_codes: dict[int, int] = field(default_factory=dict)
    error_counts: dict[str, int] = field(default_factory=dict)
    error_samples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def rps(self) -> float:
        return self.completed / self.duration_s if self.duration_s > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation used by exporters."""
        latency = {
            k.removesuffix("_s"): (None if v is None else round(v * 1000, 3))
            for k, v in (
                ("min_s", self.latency.min_s),
                ("mean_s", self.latency.mean_s),
                ("median_s", self.latency.median_s),
                ("p50_s", self.latency.p50_s),
                ("p90_s", self.latency.p90_s),
                ("p95_s", self.latency.p95_s),
                ("p99_s", self.latency.p99_s),
                ("max_s", self.latency.max_s),
            )
            if k != "count"
        }
        return {
            "target": self.target,
            "method": self.method,
            "requested": self.requested,
            "completed": self.completed,
            "success": self.success,
            "failed": self.failed,
            "retries": self.retries,
            "duration_s": round(self.duration_s, 3),
            "rps": round(self.rps, 2),
            "interrupted": self.interrupted,
            "latency_ms": latency,
            "status_codes": {str(k): v for k, v in sorted(self.status_codes.items())},
            "errors": dict(sorted(self.error_counts.items())),
        }


def percentile(sorted_values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile of pre-sorted values."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
