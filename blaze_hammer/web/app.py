"""FastAPI application factory for the Blaze Hammer Web GUI."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from blaze_hammer.web.config import ResolvedWebSettings
from blaze_hammer.web.dependencies import WebState, require_session
from blaze_hammer.web.routes import (
    auth_router,
    config_router,
    profiles_router,
    runs_router,
)
from blaze_hammer.web.runs import RunManager
from blaze_hammer.web.ws import Hub
from blaze_hammer.web.ws import router as ws_router

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Mutating JSON bodies larger than this are rejected outright.
MAX_BODY_BYTES = 8 * 1024 * 1024

_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'"
)


class _SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {k.decode().lower() for k, _ in headers}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"),
                    (b"content-security-policy", _CSP.encode()),
                    (b"cache-control", b"no-store"),
                ]
                for key, value in additions:
                    if key.decode() not in existing:
                        headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodySizeLimit:
    """Reject oversized request bodies before they hit routes."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            length = headers.get("content-length")
            if length and length.isdigit() and int(length) > self.limit:
                response = JSONResponse({"detail": "Request body too large"}, status_code=413)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _package_dir() -> Path:
    import blaze_hammer.web as pkg

    return Path(pkg.__file__).parent


def create_app(
    *,
    settings: ResolvedWebSettings,
    project_dir: Path,
    project_file: Path | None = None,
) -> FastAPI:
    """Build the Web GUI app bound to one project directory."""
    state = WebState(settings=settings, project_dir=project_dir, project_file=project_file)
    hub = Hub()
    state.hub = hub
    state.manager = RunManager(publish=hub.publish)

    app = FastAPI(
        title="Blaze Hammer Web",
        version="1.0.0",
        # Documentation is served manually behind the session guard below.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.web = state

    app.add_middleware(_BodySizeLimit)
    app.add_middleware(_SecurityHeaders)
    if settings.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["X-Requested-With", "Content-Type"],
        )

    app.include_router(auth_router)
    app.include_router(config_router)
    app.include_router(profiles_router)
    app.include_router(runs_router)
    app.include_router(ws_router)

    # -- protected documentation -------------------------------------------

    @app.get("/docs", include_in_schema=False)
    async def docs(request: Request) -> Any:
        require_session(request)
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Blaze Hammer API")

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi(request: Request) -> JSONResponse:
        require_session(request)
        return JSONResponse(app.openapi())

    # -- frontend -----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(_read_asset("templates/index.html"))

    @app.get("/static/app.js", include_in_schema=False)
    async def app_js() -> FileResponse:
        path = _package_dir() / "static" / "app.js"
        return FileResponse(path, media_type="text/javascript")

    # -- lifecycle ----------------------------------------------------------

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ticker = asyncio.create_task(_stats_ticker(state))
        try:
            yield
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            assert state.manager is not None
            await state.manager.shutdown()

    app.router.lifespan_context = lifespan
    return app


def _read_asset(relpath: str) -> str:
    return (_package_dir() / relpath).read_text(encoding="utf-8")


async def _stats_ticker(state: WebState) -> None:
    """Broadcast stats.updated ~4x/s while any run is active."""
    assert state.manager is not None
    hub = cast(Hub, state.hub)
    while True:
        for handle in state.manager.running_handles():
            await hub.publish(
                {"type": "stats.updated", "run_id": handle.run_id, **handle.live_stats()}
            )
        await asyncio.sleep(0.25)
