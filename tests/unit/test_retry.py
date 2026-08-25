"""Retry policy decisions."""

from __future__ import annotations

import httpx
import pytest

from blaze_hammer.config.models import RetryPolicy
from blaze_hammer.engine.errors import ErrorCategory, classify_exception
from blaze_hammer.engine.retry import backoff_delay, retry_delay


def _response(status: int, headers=None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("GET", "https://t.test")
    )


def test_disabled_by_default():
    policy = RetryPolicy()
    assert retry_delay(policy, 0, None, _response(503)) is None


def test_retryable_status_and_backoff_growth():
    policy = RetryPolicy(max_retries=3, backoff_base=0.5, backoff_cap=8)
    assert retry_delay(policy, 0, None, _response(503)) == pytest.approx(0.5)
    assert retry_delay(policy, 1, None, _response(500)) is None  # 500 not in defaults
    assert retry_delay(policy, 2, None, _response(502)) == pytest.approx(2.0)
    assert retry_delay(policy, 3, None, _response(502)) is None  # exhausted


def test_backoff_respects_cap():
    policy = RetryPolicy(max_retries=9, backoff_base=4, backoff_cap=6)
    assert backoff_delay(policy, 5) == 6


def test_connection_error_retried_timeout_not_when_disabled():
    policy = RetryPolicy(max_retries=2, retry_on_timeout=False)
    connect = classify_exception(httpx.ConnectError("boom"))
    assert connect is ErrorCategory.CONNECTION
    delay = retry_delay(policy, 0, httpx.ConnectError("refused"), None)
    assert delay == pytest.approx(policy.backoff_base)

    timeout_exc = httpx.ReadTimeout("slow")
    assert retry_delay(policy, 0, timeout_exc, None) is None
    policy_on = RetryPolicy(max_retries=2, retry_on_timeout=True)
    assert retry_delay(policy_on, 0, timeout_exc, None) is not None


def test_retry_after_header_honored_and_capped():
    policy = RetryPolicy(max_retries=2, backoff_base=0.1)
    resp = _response(429, {"Retry-After": "2"})
    assert retry_delay(policy, 0, None, resp) >= 2.0
    resp_big = _response(429, {"Retry-After": "999"})
    assert retry_delay(policy, 0, None, resp_big) <= 30.0


def test_template_errors_never_retried():
    from blaze_hammer.errors import TemplateResolutionError

    policy = RetryPolicy(max_retries=3)
    assert retry_delay(policy, 0, TemplateResolutionError("bad"), None) is None
