"""Placeholder templating engine."""

from blaze_hammer.templating.faker_bridge import FakerFactory, resolve_faker_token
from blaze_hammer.templating.registry import (
    PlaceholderRegistry,
    PlaceholderSpec,
    ResolveContext,
    build_default_registry,
)
from blaze_hammer.templating.resolver import TemplateResolver, iter_env_references
from blaze_hammer.templating.validation import (
    PlaceholderIssue,
    TemplateValidator,
    ValidationReport,
)

__all__ = [
    "FakerFactory",
    "PlaceholderIssue",
    "PlaceholderRegistry",
    "PlaceholderSpec",
    "ResolveContext",
    "TemplateResolver",
    "TemplateValidator",
    "ValidationReport",
    "build_default_registry",
    "iter_env_references",
    "resolve_faker_token",
]
