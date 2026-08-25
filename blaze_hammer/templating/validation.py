"""Pre-flight placeholder validation.

Validates every ``{placeholder}`` and ``${ENV}`` reference found in the
*parsed* template structures (so JSON object braces are never mistaken
for placeholders) and maps issues back to source line numbers where
possible. Faker tokens are probed against a throwaway instance so bad
providers/arguments fail before any network request.
"""

from __future__ import annotations

import difflib
import random
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.security.env import ENV_PATTERN
from blaze_hammer.templating.builtins import LIST_PATTERN
from blaze_hammer.templating.faker_bridge import FakerFactory, resolve_faker_token
from blaze_hammer.templating.registry import (
    PLACEHOLDER_PATTERN,
    ResolveContext,
    parse_placeholder_args,
)

if TYPE_CHECKING:
    from blaze_hammer.templating.registry import PlaceholderRegistry


@dataclass(frozen=True)
class PlaceholderIssue:
    label: str
    line: int | None
    token: str
    problem: str
    suggestions: tuple[str, ...] = ()

    @property
    def location(self) -> str:
        if self.line is not None:
            return f"{self.label}:{self.line}"
        return self.label

    def render(self) -> str:
        parts = [f"[x] {self.location}", f"  {self.token}"]
        parts.append(f"  {self.problem}")
        for suggestion in self.suggestions:
            parts.append(f"  Did you mean: {suggestion}?")
        return "\n".join(parts)


@dataclass
class ValidationReport:
    issues: list[PlaceholderIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def render(self) -> str:
        if self.ok:
            return "Placeholders valid"
        return "\n\n".join(issue.render() for issue in self.issues)


def _walk_strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)
    elif isinstance(node, str):
        yield node


def _find_line(raw_text: str, needle: str) -> int | None:
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if needle in line:
            return line_number
    return None


def _extract_list_expressions(text: str) -> list[str]:
    """Extract balanced ``list(...)`` spans, even when braces sit inside."""
    found: list[str] = []
    for marker in re.finditer(r"list\(", text):
        depth = 0
        for index in range(marker.start(), len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    found.append(text[marker.start() : index + 1])
                    break
    return found


class TemplateValidator:
    """Validates parsed templates plus their raw text for line numbers."""

    def __init__(self, registry: PlaceholderRegistry, faker_locale: str | None = None) -> None:
        self._registry = registry
        probe_rng = random.Random(0)  # probe only validates; values are discarded
        self._probe_ctx = ResolveContext(
            rng=probe_rng,
            faker=FakerFactory(probe_rng, default_locale=faker_locale),
        )
        self._pattern = re.compile(PLACEHOLDER_PATTERN)
        self._keywords = registry.keywords()
        factory = self._probe_ctx.faker
        flat = [f"faker.{name}" for name in factory.attribute_candidates()]
        dotted = [f"faker.{path}" for path in factory.provider_path_candidates()]
        self._faker_candidates = [*dotted, *flat]

    def validate(self, templates: Mapping[str, tuple[Any, str]]) -> ValidationReport:
        """templates: label -> (parsed_object_or_None, raw_file_text)."""
        report = ValidationReport()
        for label, (obj, raw_text) in templates.items():
            report.issues.extend(self._scan(label, obj, raw_text))
        return report

    def _scan(self, label: str, obj: Any, raw_text: str) -> list[PlaceholderIssue]:
        issues: list[PlaceholderIssue] = []
        seen: set[str] = set()
        for text in _walk_strings(obj or {}):
            for match in self._pattern.finditer(text):
                content = match.group(1).strip()
                if content in seen:
                    continue
                seen.add(content)
                issue = self._check_token(
                    label,
                    _find_line(raw_text, content),
                    f"{{{content}}}",
                    content,
                    raw_env=text,
                )
                if issue is not None:
                    issues.append(issue)
            # The brace-excluding pattern cannot see a {list(...)} token whose
            # item expression itself contains braces (e.g. {list(item={uuid},
            # length=2)}); catch those constructs explicitly so the runtime
            # brace rejection also surfaces during pre-flight validation.
            for list_expr in _extract_list_expressions(text):
                if list_expr in seen:
                    continue
                seen.add(list_expr)
                spec_hit = self._registry.match(list_expr)
                if spec_hit is None:
                    continue
                spec, _handler = spec_hit
                args = parse_placeholder_args(list_expr)
                problem = spec.validator(list_expr, args) if spec.validator else None
                if problem is not None:
                    issues.append(
                        PlaceholderIssue(
                            label=label,
                            line=_find_line(raw_text, "list("),
                            token=f"{{{list_expr}}}",
                            problem=problem,
                        )
                    )
        return issues

    def _check_token(
        self,
        label: str,
        line_number: int | None,
        display_token: str,
        content: str,
        *,
        raw_env: str,
    ) -> PlaceholderIssue | None:
        def issue(problem: str, suggestions: tuple[str, ...] = ()) -> PlaceholderIssue:
            return PlaceholderIssue(
                label=label,
                line=line_number,
                token=display_token,
                problem=problem,
                suggestions=suggestions,
            )

        env_names = ENV_PATTERN.findall(raw_env)
        if env_names:
            missing = [name for name in env_names if name not in self._environ()]
            if missing:
                return issue(f"Environment variable(s) not set: {', '.join(missing)}")

        hit = self._registry.match(content)
        if hit is not None:
            spec, _handler = hit
            args = parse_placeholder_args(content)
            problem = spec.validator(content, args) if spec.validator else None
            if problem is not None:
                return issue(problem)
            return self._deep_probe(spec.keyword, content, issue)

        if content.startswith("faker."):
            try:
                resolve_faker_token(content, self._probe_ctx)
            except TemplateResolutionError as exc:
                suggestions = exc.suggestions or _suggest(
                    content.replace("faker.", "", 1), self._faker_candidates
                )
                return issue(exc.message, suggestions)
            return None

        candidates = (*self._keywords,)
        return issue("Unknown placeholder", _suggest(content.split("(")[0], candidates))

    def _environ(self) -> Mapping[str, str]:
        # Presence matters here, not values; real expansion uses os.environ
        # through the resolver at generation time.
        import os

        return os.environ

    def _deep_probe(
        self,
        keyword: str,
        content: str,
        issue: Callable[..., PlaceholderIssue | None],
    ) -> PlaceholderIssue | None:
        """Execute nested expressions once to surface hidden argument errors.

        Currently used by ``{list(item=..., length=N)}``: the item expression
        is resolved a single time against the throwaway probe context so a
        bad nested placeholder (e.g. ``item=hex(length=abc)``) fails during
        validation rather than mid-run.
        """
        if keyword != "list":
            return None
        from blaze_hammer.templating.resolver import TemplateResolver

        match = LIST_PATTERN.match(content.strip())
        if match is None:  # already reported by the spec validator
            return None
        item_expr = match.group("item").strip()
        resolver = TemplateResolver(self._registry, self._probe_ctx)
        try:
            resolver.resolve_string("{" + item_expr + "}")
        except TemplateResolutionError as exc:
            return issue(exc.message)
        except Exception as exc:  # noqa: BLE001 - probe must never crash CLI
            return issue(str(exc))
        return None


def _suggest(value: str, candidates: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(difflib.get_close_matches(value, list(candidates), n=3, cutoff=0.5))
