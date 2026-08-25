"""Shared Click option definitions for configuration-building commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click


def config_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """Options that describe WHAT to send / how to configure a run."""
    options = [
        click.option("--profile", "profile", default=None, help="Profile name or JSON path."),
        click.option(
            "--config",
            "config_path",
            type=click.Path(path_type=str),
            default=None,
            help="Project YAML path (default: ./blazehammer.yaml).",
        ),
        click.option(
            "-m",
            "--method",
            type=click.Choice(["GET", "POST"], case_sensitive=False),
            default=None,
            show_default=False,
            help="HTTP method.",
        ),
        click.option(
            "-p",
            "--payload",
            "payload_file",
            type=click.Path(path_type=str),
            default=None,
            help="Path to JSON payload file with {placeholder} support.",
        ),
        click.option(
            "--headers",
            "--h",
            "headers_file",
            type=click.Path(path_type=str),
            default=None,
            help="Path to JSON headers file with {placeholder} support.",
        ),
        click.option(
            "-dh",
            "--disable-headers",
            "disable_headers",
            is_flag=True,
            default=False,
            help="Do not include the headers file in requests.",
        ),
        click.option(
            "-fp",
            "--file-payload",
            "file_payload",
            is_flag=True,
            default=False,
            help="Send multipart attachments from blaze_hammer/ext/attachments.py.",
        ),
        click.option(
            "-pt",
            "--post-type",
            "post_type",
            type=click.Choice(["json", "form"], case_sensitive=False),
            default=None,
            help="Body encoding for POST requests.",
        ),
        click.option("-n", "--requests", type=int, default=None, help="Total requests."),
        click.option("-c", "--concurrency", type=int, default=None, help="Max parallel requests."),
        click.option("-d", "--delay", type=float, default=None, help="Delay between requests (s)."),
        click.option("--rate", type=float, default=None, help="Target request rate (req/s)."),
        click.option("-t", "--timeout", type=float, default=None, help="Per-request timeout (s)."),
        click.option("--seed", type=int, default=None, help="Seed for reproducible generation."),
        click.option(
            "--faker-locale",
            "faker_locale",
            type=str,
            default=None,
            help="Global Faker locale (e.g. en_US, bn_BD). Per-placeholder locale= overrides this.",
        ),
        click.option(
            "--retries",
            "max_retries",
            type=int,
            default=None,
            help="Retry attempts for connection failures/timeouts/retryable statuses.",
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def output_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """Options that control execution-time output."""
    options = [
        click.option(
            "-pp",
            "--print-payload",
            "print_payload",
            is_flag=True,
            default=False,
            help="Print each resolved payload.",
        ),
        click.option(
            "-pr",
            "--print-response",
            "print_response",
            is_flag=True,
            default=False,
            help="Print response bodies.",
        ),
        click.option(
            "-ph",
            "--print-headers",
            "print_headers",
            is_flag=True,
            default=False,
            help="Print resolved headers.",
        ),
        click.option(
            "--status",
            "status_filter",
            default=None,
            help="Only print responses with these codes, e.g. 500 or 400,401.",
        ),
        click.option(
            "--failed-only",
            "failed_only",
            is_flag=True,
            default=False,
            help="Print failed requests only.",
        ),
        click.option(
            "--success-only",
            "success_only",
            is_flag=True,
            default=False,
            help="Print successful requests only.",
        ),
        click.option(
            "--max-response-size",
            "max_response_size",
            type=int,
            default=None,
            help="Truncate printed/saved bodies to this many characters.",
        ),
        click.option(
            "--save-responses",
            "save_responses_dir",
            type=click.Path(path_type=str),
            default=None,
            help="Save summary.json/responses.jsonl/errors.jsonl here.",
        ),
        click.option(
            "--output",
            "export_path",
            type=click.Path(path_type=str),
            default=None,
            help="Export run summary to FILE (.json or .csv).",
        ),
        click.option(
            "-s",
            "--simple",
            is_flag=True,
            default=False,
            help="Plain output for CI/pipes (no live dashboard).",
        ),
        click.option("--log-level", type=str, default=None, help="Logging level (e.g. DEBUG)."),
        click.option(
            "--log-file",
            "log_file",
            type=click.Path(path_type=str),
            default=None,
            help="Write diagnostics to this file instead of stderr.",
        ),
        click.option("--debug", is_flag=True, default=False, help="Show tracebacks on errors."),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def parse_status_filter(raw: str | None) -> frozenset[int] | None:
    if raw is None:
        return None
    codes: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            codes.add(int(part))
        except ValueError as exc:
            raise click.BadParameter(
                f"'{part}' is not an HTTP status code", param_hint="--status"
            ) from exc
    return frozenset(codes)


def explicit_overrides(ctx: click.Context) -> dict[str, Any]:
    """Collect only options explicitly provided on the command line."""
    from click.core import ParameterSource

    overrides: dict[str, Any] = {}
    for name, value in ctx.params.items():
        if ctx.get_parameter_source(name) is ParameterSource.COMMANDLINE:
            overrides[name] = value
    return overrides
