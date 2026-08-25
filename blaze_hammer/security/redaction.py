"""Secret redaction applied uniformly to preview, print, export and logs.

Values are redacted by header/field *name* heuristics. Names are matched
case-insensitively by substring so ``X-API-Key`` or ``auth-token`` are
caught without enumerating every variant. Profiles may add names via
``sensitive_keys``; the defaults below are always active.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "***REDACTED***"

DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "api-key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
    }
)


def is_sensitive(name: str, extra: Iterable[str] = ()) -> bool:
    normalized = name.lower()
    return any(pattern in normalized for pattern in (*DEFAULT_SENSITIVE_KEYS, *extra))


def redact_mapping(
    mapping: Mapping[str, Any],
    extra: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a copy of *mapping* with sensitive values replaced.

    Nested dicts and lists of dicts are walked recursively. Values that
    came from ``${VAR}`` expansion should be passed via *extra* key names
    by the caller (see the planner).
    """
    extra_names = tuple(extra)
    result: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, Mapping):
            result[key] = redact_mapping(value, extra_names)
        elif isinstance(value, list):
            result[key] = [
                redact_mapping(item, extra_names) if isinstance(item, Mapping) else item
                for item in value
            ]
        elif is_sensitive(str(key), extra_names):
            result[key] = REDACTED
        else:
            result[key] = value
    return result


def sensitive_leaf_paths(
    mapping: Mapping[str, Any],
    extra: Iterable[str] = (),
    prefix: str = "",
) -> tuple[str, ...]:
    """Dotted paths of leaves whose values were redacted.

    Used by the planner to mark keys whose values originated from
    ``${VAR}`` expansion as sensitive even when their names look benign.
    """
    found: list[str] = []
    extra_names = tuple(extra)
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            found.extend(sensitive_leaf_paths(value, extra_names, path))
        elif is_sensitive(str(key), extra_names):
            found.append(path)
    return tuple(found)
