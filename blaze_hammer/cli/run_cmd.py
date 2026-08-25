"""``run`` command: execute a load test (or dry-run/preview/json-diff)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from blaze_hammer.cli.options import (
    config_options,
    explicit_overrides,
    output_options,
)
from blaze_hammer.errors import EXIT_OK, BlazeHammerError

_OUTPUT_KEYS = (
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
)


@click.command(name="run")
@click.argument("url", required=False, metavar="[URL]")
@click.option(
    "--url",
    "url_option",
    default=None,
    help="Target URL (named form; conflicts with a positional URL).",
)
@config_options
@output_options
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Resolve placeholders and build the request, but never send anything.",
)
@click.option(
    "--preview",
    "preview",
    flag_value=1,
    default=None,
    help="Preview one representative request instead of running.",
)
@click.option(
    "--preview-count",
    type=click.IntRange(1, 100),
    default=None,
    help="Preview N generated requests instead of running.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt for unusually large runs.",
)
@click.option(
    "-jd",
    "--json-diff",
    "json_diff",
    multiple=True,
    default=None,
    help="Offline mode: resolve placeholders in FILE(s) and show differences.",
)
@click.pass_context
def run(ctx: click.Context, url: str | None, **_kwargs: Any) -> None:
    """Send load to URL using the resolved configuration."""
    overrides = explicit_overrides(ctx)

    # Special-cased options.
    json_diff_files: list[str] | None = overrides.pop("json_diff", None)
    dry_run: bool = overrides.pop("dry_run", False)
    preview_flag: int | None = overrides.pop("preview", None)
    preview_count: int | None = overrides.pop("preview_count", None)
    profile: str | None = overrides.pop("profile", None)
    config_path: str | None = overrides.pop("config_path", None)
    url_option: str | None = overrides.pop("url_option", None)
    provided_url: str | None = overrides.pop("url", None)

    # Positional URL wins the slot; reject genuine conflicts (same value OK).
    if provided_url and url_option and provided_url.strip() != url_option.strip():
        raise click.ClickException(
            "Conflicting target URLs\n\n"
            f"  positional: {provided_url}\n"
            f"  --url:      {url_option}\n\n"
            "Provide only one of the two."
        )
    target = url_option or url or provided_url

    # Normalize values into RunConfig shapes.
    if overrides.get("method"):
        overrides["method"] = str(overrides["method"]).upper()
    if overrides.get("post_type"):
        overrides["post_type"] = str(overrides["post_type"]).lower()
    for key in ("payload_file", "headers_file"):
        if isinstance(overrides.get(key), str):
            overrides[key] = Path(overrides[key])

    retries_max = overrides.pop("max_retries", None)
    if retries_max is not None:
        overrides["retries"] = {"max_retries": retries_max}

    output: dict[str, Any] = {}
    for key in _OUTPUT_KEYS:
        value = overrides.pop(key, None)
        if value is not None:
            output[key] = value
    if isinstance(output.get("save_responses_dir"), str):
        output["save_responses_dir"] = Path(output["save_responses_dir"])
    if isinstance(output.get("export_path"), str):
        output["export_path"] = Path(output["export_path"])
    if isinstance(output.get("log_file"), str):
        output["log_file"] = Path(output["log_file"])
    if output:
        overrides["output"] = output

    preview: dict[str, Any] = {}
    if dry_run:
        preview["dry_run"] = True
    effective_preview_count = preview_count or preview_flag
    if effective_preview_count is not None:
        preview["preview_count"] = effective_preview_count
    if preview:
        overrides["preview"] = preview

    if target:
        overrides["target"] = target

    exit_code = EXIT_OK
    try:
        if json_diff_files:
            from blaze_hammer.services import json_diff_files as service_fn

            ctx.exit(service_fn(json_diff_files))
        from blaze_hammer.services import build_run_config, run_load_test

        cfg = build_run_config(overrides, profile=profile, config_path=config_path)
        exit_code = run_load_test(cfg)
    except BlazeHammerError as error:
        from blaze_hammer.output.console import make_console, render_error

        render_error(make_console(), error, debug=bool(ctx.params.get("debug")))
        ctx.exit(error.exit_code)
    except Exception as error:  # noqa: BLE001 - concise unless --debug
        if ctx.params.get("debug"):
            raise
        from blaze_hammer.errors import BlazeHammerError as _bhe
        from blaze_hammer.output.console import plain_error

        click.echo(
            plain_error(
                _bhe(
                    f"Unexpected error: {error}",
                    hint="run with --debug for a traceback",
                )
            )
        )
        ctx.exit(1)
    ctx.exit(exit_code)
