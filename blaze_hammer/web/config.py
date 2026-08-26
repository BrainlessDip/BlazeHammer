"""Web settings resolution: YAML ``web:`` section + env + CLI precedence.

Reuses the standard pipeline (``build_config``) so web options obey the
documented merge order; the resolved :class:`RunConfig` carries both run
defaults and the ``web`` section.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING

from blaze_hammer.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from blaze_hammer.config.models import RunConfig


@dataclass(frozen=True)
class ResolvedAuth:
    enabled: bool
    username: str | None = None
    password_hash: str | None = None


@dataclass(frozen=True)
class ResolvedWebSettings:
    enabled: bool
    host: str
    port: int
    auth: ResolvedAuth
    cors_enabled: bool
    cors_origins: tuple[str, ...]

    @property
    def auth_ready(self) -> bool:
        return not self.auth.enabled or (bool(self.auth.username) and bool(self.auth.password_hash))


def resolve_web_settings(
    cfg: RunConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> ResolvedWebSettings:
    """Derive final web settings from a merged RunConfig.

    A plaintext ``web.auth.password`` is hashed here (in memory only).
    Environment variables were already folded into *cfg* by
    ``build_config``; this function additionally honours the short
    ``BH_WEB_*`` spellings for convenience.
    """
    import os

    from blaze_hammer.web.auth import hash_password

    env = os.environ if environ is None else environ
    web = cfg.web.model_copy(deep=True)

    # Short-prefix convenience aliases (existing BLAZE_*/BLAZE_HAMMER_*
    # variables are handled by build_config's ENV_FIELDS).
    def _env(name: str) -> str | None:
        for prefix in ("BLAZE_HAMMER_WEB_", "BLAZE_WEB_", "BH_WEB_"):
            value = env.get(prefix + name)
            if value:
                return value
        return None

    host_env = _env("HOST")
    port_env = _env("PORT")
    user_env = _env("USERNAME")
    pass_env = _env("PASSWORD")
    if host_env:
        web.host = host_env
    if port_env:
        try:
            web.port = int(port_env)
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid BH_WEB_PORT value: {port_env!r}", reason=str(exc)
            ) from exc
    if user_env:
        web.auth.username = user_env
    if pass_env and not web.auth.password_hash:
        web.auth.password = pass_env

    password_hash = web.auth.password_hash
    if not password_hash and web.auth.password:
        password_hash = hash_password(web.auth.password)

    auth = ResolvedAuth(
        enabled=web.auth.enabled,
        username=web.auth.username,
        password_hash=password_hash,
    )
    return ResolvedWebSettings(
        enabled=web.enabled,
        host=web.host,
        port=web.port,
        auth=auth,
        cors_enabled=web.cors.enabled,
        cors_origins=tuple(web.cors.allow_origins),
    )


def generate_password(length: int = 16) -> str:
    """Random password for setup flows (printed once by the CLI)."""
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(max(8, length)))
