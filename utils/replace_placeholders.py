"""Deprecated shim — the placeholder engine now lives in
``blaze_hammer.templating``. This module keeps the old import path and
function signature working for ad-hoc scripts.
"""

from __future__ import annotations

import random
from typing import Any

from blaze_hammer.templating import (
    FakerFactory,
    ResolveContext,
    TemplateResolver,
    build_default_registry,
)

_default_ctx: ResolveContext | None = None


def _get_context() -> ResolveContext:
    global _default_ctx
    if _default_ctx is None:
        rng = random.Random()
        _default_ctx = ResolveContext(rng=rng, faker=FakerFactory(rng))
    return _default_ctx


def replace_placeholders(obj: Any) -> Any:
    """Resolve {placeholder} tokens in *obj* (legacy API, unseeded)."""
    resolver = TemplateResolver(build_default_registry(), _get_context())
    return resolver.resolve_obj(obj)


__all__ = ["replace_placeholders"]
