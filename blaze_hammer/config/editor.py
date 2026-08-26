"""Round-trip YAML config editing: PATCH semantics without destroying the file.

``update_fields`` modifies only explicitly supplied fields while preserving
comments, key ordering, quoting, blank lines and unknown/custom keys via
ruamel.yaml's round-trip loader. Writes go through
:func:`blaze_hammer.files.templates.atomic_write_text` (tmp → fsync → rename).

This is a *file-level* service; validation of the resulting configuration
happens in the API layer using the normal ``RunConfig`` pipeline.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from blaze_hammer.files.templates import atomic_write_text, compute_revision

_MISSING = object()


def _make_yaml() -> YAML:
    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True  # keep 'quoted' values quoted
    yaml_rt.width = 4096  # never fold long target URLs
    return yaml_rt


def read_config(path: Path) -> tuple[str, str]:
    """Return ``(raw_text, revision)`` for the current file."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return text, compute_revision(text)


def _as_mapping(loaded: Any) -> CommentedMap:
    if loaded is None:
        return CommentedMap()
    if isinstance(loaded, CommentedMap):
        return loaded
    raise ValueError("top level of blazehammer.yaml must be a mapping")


def _walk(node: Any, updates: dict[str, Any], prefix: str) -> list[tuple[str, Any, Any]]:
    """Depth-first merge; returns ``(dotted_name, old, new)`` for real changes."""
    changed: list[tuple[str, Any, Any]] = []
    for key, new_value in updates.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        old_value = node.get(key, _MISSING)
        if isinstance(new_value, dict):
            child = node.get(key)
            if not isinstance(child, dict):
                # Promote scalars/absent keys to mappings to host children.
                child = CommentedMap()
                node[key] = child
            changed.extend(_walk(child, new_value, dotted))
        else:
            if old_value is _MISSING or old_value != new_value:
                node[key] = new_value
                changed.append((dotted, old_value, new_value))
    return changed


def plan_update(path: Path, updates: dict[str, Any]) -> tuple[str, list[str], str]:
    """Apply *updates* to an in-memory round-trip copy.

    Returns ``(new_text, changed_names, current_revision)`` where
    ``changed_names`` are dotted field paths that actually differ. The caller
    decides whether to persist; nothing here touches disk.
    """
    text, revision = read_config(path)
    yaml_rt = _make_yaml()
    root = _as_mapping(yaml_rt.load(text) if text.strip() else None)

    changed = _walk(root, updates, "")
    if not changed:
        return text, [], revision

    buffer = io.StringIO()
    yaml_rt.dump(root, buffer)
    return buffer.getvalue(), [name for name, _old, _new in changed], revision


def update_fields(path: Path, updates: dict[str, Any]) -> tuple[list[str], str]:
    """Persist *updates* atomically; returns ``(changed_names, new_revision)``.

    A no-op update (empty or identical values) leaves the file untouched and
    reports the existing revision.
    """
    new_text, changed, _current = plan_update(path, updates)
    if not changed:
        _text, current = read_config(path)
        return [], current
    new_revision = atomic_write_text(path, new_text)
    return changed, new_revision


__all__ = [
    "plan_update",
    "read_config",
    "update_fields",
]
