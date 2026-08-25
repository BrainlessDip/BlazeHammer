"""Environment variable expansion for payload/header templates."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from blaze_hammer.errors import MissingEnvVarError

ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class EnvExpansion:
    value: str
    variables: tuple[str, ...]


def expand_env_vars(text: str, environ: Mapping[str, str] | None = None) -> EnvExpansion:
    """Expand ``${VAR}`` references in *text*.

    Raises :class:`MissingEnvVarError` listing every missing variable so
    users get one actionable message instead of a failure per request.
    """
    env = os.environ if environ is None else environ
    referenced = ENV_PATTERN.findall(text)
    if not referenced:
        return EnvExpansion(text, ())

    missing = [name for name in referenced if name not in env]
    if missing:
        raise MissingEnvVarError(missing)

    def repl(match: re.Match[str]) -> str:
        return env[match.group(1)]

    return EnvExpansion(ENV_PATTERN.sub(repl, text), tuple(dict.fromkeys(referenced)))
