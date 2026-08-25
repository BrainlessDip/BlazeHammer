"""Live Rich dashboard polling the UI-independent StatsCollector."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from blaze_hammer.stats.collector import StatsCollector


class DashboardView:
    """Renderable that re-reads collector state on every Rich refresh."""

    def __init__(self, collector: StatsCollector, requested: int) -> None:
        self._collector = collector
        self._requested = requested
        self._progress = Progress(
            TextColumn("Progress"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )
        self._task = self._progress.add_task("", total=requested)

    def __rich__(self) -> Group:
        stats = self._collector.snapshot(target="", method="", requested=self._requested)
        self._progress.update(self._task, completed=stats.completed)

        metrics = Table.grid(padding=(0, 2))
        metrics.add_column(style="bold")
        metrics.add_column()
        latency = stats.latency

        def ms(value: float | None) -> str:
            return f"{value * 1000:.0f} ms" if value is not None else "-"

        metrics.add_row("Rate", f"{stats.rps:.1f} req/s")
        metrics.add_row("Success", str(stats.success))
        metrics.add_row("Failed", str(stats.failed))
        if stats.retries:
            metrics.add_row("Retries", str(stats.retries))
        metrics.add_row("P50", ms(latency.p50_s))
        metrics.add_row("P95", ms(latency.p95_s))
        metrics.add_row("P99", ms(latency.p99_s))

        parts: list[Table | Text | Progress] = [self._progress, Text(""), metrics]
        if stats.status_codes:
            codes = Table.grid(padding=(0, 2))
            codes.add_column(style="bold")
            codes.add_column()
            for code, count in sorted(stats.status_codes.items())[-8:]:
                codes.add_row(str(code), str(count))
            parts.append(Text("Status Codes", style="bold"))
            parts.append(codes)
        if stats.error_counts:
            errors = Table.grid(padding=(0, 2))
            errors.add_column(style="bold")
            errors.add_column()
            for category, count in sorted(stats.error_counts.items()):
                errors.add_row(category, str(count))
            parts.append(Text("Errors", style="bold red"))
            parts.append(errors)
        return Group(*parts)


@contextmanager
def live_dashboard(
    console: Console,
    collector: StatsCollector,
    requested: int,
) -> Iterator[None]:
    """Rich Live panel; use null_dashboard() under simple mode."""
    view = DashboardView(collector, requested)
    with Live(
        Panel(view, title="Blaze Hammer", border_style="magenta"),
        refresh_per_second=6,
        console=console,
        transient=True,
    ):
        yield


@contextmanager
def null_dashboard() -> Iterator[None]:  # pragma: no cover - trivial
    yield
