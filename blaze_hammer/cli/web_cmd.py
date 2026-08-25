"""``web`` command — launch the Blaze Hammer Web GUI."""

from __future__ import annotations

from typing import Any

import click

from blaze_hammer.cli.options import config_options, explicit_overrides
from blaze_hammer.errors import EXIT_OK, BlazeHammerError


@click.command(name="web")
@config_options
@click.option("--host", default=None, help="Bind address (default: 127.0.0.1).")
@click.option("--port", type=int, default=None, help="Port (default: 8080; 0 = auto).")
@click.option("--username", default=None, help="Web username override.")
@click.option("--password", default=None, help="Web password override (hashed in memory).")
@click.option(
    "--open", "open_browser", is_flag=True, default=False, help="Open the dashboard in a browser."
)
@click.option(
    "--yes-i-know",
    is_flag=True,
    default=False,
    help="Confirm insecure exposure (0.0.0.0 without auth).",
)
@click.pass_context
def web(ctx: click.Context, **kwargs: Any) -> None:
    """Launch the Web GUI for this project."""
    overrides = explicit_overrides(ctx)
    profile: str | None = overrides.pop("profile", None)
    config_path: str | None = overrides.pop("config_path", None)

    web_overrides: dict[str, Any] = {}
    for key in ("host", "port", "username", "password"):
        # Pop unconditionally: leftover keys would leak into RunConfig.
        from_overrides = overrides.pop(key, None)
        value = kwargs.get(key)
        if value is None:
            value = from_overrides
        if value is not None:
            web_overrides[key] = value
    if web_overrides:
        overrides["web"] = web_overrides

    # Drop run-only options that config_options injects but web ignores.
    for run_only in (
        "method",
        "payload_file",
        "headers_file",
        "disable_headers",
        "file_payload",
        "post_type",
        "requests",
        "concurrency",
        "delay",
        "rate",
        "timeout",
        "seed",
        "faker_locale",
        "max_retries",
        "target",
        "url",
        "print_payload",
        "print_response",
        "print_headers",
        "failed_only",
        "success_only",
        "simple",
        "debug",
        "status_filter",
        "max_response_size",
        "save_responses_dir",
        "export_path",
        "log_level",
        "log_file",
    ):
        overrides.pop(run_only, None)

    open_browser = bool(kwargs.get("open_browser"))
    yes_i_know = bool(kwargs.get("yes_i_know"))

    from blaze_hammer.output.console import make_console, render_error
    from blaze_hammer.services import build_run_config, run_web_server

    try:
        cfg = build_run_config(overrides, profile=profile, config_path=config_path)
        code = run_web_server(cfg, open_browser=open_browser, yes_i_know=yes_i_know)
    except BlazeHammerError as error:
        render_error(make_console(), error, debug=bool(kwargs.get("debug")))
        ctx.exit(error.exit_code)
    ctx.exit(EXIT_OK if code == 0 else code)
