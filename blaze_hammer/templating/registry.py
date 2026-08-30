"""Placeholder registry and dispatch.

Preserves the legacy dispatch semantics of ``utils/replace_placeholders.py``:

1. exact-match tokens (``{uuid}``, ``{ip}``, ...)
2. ordered prefix matching (``{email(...)}`` starts-with ``email``), in the
   exact registration order the old ``startswith`` chain used
3. ``faker.*`` tokens handled by :mod:`blaze_hammer.templating.faker_bridge`
4. anything else is left untouched at runtime — but pre-flight validation
   refuses to run a config containing unknown tokens.

Type preservation: handlers registered with ``native=True`` may return real
JSON types (bool/int/float/None/list). When such a placeholder occupies the
*entire* template value the resolver returns that native type; embedded in a
larger string it is coerced to text (JSON style for bool/null). Legacy
placeholders keep returning strings exactly as before.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from blaze_hammer.templating.faker_bridge import FakerFactory

#: Matches the inner content of ``{placeholder}`` tokens (legacy pattern).
PLACEHOLDER_PATTERN = r"\{([^{}]+)\}"

ArgsDict = dict[str, str]


@dataclass(frozen=True)
class PlaceholderSpec:
    """Metadata for one placeholder: dispatch, validation and documentation.

    ``validator(content, args) -> str | None`` performs pre-flight argument
    checking (returning an error string) so bad arguments fail before any
    traffic is sent instead of falling back to silent defaults.
    """

    keyword: str
    exact: bool = False
    syntax: str = ""
    description: str = ""
    example: str = ""
    arguments: str = ""
    default: str = ""
    returns: str = "string"
    native: bool = False
    validator: Callable[[str, ArgsDict], str | None] | None = None


@dataclass
class ResolveContext:
    """Shared per-run state handed to every placeholder handler."""

    rng: random.Random
    faker: FakerFactory
    now: Callable[[], datetime] = datetime.now
    file_cache: dict[str, list[str]] = field(default_factory=dict)
    #: True when the run was started with an explicit --seed; crypto-flavored
    #: placeholders (token) become deterministic in that mode.
    seeded: bool = False
    #: Back-reference set by TemplateResolver (used by {list(...)} nesting).
    resolver: Any = None
    list_depth: int = 0


#: Handlers may return native JSON values; embedded usage coerces to text.
Handler = Callable[[str, ArgsDict, ResolveContext], Any]

ArgValidator = Callable[[str, ArgsDict], str | None]


class PlaceholderRegistry:
    """Registry of placeholder handlers."""

    def __init__(self) -> None:
        self._exact: dict[str, tuple[PlaceholderSpec, Handler]] = {}
        self._prefix: list[tuple[PlaceholderSpec, Handler]] = []

    def register(self, spec: PlaceholderSpec, handler: Handler) -> None:
        if spec.exact:
            self._exact[spec.keyword] = (spec, handler)
        else:
            self._prefix.append((spec, handler))

    def match(self, content: str) -> tuple[PlaceholderSpec, Handler] | None:
        hit = self._exact.get(content)
        if hit is not None:
            return hit
        for spec, handler in self._prefix:
            if content.startswith(spec.keyword):
                return spec, handler
        return None

    def exists(self, name: str) -> bool:
        return self.match(name) is not None

    def specs(self) -> list[PlaceholderSpec]:
        """All registered placeholders in dispatch order."""
        prefix_specs = [spec for spec, _ in self._prefix]
        exact_specs = [spec for spec, _ in self._exact.values()]
        return [*exact_specs, *prefix_specs]

    def keywords(self) -> tuple[str, ...]:
        exact = tuple(self._exact)
        prefix = tuple(spec.keyword for spec, _ in self._prefix)
        return (*exact, *prefix)


def build_default_registry() -> PlaceholderRegistry:
    """Create a registry populated with all built-in placeholders."""
    from blaze_hammer.templating.builtins import register_builtins

    registry = PlaceholderRegistry()
    register_builtins(registry)
    return registry


def parse_placeholder_args(content: str) -> ArgsDict:
    """Parse ``key=value`` pairs from token content (legacy regex)."""
    import re

    return dict(re.findall(r"(\w+)=([^,{}()]+)", content))
