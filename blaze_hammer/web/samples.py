"""Structured request/response sample collection for representative runs.

A :class:`SampleStore` is attached to each :class:`RunHandle` and receives
every completed :class:`RequestOutcome`.  It keeps a bounded set of
**representative** samples:

- first request (always)
- first successful request (if any)
- first failed request (if any)
- one sample per distinct HTTP status code (up to ``max_per_run``)

Each stored sample carries the full request/response structure the React
frontend needs for expandable detail panels.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from blaze_hammer.security.redaction import redact_mapping

if TYPE_CHECKING:
    from blaze_hammer.config.models import SampleOptions
    from blaze_hammer.engine.runner import RequestOutcome

#: Maximum characters kept from a single body string before it is truncated.
_CHAR_SLICE = 65536


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Return (text, truncated) after applying a byte-count cap."""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text, False
    # Walk backwards to avoid splitting a multi-byte character.
    cut = limit
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8", errors="replace"), True


def _body_excerpt(
    body_text: str | None,
    *,
    max_size: int,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Build the ``body`` portion of a response sample."""
    if body_text is None:
        return {}
    is_binary = body_text.startswith("[binary") or body_text.startswith("[unable")
    if is_binary:
        return {
            "text": body_text,
            "truncated": False,
            "content_type": content_type,
        }
    text, truncated = _truncate_text(body_text, max_size)
    parsed: Any = None
    if content_type and "json" in content_type.lower():
        with contextlib.suppress(ValueError, TypeError):
            parsed = json.loads(text)
    return {
        "text": text if parsed is None else None,
        "parsed": parsed,
        "truncated": truncated,
        "content_type": content_type,
    }


class SampleStore:
    """Collects bounded representative request/response samples per run."""

    def __init__(self, options: SampleOptions, sensitive_names: tuple[str, ...] = ()) -> None:
        self._max = options.max_per_run
        self._req_cap = options.max_request_body_size
        self._resp_cap = options.max_response_body_size
        self._sensitive = sensitive_names
        self._samples: list[dict[str, Any]] = []
        self._seen_status: set[int] = set()
        self._first_done = False
        self._first_ok_done = False
        self._first_err_done = False

    @property
    def samples(self) -> list[dict[str, Any]]:
        return list(self._samples)

    def _budget(self) -> bool:
        return len(self._samples) < self._max

    def record(self, outcome: RequestOutcome) -> None:
        """Evaluate whether *outcome* qualifies as a representative sample."""
        if not self._budget():
            return

        classify = self._classify(outcome)
        if classify is None:
            return

        sample = self._build_sample(outcome, classify)
        self._samples.append(sample)
        if outcome.status_code is not None:
            self._seen_status.add(outcome.status_code)

    # -- classification ------------------------------------------------------

    def _classify(self, o: RequestOutcome) -> str | None:
        """Return a label if this outcome is representative, else ``None``."""
        if not self._first_done:
            self._first_done = True
            return "first"
        if o.ok and not self._first_ok_done:
            self._first_ok_done = True
            return "first_success"
        if not o.ok and not self._first_err_done:
            self._first_err_done = True
            return "first_failure"
        if o.status_code is not None and o.status_code not in self._seen_status:
            return f"status_{o.status_code}"
        return None

    # -- sample builder ------------------------------------------------------

    def _build_sample(self, o: RequestOutcome, reason: str) -> dict[str, Any]:
        ts = time.time()

        # -- request side --
        req_headers = redact_mapping(o.resolved_headers or {}, self._sensitive)
        req_cookies = redact_mapping(o.resolved_cookies or {}, self._sensitive)
        req_body: dict[str, Any] | None = None
        if o.resolved_payload is not None:
            if isinstance(o.resolved_payload, dict):
                redacted_payload = redact_mapping(o.resolved_payload, self._sensitive)
                text = json.dumps(redacted_payload, ensure_ascii=False, default=str)
            else:
                text = str(o.resolved_payload)
            text, truncated = _truncate_text(text, self._req_cap)
            parsed: Any = None
            with contextlib.suppress(ValueError, TypeError):
                parsed = json.loads(text)
            req_body = {
                "text": text if parsed is None else None,
                "parsed": parsed,
                "truncated": truncated,
            }

        # -- response side --
        resp_snap = o.body
        resp_headers: dict[str, str] = {}
        resp_body: dict[str, Any] | None = None
        resp_content_type: str | None = None
        resp_size = 0
        resp_truncated = False
        if resp_snap is not None:
            resp_headers = dict(resp_snap.headers)
            resp_content_type = resp_snap.content_type
            resp_size = resp_snap.total_bytes or 0
            resp_truncated = resp_snap.truncated
            resp_body = _body_excerpt(
                resp_snap.text,
                max_size=self._resp_cap,
                content_type=resp_snap.content_type,
            )

        return {
            "id": uuid.uuid4().hex[:12],
            "timestamp": round(ts, 3),
            "reason": reason,
            "duration_ms": round(o.latency_s * 1000, 1),
            "ok": o.ok,
            "attempts": o.attempts,
            "error": o.error_message,
            "error_category": o.error_category,
            "request": {
                "method": o.method,
                "url": o.url,
                "headers": req_headers,
                "body": req_body,
                "cookies": req_cookies,
            },
            "response": {
                "status_code": o.status_code,
                "headers": resp_headers,
                "body": resp_body,
                "content_type": resp_content_type,
                "size": resp_size,
                "truncated": resp_truncated,
            },
        }
