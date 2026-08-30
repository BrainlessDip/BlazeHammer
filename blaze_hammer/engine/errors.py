"""Failure taxonomy for request outcomes."""

from __future__ import annotations

import asyncio
import enum
import ssl
from typing import Any

import httpx

from blaze_hammer.errors import TemplateResolutionError


class ErrorCategory(enum.StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    DNS = "dns"
    TLS = "tls"
    TEMPLATE = "template"
    CANCELLED = "cancelled"
    OTHER = "other"


_TLS_MARKERS = ("ssl", "certificate", "cert_", "handshake", "alert", "tls")
_DNS_MARKERS = (
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname",
    "no address associated",
    "temporary failure in name resolution",
    "domain name system",
)


def classify_exception(exc: BaseException) -> ErrorCategory:
    """Best-effort classification of an exception into an error category."""
    if isinstance(exc, TemplateResolutionError):
        return ErrorCategory.TEMPLATE
    if isinstance(exc, asyncio.CancelledError):
        return ErrorCategory.CANCELLED
    if isinstance(
        exc,
        (
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            asyncio.TimeoutError,
            TimeoutError,
        ),
    ):
        return ErrorCategory.TIMEOUT
    message = str(exc).lower()
    if isinstance(exc, ssl.SSLError) or any(m in message for m in _TLS_MARKERS):
        return ErrorCategory.TLS
    if isinstance(exc, httpx.ConnectError):
        if any(m in message for m in _DNS_MARKERS):
            return ErrorCategory.DNS
        return ErrorCategory.CONNECTION
    if isinstance(exc, httpx.HTTPError):
        return ErrorCategory.CONNECTION
    return ErrorCategory.OTHER


def describe_exception(exc: BaseException) -> str:
    """Compact human-readable reason for reports/logs (no secrets inside)."""
    category = classify_exception(exc)
    detail = str(exc).strip().splitlines()[0][:200] if str(exc) else exc.__class__.__name__
    return f"{category.value}: {detail}"


def http_status_is_error(status_code: int) -> bool:
    return status_code >= 400


def unused(_: Any) -> None:  # pragma: no cover - placeholder for future hooks
    return None
