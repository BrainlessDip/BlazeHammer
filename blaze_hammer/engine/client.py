"""Shared HTTP client factory and capped response reading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from blaze_hammer.config.models import RunConfig


@dataclass(frozen=True)
class BodySnapshot:
    """Response body captured up to a character cap."""

    text: str | None
    truncated: bool
    total_bytes: int | None


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


async def read_body_snapshot(response: httpx.Response, max_chars: int | None) -> BodySnapshot:
    """Read the response body, capping memory/terminal usage.

    Only called when output options actually need bodies (printing,
    saving). Reads at most ``max_chars`` bytes; larger bodies report
    ``truncated=True`` with the wire size when Content-Length is known.
    """
    declared: int | None = None
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except ValueError:
            declared = None

    if max_chars is None:
        data = await response.aread()
        return BodySnapshot(decode(data), False, declared or len(data))

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
    return BodySnapshot(decode(data), truncated, total)


def decode(data: bytes) -> str | None:
    if not data:
        return None
    charset = "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError:  # pragma: no cover - utf-8 always exists
        return data.decode("ascii", errors="replace")
