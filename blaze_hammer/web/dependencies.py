"""FastAPI dependency wiring: app state, sessions, CSRF guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, WebSocket, status

from blaze_hammer.web.auth import SESSION_COOKIE, LoginLimiter, SessionStore

if TYPE_CHECKING:
    from pathlib import Path

    from blaze_hammer.web.config import ResolvedWebSettings
    from blaze_hammer.web.runs import RunManager


@dataclass
class WebState:
    """Everything the routes need; attached to ``app.state``."""

    settings: ResolvedWebSettings
    project_dir: Path
    #: Explicit --config path or the discovered blazehammer.yaml (or None).
    project_file: Path | None
    sessions: SessionStore = field(default_factory=SessionStore)
    limiter: LoginLimiter = field(default_factory=LoginLimiter)
    manager: RunManager | None = None
    #: Event fan-out bus (web.ws.Hub); typed loosely to avoid a cycle.
    hub: Any = None

    def base_overrides(self) -> dict[str, Any]:
        """Overrides that pin template resolution to this project."""
        return {}

    def config_path(self) -> str | None:
        return str(self.project_file) if self.project_file is not None else None


def get_state(request: Request) -> WebState:
    return request.app.state.web


def get_ws_state(ws: WebSocket) -> WebState:
    return ws.app.state.web


def require_session(request: Request) -> Any:
    state = get_state(request)
    if not state.settings.auth.enabled:
        return None  # auth disabled: endpoints are open by explicit choice
    session = session_from_request(request, state)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Session"},
        )
    return session


def session_from_request(request: Request, state: WebState) -> Any:
    token = request.cookies.get(SESSION_COOKIE)
    return state.sessions.get(token)


def require_csrf(request: Request) -> None:
    """Mutating endpoints must carry a custom header cross-origin cannot set.

    SameSite=Strict cookies already block most CSRF; this header also blocks
    top-level form posts and plugin-initiated requests.
    """
    if request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


def require_session_csrf(request: Request) -> Any:
    """Combined guard for POST/DELETE endpoints."""
    require_csrf(request)
    return require_session(request)


def authenticate_websocket(ws: WebSocket, state: WebState) -> bool:
    """Cookie-based handshake authentication for /ws (no accept yet)."""
    if not state.settings.auth.enabled:
        return True
    token = ws.cookies.get(SESSION_COOKIE)
    return state.sessions.get(token) is not None


def blaze_to_http(exc: Any) -> HTTPException:
    """Convert a core BlazeHammerError into an API error, keeping details.

    The reason/hint carry the actionable part ("retries: Input should be
    ..."); dropping them turns every failure into a useless generic message.
    """
    from blaze_hammer.errors import BlazeHammerError

    assert isinstance(exc, BlazeHammerError)
    message = exc.message
    if exc.reason:
        message = f"{message}\n{exc.reason}"
    if exc.hint:
        message = f"{message}\nHint: {exc.hint}"
    return HTTPException(status_code=400, detail=message)
