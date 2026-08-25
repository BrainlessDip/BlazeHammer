"""Request preview / dry-run rendering (same pipeline as real execution)."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from rich.console import Console

from blaze_hammer.output.filters import truncate_text
from blaze_hammer.security.redaction import redact_mapping

if TYPE_CHECKING:
    from collections.abc import Sequence

    from blaze_hammer.engine.planner import RequestPlan


def render_request_plans(
    console: Console,
    plans: Sequence[RequestPlan],
    *,
    sensitive_names: tuple[str, ...] = (),
    max_payload_chars: int = 2000,
    dry_run: bool = False,
) -> None:
    for position, plan in enumerate(plans, start=1):
        suffix = f" (request {position}/{len(plans)})" if len(plans) > 1 else ""
        console.print("[bold]Blaze Hammer[/bold]")
        console.print("-" * 40)
        console.print(f"{plan.method} {plan.url}{suffix}\n")

        if plan.headers:
            console.print("[bold]Headers[/bold]")
            safe_headers = redact_mapping(plan.headers, sensitive_names)
            for key, value in safe_headers.items():
                console.print(f"  {key}: {value}")
            console.print()

        body = plan.body_preview
        if body is not None and body != {}:
            console.print("[bold]Payload[/bold]")
            rendered = json.dumps(
                redact_mapping(body, sensitive_names),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            shown, omitted = truncate_text(rendered, max_payload_chars)
            console.print(shown)
            if omitted:
                console.print(f"[dim]... {omitted:,} characters truncated[/dim]")
            console.print()

        if plan.method == "POST" and not body and not dry_run:
            console.print("[dim]No payload configured for POST.[/dim]\n")

    if dry_run:
        console.print("[bold yellow]DRY RUN - request was not sent.[/bold yellow]")


def preview_is_quite(stdout: object = None) -> bool:  # pragma: no cover - trivial helper

    stream = stdout or sys.stdout
    return not getattr(stream, "isatty", lambda: False)()
