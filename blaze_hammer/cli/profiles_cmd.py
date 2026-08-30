"""``profile`` / ``profiles`` command groups (identical behavior)."""

from __future__ import annotations

from typing import Any

import click


@click.command(name="list")
def list_cmd() -> None:
    """List available profiles from ./profiles/."""
    from blaze_hammer.services import profiles_list as service_fn

    service_fn()


@click.command(name="show")
@click.argument("name")
def show_cmd(name: str) -> None:
    """Show the contents of a profile."""
    from blaze_hammer.services import profiles_show as service_fn

    service_fn(name)


@click.command(name="create")
@click.argument("name")
def create_cmd(name: str) -> None:
    """Create a minimal override-only profile template."""
    from blaze_hammer.services import profiles_create as service_fn

    code = service_fn(name)
    if code != 0:
        raise SystemExit(code)


def _build(group_name: str) -> Any:
    @click.group(name=group_name)
    def grp() -> None:
        """Manage reusable test profiles."""

    grp.add_command(list_cmd)
    grp.add_command(show_cmd)
    grp.add_command(create_cmd)
    return grp


profile = _build("profile")
profiles = _build("profiles")
