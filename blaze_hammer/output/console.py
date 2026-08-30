"""Console primitives shared by every output surface."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

from blaze_hammer.errors import BlazeHammerError

if TYPE_CHECKING:
    from collections.abc import Mapping


def make_console(*, stderr: bool = False) -> Console:
    """Console tuned for stable output across terminals (legacy-safe)."""
    return Console(
        highlight=False,
        emoji=False,
        soft_wrap=False,
        file=sys.stderr if stderr else None,
    )


def render_error(
    console: Console,
    error: BlazeHammerError,
    *,
    debug: bool = False,
) -> None:
    """Friendly, actionable error panel; full trace only with --debug."""
    console.print("[bold red]Blaze Hammer Error[/bold red]")
    console.print("-" * 40)
    console.print(error.message)
    if error.reason:
        console.print(f"\nReason:\n  {error.reason}")
    if error.expected:
        console.print(f"\nExpected:\n  {error.expected}")
    if error.found:
        console.print(f"\nFound:\n  {error.found}")
    if error.hint:
        console.print(f"\nHint:\n  {error.hint}")
    if debug:
        console.print("\n[dim]--debug: original traceback follows[/dim]")
        console.print_exception(show_locals=False)
    else:
        console.print("\n[dim]Run with --debug for more information.[/dim]")


def plain_error(error: BlazeHammerError) -> str:
    """Single-line rendering for pipes/simple mode."""
    parts = [f"error: {error.message}"]
    if error.reason:
        parts.append(str(error.reason))
    if error.hint:
        parts.append(f"hint: {error.hint}")
    return " | ".join(parts)


def format_mapping(mapping: Mapping[str, object]) -> list[str]:
    """Stable 'key: value' lines used by previews."""
    return [f"{key}: {value}" for key, value in mapping.items()]
