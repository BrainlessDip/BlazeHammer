"""Deprecated shim — diffing now lives in ``blaze_hammer.output.json_diff``.

Keeps the legacy ``compare_json(before, after, file)`` signature working.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from blaze_hammer.output.json_diff import diff_tree, render_diff


def format_value(val: Any) -> str:
    if isinstance(val, (dict, list)):
        import json

        return json.dumps(val, indent=2, ensure_ascii=False)
    return str(val)


def compare_json(before: dict, after: dict, file: str) -> None:
    entries = diff_tree(before, after)
    console = Console()
    if not entries:
        console.print(f"JSON Differences - {file}: no differences")
        return
    render_diff(console, f"JSON Differences - {file}", entries)
