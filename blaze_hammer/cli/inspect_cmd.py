"""``inspect`` and ``validate`` commands."""

from __future__ import annotations

from typing import Any

import click

from blaze_hammer.cli.options import config_options, explicit_overrides
from blaze_hammer.errors import BlazeHammerError


def _execute(ctx: click.Context, url: str | None, *, show_sample: bool) -> None:
    overrides = explicit_overrides(ctx)
    provided_url: str | None = overrides.pop("url", None)
    profile: str | None = overrides.pop("profile", None)
    config_path: str | None = overrides.pop("config_path", None)
    target = url or provided_url

    if overrides.get("method"):
        overrides["method"] = str(overrides["method"]).upper()
    if overrides.get("post_type"):
        overrides["post_type"] = str(overrides["post_type"]).lower()

    from pathlib import Path

    for key in ("payload_file", "headers_file"):
        if isinstance(overrides.get(key), str):
            overrides[key] = Path(overrides[key])

    retries_max = overrides.pop("max_retries", None)
    if retries_max is not None:
        overrides["retries"] = {"max_retries": retries_max}

    if target:
        overrides["target"] = target
    elif not _project_available(config_path):
        # Legacy UX: a bare 'inspect' with example files still explains itself.
        overrides["target"] = "https://example.com/ (example - not contacted)"
    # Inspect never sends anything.
    overrides["preview"] = {"dry_run": False, "preview_count": None}

    try:
        from blaze_hammer.services import build_run_config, inspect_config

        cfg = build_run_config(overrides, profile=profile, config_path=config_path)
        code = inspect_config(cfg, show_sample=show_sample)
    except BlazeHammerError as error:
        from blaze_hammer.output.console import make_console, render_error

        render_error(make_console(), error, debug=bool(ctx.params.get("debug")))
        ctx.exit(error.exit_code)
    ctx.exit(code)


def _project_available(config_path: str | None) -> bool:
    """True when a target could still come from YAML or the environment."""
    import os

    if config_path:
        return True
    from blaze_hammer.config.project import find_project_config

    if find_project_config() is not None:
        return True
    return "BLAZE_TARGET" in os.environ or "BLAZE_HAMMER_TARGET" in os.environ


@click.command(name="inspect")
@click.argument("url", required=False, metavar="[URL]")
@config_options
@click.option("--debug", is_flag=True, default=False, help="Show tracebacks on errors.")
@click.pass_context
def inspect(ctx: click.Context, url: str | None, **_kwargs: Any) -> None:
    """Validate the configuration and explain it, with one generated request."""
    _execute(ctx, url, show_sample=True)


@click.command(name="validate")
@click.argument("url", required=False, metavar="[URL]")
@config_options
@click.option("--debug", is_flag=True, default=False, help="Show tracebacks on errors.")
@click.pass_context
def validate(ctx: click.Context, url: str | None, **_kwargs: Any) -> None:
    """Validate configuration, payload/header files and placeholders."""
    _execute(ctx, url, show_sample=False)
