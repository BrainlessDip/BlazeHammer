"""Web GUI authentication: scrypt hashing, sessions, login rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

#: Cookie name for the HTTP-only session token.
SESSION_COOKIE = "blaze_session"

#: Sessions expire after this many seconds regardless of activity.
SESSION_TTL_S = 12 * 3600

#: Sliding expiry: a session idle longer than this is dropped.
SESSION_IDLE_S = 2 * 3600

# --- password hashing -------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 64


def hash_password(password: str) -> str:
    """Hash *password* into a self-describing ``$scrypt$`` string."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DKLEN,
    )
    return "$".join(
        (
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time verification of *password* against *encoded*."""
    if not encoded:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    try:
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- login rate limiting ----------------------------------------------------


class LoginLimiter:
    """Sliding-window failure counter per client key (in-memory)."""

    def __init__(self, *, max_failures: int = 5, window_s: float = 300.0) -> None:
        self._max = max_failures
        self._window = window_s
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self._window]
            self._failures[key] = recent
            return len(recent) < self._max

    def record_failure(self, key: str) -> None:
        with self._lock:
            self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# --- sessions ----------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    username: str
    created_at: float
    last_seen: float


class SessionStore:
    """Server-side session registry keyed by opaque random tokens."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = Session(username=username, created_at=now, last_seen=now)
        with self._lock:
            self._purge_locked()
            self._sessions[token] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        """Return the live session for *token*, sliding its idle window."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            expired = (
                now - session.created_at > SESSION_TTL_S or now - session.last_seen > SESSION_IDLE_S
            )
            if expired:
                del self._sessions[token]
                return None
            refreshed = Session(
                username=session.username,
                created_at=session.created_at,
                last_seen=now,
            )
            self._sessions[token] = refreshed
            return refreshed

    def drop(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _purge_locked(self) -> None:
        now = time.time()
        dead = [
            token
            for token, s in self._sessions.items()
            if now - s.created_at > SESSION_TTL_S or now - s.last_seen > SESSION_IDLE_S
        ]
        for token in dead:
            del self._sessions[token]

    # -- introspection for tests -------------------------------------------

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def debug_snapshot(self) -> dict[str, Any]:  # pragma: no cover - tests only
        with self._lock:
            return dict(self._sessions)
