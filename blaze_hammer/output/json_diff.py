"""Nested JSON diff with structured entries and Rich rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass(frozen=True)
class DiffEntry:
    op: str  # "+", "-", "~"
    path: str
    before: Any = None
    after: Any = None


def _format(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def diff_tree(before: Any, after: Any, path: str = "$") -> list[DiffEntry]:
    """Structural diff supporting nested dicts/lists."""
    entries: list[DiffEntry] = []
    _diff(before, after, path, entries)
    return entries


def _diff(before: Any, after: Any, path: str, entries: list[DiffEntry]) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before:
            child = f"{path}.{key}"
            if key not in after:
                entries.append(DiffEntry("-", child, before[key]))
            else:
                _diff(before[key], after[key], child, entries)
        for key in after:
            if key not in before:
                entries.append(DiffEntry("+", f"{path}.{key}", after=after[key]))
    elif isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            child = f"{path}[{index}]"
            if index >= len(before):
                entries.append(DiffEntry("+", child, after=after[index]))
            elif index >= len(after):
                entries.append(DiffEntry("-", child, before=before[index]))
            else:
                _diff(before[index], after[index], child, entries)
    elif before != after:
        entries.append(DiffEntry("~", path, before, after))


def render_diff(console: Console, title: str, entries: list[DiffEntry]) -> None:
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("", style="bold", width=2)
    table.add_column("Path", style="bold cyan", no_wrap=True)
    table.add_column("Before", style="red", overflow="fold")
    table.add_column("After", style="green", overflow="fold")
    styles = {"+": "bold green", "-": "bold red", "~": "yellow"}
    for entry in entries:
        style = styles.get(entry.op, "")
        table.add_row(
            entry.op,
            entry.path,
            "" if entry.op == "+" else _format(entry.before),
            "" if entry.op == "-" else _format(entry.after),
            style=style,
        )
    console.print(table)
