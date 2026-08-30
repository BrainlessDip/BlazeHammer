"""Output layer: console, dashboard, preview, filters, diff, exporters."""

from blaze_hammer.output.console import make_console, plain_error, render_error
from blaze_hammer.output.exporters import (
    ResponseRecorder,
    export_results,
    write_summary,
)
from blaze_hammer.output.filters import ResponseFilter, truncate_text
from blaze_hammer.output.json_diff import DiffEntry, diff_tree, render_diff

__all__ = [
    "DiffEntry",
    "ResponseFilter",
    "ResponseRecorder",
    "diff_tree",
    "export_results",
    "make_console",
    "plain_error",
    "render_diff",
    "render_error",
    "truncate_text",
    "write_summary",
]
