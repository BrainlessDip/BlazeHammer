"""Shared HTTP client factory and capped response reading."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable

    from blaze_hammer.config.models import RunConfig


#: Excerpt substituted for binary payloads (never store raw bytes).
BINARY_PLACEHOLDER = "[binary response omitted]"
#: Excerpt substituted when text decoding fails under the declared charset.
DECODE_FAILED_PLACEHOLDER = "[unable to decode response body]"

#: Response headers persisted by default. Anything not listed is dropped
#: before storage so session/auth secrets can never leak into run history.
DEFAULT_RESPONSE_HEADER_ALLOWLIST = frozenset(
    {
        "content-type",
        "content-length",
        "server",
        "location",
        "date",
        "retry-after",
        "x-request-id",
        "x-trace-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
    }
)

_BINARY_PREFIXES = ("image/", "audio/", "video/")
_BINARY_TYPES = (
    "application/octet-stream",
    "application/zip",
    "application/pdf",
    "application/gzip",
    "application/x-tar",
    "application/vnd.rar",
    "application/wasm",
)


def is_binary_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    lowered = content_type.split(";", 1)[0].strip().lower()
    return lowered.startswith(_BINARY_PREFIXES) or lowered in _BINARY_TYPES


def select_header_allowlist(configured: Iterable[str] | None) -> frozenset[str]:
    """Configured allowlist wins when non-empty; otherwise the safe default."""
    if configured:
        return frozenset(name.lower() for name in configured)
    return DEFAULT_RESPONSE_HEADER_ALLOWLIST


@dataclass(frozen=True)
class BodySnapshot:
    """Response body captured up to a byte cap, plus wire metadata."""

    text: str | None
    truncated: bool
    total_bytes: int | None
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def build_client(cfg: RunConfig) -> httpx.AsyncClient:
    """Create the single AsyncClient used for the whole run.

    Connection pool scales with configured concurrency; HTTP/2 stays on
    (legacy behavior) and requires the ``h2`` dependency pulled in via
    ``httpx[http2]``.
    """
    concurrency = max(cfg.concurrency, 1)
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=concurrency * 2,
            max_keepalive_connections=concurrency,
        ),
        timeout=httpx.Timeout(cfg.timeout),
        http2=True,
    )


async def read_body_snapshot(
    response: httpx.Response,
    max_chars: int | None,
    *,
    header_allowlist: frozenset[str] = DEFAULT_RESPONSE_HEADER_ALLOWLIST,
) -> BodySnapshot:
    """Read the response body, capping memory usage.

    Reads at most ``max_chars`` bytes; larger bodies report
    ``truncated=True`` with the wire size when Content-Length is known.
    Binary content types are never read or decoded — the connection is
    closed immediately and a placeholder excerpt recorded instead.
    """
    content_type = response.headers.get("content-type")
    headers = _allowed_headers(response, header_allowlist)

    declared: int | None = None
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        with contextlib.suppress(ValueError):
            declared = int(raw_length)

    if is_binary_content_type(content_type):
        await response.aclose()
        total = declared if declared is not None else 0
        return BodySnapshot(
            text=BINARY_PLACEHOLDER,
            truncated=False,
            total_bytes=total,
            content_type=content_type,
            headers=headers,
        )

    if max_chars is None:
        data = await response.aread()
        return _text_snapshot(data, response, False, declared, content_type, headers)

    chunks: list[bytes] = []
    received = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        received += len(chunk)
        if received >= max_chars:
            truncated = True
            break
    data = b"".join(chunks)[:max_chars]
    total = declared if declared is not None else received
    return _text_snapshot(data, response, truncated, total, content_type, headers)


def _allowed_headers(response: httpx.Response, allowlist: frozenset[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in response.headers:
        lowered = name.lower()
        if lowered in allowlist and lowered not in out:
            out[lowered] = response.headers[name]
    return out


def decode(data: bytes, encoding: str | None = None) -> str | None:
    """Decode using the declared charset, falling back to UTF-8.

    Never raises: undecodable payloads become ``DECODE_FAILED_PLACEHOLDER``
    rather than crashing a run.
    """
    if not data:
        return None
    for candidate in (encoding, "utf-8"):
        if not candidate:
            continue
        try:
            return data.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return DECODE_FAILED_PLACEHOLDER


def _charset_of(response: httpx.Response) -> str | None:
    """Explicit charset parameter from Content-Type, if any."""
    ctype = response.headers.get("content-type", "")
    for part in ctype.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip('"').strip("'") or None
    return None


def _text_snapshot(
    data: bytes,
    response: httpx.Response,
    truncated: bool,
    total: int | None,
    content_type: str | None,
    headers: dict[str, str],
) -> BodySnapshot:
    charset = _charset_of(response)
    try:
        text = decode(data, charset)
    except Exception:  # pragma: no cover - decode() never raises by contract
        text = DECODE_FAILED_PLACEHOLDER
    return BodySnapshot(
        text=text,
        truncated=truncated,
        total_bytes=total,
        content_type=content_type,
        headers=headers,
    )
