"""Web GUI package: FastAPI transport over the shared Blaze Hammer core."""

from __future__ import annotations

from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings

__all__ = ["create_app", "resolve_web_settings"]
