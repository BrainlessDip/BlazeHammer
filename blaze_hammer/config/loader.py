"""Configuration loading: files, profiles, environment and CLI merge.

Priority (highest wins):

    CLI arguments  >  environment variables  >  profile file
                            >  blazehammer.yaml  >  built-in defaults

Environment variables are accepted under two prefixes: ``BLAZE_*``
(legacy) and ``BLAZE_HAMMER_*``. When both spellings of one field are
set the plain ``BLAZE_*`` value wins (deterministic, documented).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from blaze_hammer.errors import ConfigurationError, ProfileError

if TYPE_CHECKING:
    from collections.abc import Mapping

PROFILES_DIR = Path("profiles")
PROJECT_CONFIG_NAME = "blazehammer.yaml"
ENV_PREFIXES = ("BLAZE_HAMMER_", "BLAZE_")

#: Supported environment overrides: env name suffix -> (config path, caster)
ENV_FIELDS: dict[str, tuple[tuple[str, ...], type]] = {
    "TARGET": (("target",), str),
    "METHOD": (("method",), str),
    "REQUESTS": (("requests",), int),
    "CONCURRENCY": (("concurrency",), int),
    "DELAY": (("delay",), float),
    "RATE": (("rate",), float),
    "TIMEOUT": (("timeout",), float),
    "RETRIES": (("retries", "max_retries"), int),
    "SEED": (("seed",), int),
    "FAKER_LOCALE": (("faker_locale",), str),
    "PAYLOAD_FILE": (("payload_file",), str),
    "HEADERS_FILE": (("headers_file",), str),
    "WEB_HOST": (("web", "host"), str),
    "WEB_PORT": (("web", "port"), int),
    "WEB_USERNAME": (("web", "auth", "username"), str),
    "WEB_PASSWORD": (("web", "auth", "password"), str),
}


def load_json_file(path: Path, label: str) -> dict:
    """Load a JSON template/config file with actionable error reporting."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(
            f"Unable to load {label}", reason=f"file not found: {path}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(f"Unable to load {label}", reason=str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"Unable to load {label}", reason=f"{path} is not valid UTF-8"
        ) from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Unable to load {label}",
            reason=(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"),
            expected="string, number, boolean, object, array",
            found="malformed JSON syntax",
            hint=f"check {path} around the reported position",
        ) from exc
    if not isinstance(parsed, dict):
        raise ConfigurationError(
            f"Unable to load {label}",
            reason=f"{path} must contain a JSON object at the top level",
            found=type(parsed).__name__,
        )
    return parsed


def resolve_profile_path(name_or_path: str, base_dir: Path | None = None) -> Path:
    """Resolve ``register`` to ``profiles/register.json``; paths pass through.

    When *base_dir* is given (the project YAML's directory) it is checked
    first, so ``--config sub/proj/blazehammer.yaml --profile x`` finds
    ``sub/proj/profiles/x.json`` regardless of the caller's cwd.
    """
    candidate = Path(name_or_path)
    if candidate.suffix == ".json" or candidate.is_file():
        return candidate
    search_dirs = []
    if base_dir is not None:
        search_dirs.append(base_dir / "profiles")
    search_dirs.append(PROFILES_DIR)
    for directory in search_dirs:
        default = directory / f"{name_or_path}.json"
        if default.is_file():
            return default
    available = sorted(p.stem for p in PROFILES_DIR.glob("*.json")) if PROFILES_DIR.is_dir() else []
    listing = ", ".join(available) if available else "none found"
    raise ProfileError(
        f"Profile '{name_or_path}' not found",
        reason=f"looked for {candidate} and {', '.join(str(d) for d in search_dirs)}",
        hint=f"available profiles: {listing}",
    )


def load_profile(name_or_path: str, base_dir: Path | None = None) -> dict[str, Any]:
    return load_json_file(resolve_profile_path(name_or_path, base_dir), f"profile '{name_or_path}'")


def collect_env_overrides(environ: Mapping[str, str]) -> dict[str, Any]:
    """Translate ``BLAZE_*`` / ``BLAZE_HAMMER_*`` variables into config."""
    overrides: dict[str, Any] = {}
    for key in sorted(environ):
        value = environ[key]
        field_name: str | None = None
        for prefix in ENV_PREFIXES:
            if key.startswith(prefix):
                field_name = key[len(prefix) :]
                break
        if field_name is None:
            continue
        entry = ENV_FIELDS.get(field_name)
        if entry is None:
            continue
        path, caster = entry
        try:
            casted: Any = caster(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"Invalid value for environment variable {key}",
                reason=f"{value!r} is not a valid {caster.__name__}",
            ) from exc
        cursor = overrides
        for step in path[:-1]:
            cursor = cursor.setdefault(step, {})
        cursor[path[-1]] = casted
    return overrides


def build_config(  # noqa: C901
    cli_overrides: Mapping[str, Any],
    *,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> Any:  # RunConfig; typed loosely to avoid import cycle in annotations
    """Merge all configuration sources and validate into a RunConfig.

    Merge order (lowest to highest): blazehammer.yaml, profile,
    environment variables, CLI arguments. ``config_path`` overrides
    automatic discovery of ``./blazehammer.yaml``.
    """
    import os

    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.config.project import find_project_config, load_project_yaml

    # A profile may arrive either explicitly or as an override key.
    profile_name = profile or cli_overrides.get("profile")
    env = os.environ if environ is None else environ

    merged: dict[str, Any] = {}

    # 4. project YAML (lowest explicit layer)
    yaml_path: Path | None
    if config_path is not None:
        yaml_path = Path(config_path).expanduser()
        if not yaml_path.is_file():
            raise ConfigurationError(
                f"Configuration file not found: {config_path}",
                hint="check the --config path (relative paths resolve against the cwd)",
            )
    else:
        yaml_path = find_project_config()
    if yaml_path is not None:
        merged.update(load_project_yaml(yaml_path))

    # 3. profile file
    if profile_name:
        base_dir = yaml_path.resolve().parent if yaml_path is not None else None
        merged.update(load_profile(str(profile_name), base_dir))

    # 2. environment variables
    merged.update(collect_env_overrides(env))

    # 1. CLI arguments
    merged.update({k: v for k, v in cli_overrides.items() if v is not None})
    merged.pop("profile", None)

    # Friendly zero-config UX: fire whenever no explicit profile supplied a
    # target, so an empty/targetless project still points the user at init.
    # A *profile* without a target falls through to schema validation so the
    # user sees their actual mistake (unknown fields, bad types, ...).
    target = merged.get("target")
    if (not isinstance(target, str) or not target.strip()) and not profile_name:
        raise ConfigurationError(
            "No Blaze Hammer project found",
            reason=(
                "no target URL was given and no blazehammer.yaml with a target "
                "exists in the current directory"
            ),
            hint=(
                "create a project with 'blaze-hammer init', pass a URL "
                "('bh https://host/api'), or use --config"
            ),
        )
    if isinstance(target, str):
        merged["target"] = target.strip()

    try:
        cfg = RunConfig(**merged)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<config>"
            lines.append(f"- {location}: {error['msg']}")
        raise ConfigurationError(
            "Invalid configuration",
            reason="\n".join(lines),
            hint="run 'blaze-hammer inspect' for a full explanation",
        ) from exc
    if profile_name:
        cfg.profile = str(profile_name)
    return cfg
