"""FastAPI application factory — headless API + WebSocket server.

The Python package ships no frontend. A separate React project consumes
the REST/WebSocket surface documented at ``/docs``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from blaze_hammer import __version__
from blaze_hammer.web.config import ResolvedWebSettings
from blaze_hammer.web.dependencies import WebState, require_session
from blaze_hammer.web.routes import (
    auth_router,
    config_router,
    placeholder_router,
    profiles_router,
    runs_router,
)
from blaze_hammer.web.runs import RunManager
from blaze_hammer.web.ws import Hub
from blaze_hammer.web.ws import router as ws_router

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

#: Mutating JSON bodies larger than this are rejected outright.
MAX_BODY_BYTES = 8 * 1024 * 1024

API_VERSION = "v1"

#: API-only server: nothing scriptable is served, so lock everything down.
_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "NOT_AUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_413_CONTENT_TOO_LARGE: "REQUEST_TOO_LARGE",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
    status.HTTP_428_PRECONDITION_REQUIRED: "CONFIRMATION_REQUIRED",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


def _error_response(code: str, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=http_status,
    )


class _SecurityHeaders:
    def __init__(self, app: Any) -> None:
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

    def __init__(self, app: Any, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            length = headers.get("content-length")
            if length and length.isdigit() and int(length) > self.limit:
                await _error_response("REQUEST_TOO_LARGE", "Request body too large", 413)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def server_info() -> dict[str, Any]:
    """Frontend-agnostic capability descriptor."""
    return {
        "name": "Blaze Hammer",
        "version": __version__,
        "status": "ok",
        "api_version": API_VERSION,
        "features": {
            "websocket": True,
            "profiles": True,
            "faker": True,
            "preview": True,
        },
    }


def create_app(
    *,
    settings: ResolvedWebSettings,
    project_dir: Path,
    project_file: Path | None = None,
) -> FastAPI:
    """Build the headless Blaze Hammer API bound to one project directory."""
    state = WebState(settings=settings, project_dir=project_dir, project_file=project_file)
    hub = Hub()
    state.hub = hub
    state.manager = RunManager(publish=hub.publish)

    app = FastAPI(
        title="Blaze Hammer API",
        version=__version__,
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

    app.include_router(auth_router, prefix=f"/api/{API_VERSION}")
    app.include_router(config_router, prefix=f"/api/{API_VERSION}")
    app.include_router(placeholder_router, prefix=f"/api/{API_VERSION}")
    app.include_router(profiles_router, prefix=f"/api/{API_VERSION}")
    app.include_router(runs_router, prefix=f"/api/{API_VERSION}")
    app.include_router(ws_router, prefix=f"/api/{API_VERSION}")

    # -- error envelope -----------------------------------------------------

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            # Structured route errors (e.g. TEMPLATE_CONFLICT) keep their
            # extra fields inside the error object.
            err = dict(detail)
            code = err.pop("code", "HTTP_ERROR")
            message = err.pop("message", "Request failed")
            return JSONResponse(
                {"ok": False, "error": {"code": str(code), "message": str(message), **err}},
                status_code=exc.status_code,
            )
        code = _ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
        message = detail if isinstance(detail, str) else "Request failed"
        return JSONResponse(
            {"ok": False, "error": {"code": code, "message": message}},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = next(iter(exc.errors()), None)
        where = ".".join(str(p) for p in first["loc"][1:]) if first else "body"
        msg = first.get("msg", "invalid input") if first else "invalid input"
        return _error_response(
            "VALIDATION_ERROR", f"{where}: {msg}", status.HTTP_422_UNPROCESSABLE_CONTENT
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Never leak tracebacks; full details go to the server log via uvicorn.
        return _error_response(
            "INTERNAL_ERROR",
            "Unexpected server error",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # -- meta endpoints ------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        payload = server_info()
        return JSONResponse(
            {
                "name": payload["name"],
                "version": payload["version"],
                "status": "ok",
                "api": f"/api/{API_VERSION}",
                "websocket": f"/api/{API_VERSION}/ws",
                "docs": "/docs",
            }
        )

    @app.get(f"/api/{API_VERSION}/info", response_model=None)
    async def info() -> JSONResponse:
        return JSONResponse(server_info())

    # -- protected documentation -------------------------------------------

    @app.get("/docs", include_in_schema=False)
    async def swagger_docs(request: Request) -> Any:
        require_session(request)
        return get_swagger_ui_html(openapi_url="/openapi.json", title="Blaze Hammer API — Swagger")

    @app.get("/redoc", include_in_schema=False)
    async def redoc_docs(request: Request) -> Any:
        require_session(request)
        return get_redoc_html(openapi_url="/openapi.json", title="Blaze Hammer API — ReDoc")

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_schema(request: Request) -> JSONResponse:
        require_session(request)
        return JSONResponse(app.openapi())

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
