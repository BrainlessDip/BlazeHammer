"""Opt-in retry policy evaluation."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import httpx

from blaze_hammer.engine.errors import ErrorCategory, classify_exception

if TYPE_CHECKING:
    from blaze_hammer.config.models import RetryPolicy

MAX_RETRY_AFTER_S = 30.0


def backoff_delay(policy: RetryPolicy, attempt: int) -> float:
    """Exponential backoff with cap; *attempt* is zero-based."""
    return min(policy.backoff_cap, policy.backoff_base * (2**attempt))


def retry_delay(
    policy: RetryPolicy,
    attempt: int,
    exc: BaseException | None,
    response: httpx.Response | None,
) -> float | None:
    """Return seconds to wait before the next attempt, or None to stop.

    Retries connection-family failures and configured HTTP statuses.
    Timeouts honor ``retry_on_timeout``. Template/cancellation errors are
    never retried. Honors numeric ``Retry-After`` (capped) so servers can
    pace us instead of being hammered.
    """
    if attempt >= policy.max_retries:
        return None

    if exc is not None:
        category = classify_exception(exc)
        if category in (ErrorCategory.TEMPLATE, ErrorCategory.CANCELLED):
            return None
        if category is ErrorCategory.TIMEOUT and not policy.retry_on_timeout:
            return None
        if category in (
            ErrorCategory.TIMEOUT,
            ErrorCategory.CONNECTION,
            ErrorCategory.DNS,
            ErrorCategory.TLS,
        ):
            return backoff_delay(policy, attempt)
        return None  # never retry unknown/other failures

    if response is None:
        return None
    status = response.status_code
    # 429 always honors Retry-After: respecting the server's rate limits is
    # preferable to hammering it, even when 429 isn't in retry_statuses.
    if status not in policy.retry_statuses and status != 429:
        return None
    delay = backoff_delay(policy, attempt)
    retry_after = response.headers.get("retry-after")
    if retry_after:
        with contextlib.suppress(ValueError):
            delay = max(delay, min(float(retry_after), MAX_RETRY_AFTER_S))
    return delay
