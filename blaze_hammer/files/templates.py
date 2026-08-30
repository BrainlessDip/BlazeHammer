"""Payload/header template persistence: revisions, JSON checks, atomic saves.

Shared by the Web API today; shaped so the CLI can adopt the same helpers.
Everything here is side-effect-explicit: ``validate_json`` never touches
disk, ``atomic_write_text`` never partially replaces a file.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any


class TemplateValidationError(Exception):
    """Structured, user-facing template problem (maps into the API envelope)."""

    def __init__(
        self,
        *,
        code: str,
        file: str,
        message: str,
        line: int | None = None,
        column: int | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.file = file
        self.message = message
        self.line = line
        self.column = column
        self.status_code = status_code

    def detail(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "file": self.file,
            "message": self.message,
        }
        if self.line is not None:
            out["line"] = self.line
        if self.column is not None:
            out["column"] = self.column
        return out


def compute_revision(text: str) -> str:
    """Deterministic content revision (SHA-256 of the exact bytes on disk)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_newline(text: str) -> str:
    """Minimal normalization: guarantee a single trailing newline."""
    stripped = text.rstrip("\n")
    return f"{stripped}\n" if stripped else ""


def read_template(path: Path | None) -> tuple[str | None, str | None]:
    """Return ``(text, revision)``; ``(None, None)`` when absent/unconfigured."""
    if path is None or not path.is_file():
        return None, None
    raw = path.read_text(encoding="utf-8")
    return raw, compute_revision(raw)


def validate_json(text: str, label: str) -> Any:
    """Parse *text* as JSON, raising a structured 422-style error otherwise.

    Only JSON syntax matters here — Blaze Hammer placeholders are plain
    string values and are validated elsewhere by the templating engine.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise TemplateValidationError(
            code="INVALID_JSON",
            file=label,
            message=f"Invalid JSON: {exc.msg}",
            line=exc.lineno,
            column=exc.colno,
        ) from exc


def atomic_write_text(path: Path, text: str) -> str:
    """Write *text* atomically (tmp → flush/fsync → rename) and return its revision.

    A crash at any point leaves the previous file intact; ``os.replace`` is
    the only step that touches the destination.
    """
    data = normalize_newline(text)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        # Best effort cleanup of the temp file; original remains untouched.
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise
    return compute_revision(data)
