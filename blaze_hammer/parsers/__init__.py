"""Per-status-code output formatting (adapters over blaze_hammer.ext.parsers).

A failing user parser is reported inline but never crashes the run.
"""

from __future__ import annotations

from typing import Any

from blaze_hammer.ext import parsers as _user


def _dispatch(table: dict[Any, Any], status_code: int | None, value: Any) -> str | None:
    parser = None
    if status_code is not None:
        parser = table.get(status_code)
    if parser is None:
        parser = table.get("all")
    if parser is None:
        return None
    try:
        result = parser(value)
    except Exception as exc:  # noqa: BLE001 - user code must not kill the run
        return f"[parser error: {exc}]"
    return str(result)


def render_response(status_code: int | None, body_text: str | None) -> str | None:
    return _dispatch(_user.custom_response_parsers, status_code, body_text)


def render_payload(status_code: int | None, payload: dict | None) -> str | None:
    return _dispatch(_user.custom_payload_parsers, status_code, payload)


def render_headers(status_code: int | None, headers: dict | None) -> str | None:
    return _dispatch(_user.custom_headers_parsers, status_code, headers)
