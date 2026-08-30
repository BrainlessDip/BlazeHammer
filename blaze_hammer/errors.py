"""Exception hierarchy for expected, user-actionable failures.

Every error carries enough structured context for ``output.console``
to render an actionable message without a stack trace. Unexpected
exceptions are not wrapped: they propagate and are shown in full when
``--debug`` is set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class BlazeHammerError(Exception):
    """Base class for all expected Blaze Hammer failures."""

    exit_code: int = EXIT_ERROR

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        expected: str | None = None,
        found: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.expected = expected
        self.found = found
        self.hint = hint


class ConfigurationError(BlazeHammerError):
    """Invalid or inconsistent configuration (CLI, profile, env, files)."""


class ProfileError(ConfigurationError):
    """A test profile could not be found or parsed."""


class PlaceholderError(ConfigurationError):
    """A placeholder failed pre-flight validation."""

    def __init__(
        self,
        message: str,
        *,
        file: str | None = None,
        line: int | None = None,
        token: str | None = None,
        suggestions: Sequence[str] = (),
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.file = file
        self.line = line
        self.token = token
        self.suggestions = tuple(suggestions)


class MissingEnvVarError(ConfigurationError):
    """An ${VAR} reference points at an environment variable that is unset."""

    def __init__(self, variables: Iterable[str]) -> None:
        names = ", ".join(sorted(set(variables)))
        super().__init__(
            "Unresolved environment variable reference",
            reason=f"no value set for: {names}",
            hint="export the variable before running, or replace the reference",
        )
        self.variables = tuple(sorted(set(variables)))


class TemplateResolutionError(BlazeHammerError):
    """A placeholder failed while generating a specific request.

    Raised at generation time; the runner converts this into a failed
    request outcome instead of aborting the whole run. Pre-flight
    validation (``templating.validation``) catches almost all of these
    before any traffic is sent.
    """

    def __init__(
        self,
        message: str,
        *,
        token: str | None = None,
        suggestions: Sequence[str] = (),
        **kwargs: object,
    ) -> None:
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.token = token
        self.suggestions = tuple(suggestions)


class ExportError(BlazeHammerError):
    """Results could not be written to disk."""
