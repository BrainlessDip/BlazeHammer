"""Response filtering for printed output (statistics stay complete)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blaze_hammer.config.models import OutputOptions
    from blaze_hammer.engine.runner import RequestOutcome


@dataclass(frozen=True)
class ResponseFilter:
    """Decides which outcomes get *printed*; never affects statistics."""

    statuses: frozenset[int] | None = None
    success_only: bool = False
    failed_only: bool = False

    @classmethod
    def from_output(cls, options: OutputOptions) -> ResponseFilter:
        return cls(
            statuses=options.status_filter,
            success_only=options.success_only,
            failed_only=options.failed_only,
        )

    def matches(self, outcome: RequestOutcome) -> bool:
        if self.statuses is not None:
            code = outcome.status_code
            if code is None or code not in self.statuses:
                return False
        if self.success_only and not outcome.ok:
            return False
        return not (self.failed_only and outcome.ok)


def truncate_text(text: str | None, max_chars: int) -> tuple[str | None, int]:
    """Return (visible_text, omitted_char_count)."""
    if text is None or len(text) <= max_chars:
        return text, 0
    return text[:max_chars], len(text) - max_chars
