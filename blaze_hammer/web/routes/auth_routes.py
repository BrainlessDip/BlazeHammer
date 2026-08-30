"""Auth routes: login, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from blaze_hammer.web.auth import SESSION_COOKIE
from blaze_hammer.web.dependencies import WebState, get_state, require_session
from blaze_hammer.web.models import LoginRequest, LoginResponse, MeResponse, OkResponse

router = APIRouter(prefix="")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    state: WebState = get_state(request)
    settings = state.settings
    key = _client_key(request)

    if settings.auth.enabled:
        if not settings.auth_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is enabled but credentials are not configured",
            )
        if not state.limiter.allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed attempts; wait a few minutes",
            )
        from blaze_hammer.web.auth import verify_password

        if body.username != settings.auth.username or not verify_password(
            body.password, settings.auth.password_hash
        ):
            state.limiter.record_failure(key)
            # Uniform message: never reveal which half was wrong.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
            )
        state.limiter.reset(key)

    token, session = state.sessions.create(body.username)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=False,  # local tool; set True behind TLS deployments
        max_age=12 * 3600,
        path="/",
    )
    return LoginResponse(ok=True, username=session.username)


@router.post("/auth/logout", response_model=OkResponse)
async def logout(request: Request, response: Response) -> OkResponse:
    state: WebState = get_state(request)
    token = request.cookies.get(SESSION_COOKIE)
    state.sessions.drop(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return OkResponse()


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    session = require_session(request)  # raises 401 when auth on + no session
    state: WebState = get_state(request)
    if session is None:  # auth disabled
        return MeResponse(username="local")
    _ = state
    return MeResponse(username=session.username)


@router.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "service": "blaze-hammer"}
