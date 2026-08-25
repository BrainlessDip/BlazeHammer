"""``interactive`` command — guided run configuration."""

from __future__ import annotations

import click


@click.command(name="interactive")
def interactive() -> None:
    """Configure a run step by step, then execute it."""
    from blaze_hammer.services import interactive_flow

    interactive_flow()
