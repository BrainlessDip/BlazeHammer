"""Web route modules (thin controllers over services/state)."""

from __future__ import annotations

from blaze_hammer.web.routes.auth_routes import router as auth_router
from blaze_hammer.web.routes.config_routes import router as config_router
from blaze_hammer.web.routes.profiles_routes import router as profiles_router
from blaze_hammer.web.routes.runs_routes import router as runs_router

__all__ = ["auth_router", "config_router", "profiles_router", "runs_router"]
