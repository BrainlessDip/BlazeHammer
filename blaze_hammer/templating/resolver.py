"""Template resolution: walks payload/header structures substituting tokens.

Type preservation rule (see README):

- A placeholder occupying the **entire** string value resolves through its
  handler directly; specs flagged ``native=True`` return real JSON types
  (bool/int/float/None/list).
- Embedded placeholders are coerced to text: ``true``/``false``/``null``
  JSON-style, lists as compact JSON, numbers via ``str()``.
- Legacy placeholders keep returning strings in both positions.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.security.env import ENV_PATTERN, expand_env_vars
from blaze_hammer.templating.faker_bridge import resolve_faker_token
from blaze_hammer.templating.registry import (
    PLACEHOLDER_PATTERN,
    ResolveContext,
    parse_placeholder_args,
)

#: Maximum recursive substitution passes over one string (legacy looped until
#: stable; the cap only guards against pathological self-referencing tokens).
MAX_PASSES = 10

#: Legacy recursion cap for nested structures.
MAX_DEPTH = 10


def embed_as_text(value: Any) -> str:
    """Coerce a native placeholder result for embedded (string) usage."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return str(value)
    return str(value)


class TemplateResolver:
    """Resolves ``{placeholder}`` and ``${ENV_VAR}`` references in templates.

    Unknown placeholder tokens are left verbatim (legacy behavior), but
    pre-flight validation rejects configs containing them before a run.
    """

    def __init__(
        self,
        registry: Any,
        ctx: ResolveContext,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._environ = os.environ if environ is None else environ
        self._pattern = re.compile(PLACEHOLDER_PATTERN)
        ctx.resolver = self  # enables {list(item=..., length=...)} nesting

    def resolve_obj(self, obj: Any, _depth: int = 0) -> Any:
        if _depth > MAX_DEPTH:
            return obj
        if isinstance(obj, dict):
            return {key: self.resolve_obj(val, _depth + 1) for key, val in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_obj(item, _depth + 1) for item in obj]
        if isinstance(obj, str):
            return self.resolve_string(obj)
        return obj

    def resolve_string(self, text: str) -> Any:
        expansion = expand_env_vars(text, self._environ)
        resolved = expansion.value
        if "{" not in resolved or "}" not in resolved:
            return resolved

        # Full-value placeholders may keep their native JSON type.
        full_match = self._pattern.fullmatch(resolved)
        if full_match is not None:
            native = self._resolve_native(full_match.group(1).strip())
            if native is not _NOT_NATIVE:
                return native

        for _ in range(MAX_PASSES):
            new_text = self._pattern.sub(self._substitute, resolved)
            if new_text == resolved:
                break
            resolved = new_text
        return resolved

    def _resolve_native(self, content: str) -> Any:
        """Return the native value for a full-value token, or the sentinel."""
        hit = self._registry.match(content)
        if hit is not None and hit[0].native:
            _spec, handler = hit
            try:
                return handler(content, parse_placeholder_args(content), self._ctx)
            except TemplateResolutionError:
                raise
            except Exception as exc:
                raise TemplateResolutionError(
                    f"Placeholder '{{{content}}}' failed: {exc}", token=content
                ) from exc
        # Faker tokens: resolve and return raw result (preserving native type).
        if content.startswith("faker."):
            try:
                return resolve_faker_token(content, self._ctx)
            except TemplateResolutionError as exc:
                exc.token = exc.token or content
                raise
        return _NOT_NATIVE

    def _substitute(self, match: re.Match[str]) -> str:
        content = match.group(1).strip()
        hit = self._registry.match(content)
        if hit is not None:
            _spec, handler = hit
            try:
                value = handler(content, parse_placeholder_args(content), self._ctx)
            except TemplateResolutionError:
                raise
            except Exception as exc:
                raise TemplateResolutionError(
                    f"Placeholder '{{{content}}}' failed: {exc}", token=content
                ) from exc
            return embed_as_text(value)
        if content.startswith("faker."):
            try:
                value = resolve_faker_token(content, self._ctx)
            except TemplateResolutionError as exc:
                exc.token = exc.token or content
                raise
            return embed_as_text(value)
        # Unknown token: legacy behavior keeps it verbatim.
        return match.group(0)


class _NotNative:
    """Sentinel distinguishing 'no native value' from a resolved None."""


_NOT_NATIVE = _NotNative()


def iter_env_references(obj: Any) -> list[str]:
    """Collect every ``${VAR}`` name referenced anywhere in a structure."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            found.extend(ENV_PATTERN.findall(node))

    walk(obj)
    return list(dict.fromkeys(found))
