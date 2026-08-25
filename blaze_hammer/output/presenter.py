"""High-level presentation helpers shared by CLI commands."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from blaze_hammer import APP_NAME, __version__

if TYPE_CHECKING:
    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.stats.models import RunStats


def banner(console: Console, cfg: RunConfig | None = None) -> None:
    console.print(f"[bold magenta]{APP_NAME}[/bold magenta] [dim]v{__version__}[/dim]")
    if cfg is not None:
        console.print(f"[dim]Target:[/] {cfg.target}")
    console.print()


class Presenter:
    """Owns user-facing rendering decisions for one invocation."""

    def __init__(self, cfg: RunConfig) -> None:
        self.cfg = cfg
        self.simple = bool(cfg.output.simple or not sys.stdout.isatty())
        self.console = Console(highlight=False, emoji=False)

    # -- final report --------------------------------------------------------

    def final_summary(self, stats: RunStats) -> None:
        if self.simple:
            self._plain_summary(stats)
        else:
            self._rich_summary(stats)

    def _rich_summary(self, stats: RunStats) -> None:
        c = self.console
        c.rule("[bold green]Final Report[/bold green]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_row("Duration", f"{stats.duration_s:.2f}s")
        grid.add_row("Requests/sec", f"{stats.rps:.2f}")
        grid.add_row("Completed", f"{stats.completed}/{stats.requested}")
        grid.add_row("Successful", f"[green]{stats.success}[/green]")
        grid.add_row("Failed", f"[red]{stats.failed}[/red]" if stats.failed else "0")
        if stats.retries:
            grid.add_row("Retries", str(stats.retries))
        lat = stats.latency
        if lat.count:

            def ms(v: float | None) -> str:
                return f"{v * 1000:.0f} ms" if v is not None else "-"

            grid.add_row(
                "Latency",
                f"P50 {ms(lat.p50_s)}  P90 {ms(lat.p90_s)}  "
                f"P95 {ms(lat.p95_s)}  P99 {ms(lat.p99_s)}",
            )
        c.print(grid)

        if stats.status_codes:
            table = Table(title="Status Codes")
            table.add_column("Code", justify="right")
            table.add_column("Count", justify="right")
            table.add_column("%", justify="right")
            total = max(stats.success, 1)
            for code, count in sorted(stats.status_codes.items()):
                table.add_row(str(code), str(count), f"{count / total * 100:.1f}%")
            c.print(table)

        if stats.error_counts:
            table = Table(title="Failures")
            table.add_column("Category")
            table.add_column("Count", justify="right")
            for category, count in sorted(stats.error_counts.items()):
                table.add_row(category, str(count))
            c.print(table)
            samples = [msg for msgs in stats.error_samples.values() for msg in msgs[:1]][:3]
            if samples:
                c.print("[bold red]Sample errors:[/bold red]")
                for sample in samples:
                    c.print(f" - {sample}")

        if stats.interrupted:
            c.print("[bold yellow]Run interrupted - partial results above.[/bold yellow]")

    def _plain_summary(self, stats: RunStats) -> None:
        c = self.console
        c.print("--- Final Report ---")
        c.print(f"Duration: {stats.duration_s:.2f}s")
        c.print(f"Requests/sec: {stats.rps:.2f}")
        c.print(f"Completed: {stats.completed}/{stats.requested}")
        c.print(f"Successful: {stats.success}")
        c.print(f"Failed: {stats.failed}")
        if stats.retries:
            c.print(f"Retries: {stats.retries}")
        lat = stats.latency
        if lat.count:

            def ms(value: float | None) -> str:
                return f"{value * 1000:.0f}" if value is not None else "-"

            c.print(
                f"Latency ms P50/P90/P95/P99: "
                f"{ms(lat.p50_s)}/{ms(lat.p90_s)}/{ms(lat.p95_s)}/{ms(lat.p99_s)}"
            )
        for code, count in sorted(stats.status_codes.items()):
            c.print(f"Status {code}: {count}")
        for category, count in sorted(stats.error_counts.items()):
            c.print(f"Error {category}: {count}")
        if stats.interrupted:
            c.print("Run interrupted - partial results above.")
