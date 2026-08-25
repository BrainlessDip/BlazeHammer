"""Project configuration (``blazehammer.yaml``): discovery, parse, normalize.

The YAML layer feeds the same merge pipeline as every other source
(``config.loader.build_config``); nothing else in the application reads
this file directly.

Discovery is deliberately conservative: only ``./blazehammer.yaml`` in the
current working directory is found automatically. Parent directories are
never searched; use ``--config`` when running from elsewhere.

Paths inside the YAML (``payload:``, ``headers:``) resolve relative to the
directory containing the YAML file, so a project works from any cwd::

    cd /somewhere
    bh run --config /projects/my-api-test/blazehammer.yaml   # still correct
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from blaze_hammer.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterable

PROJECT_FILE_NAME = "blazehammer.yaml"

#: Friendly YAML spellings -> canonical RunConfig locations.
TOP_LEVEL_ALIASES = {
    "payload": "payload_file",
    "headers": "headers_file",
}

#: Keys accepted at the top level after alias expansion.
ALLOWED_TOP_KEYS = frozenset(
    {
        "target",
        "method",
        "payload_file",
        "headers_file",
        "disable_headers",
        "post_type",
        "file_payload",
        "requests",
        "concurrency",
        "delay",
        "rate",
        "timeout",
        "seed",
        "faker",
        "faker_locale",
        "assume_yes",
        "sensitive_keys",
        "retries",
        "preview",
        "output",
        "web",
    }
)

ALLOWED_WEB_KEYS = frozenset({"enabled", "host", "port", "auth", "cors"})
ALLOWED_WEB_AUTH_KEYS = frozenset({"enabled", "username", "password", "password_hash"})
ALLOWED_WEB_CORS_KEYS = frozenset({"enabled", "allow_origins"})

ALLOWED_FAKER_KEYS = frozenset({"locale", "seed"})
ALLOWED_OUTPUT_KEYS = frozenset(
    {
        "simple",
        "print_payload",
        "print_headers",
        "print_response",
        "status_filter",
        "failed_only",
        "success_only",
        "max_response_size",
        "file",
        "log_level",
        "log_file",
        "debug",
    }
)
ALLOWED_RETRY_KEYS = frozenset(
    {
        "max_retries",
        "backoff_base",
        "backoff_cap",
        "retry_statuses",
        "retry_on_timeout",
    }
)

#: ``output.file`` is friendlier than the internal field name.
OUTPUT_ALIASES = {"file": "export_path"}


def find_project_config(start: Path | None = None) -> Path | None:
    """Return ``<start>/blazehammer.yaml`` when it exists (no parent walk)."""
    candidate = (start or Path.cwd()) / PROJECT_FILE_NAME
    return candidate if candidate.is_file() else None


def load_project_yaml(path: Path) -> dict[str, Any]:
    """Load and normalize a project YAML file into merge-ready fragments."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Unable to load {path}", reason="file not found") from exc
    except OSError as exc:
        raise ConfigurationError(f"Unable to load {path}", reason=str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"Unable to load {path}", reason=f"{path} is not valid UTF-8"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _yaml_error(path, exc) from exc

    if data is None:  # empty file
        data = {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"Invalid configuration file: {path}",
            reason=f"top level must be a mapping, got {type(data).__name__}",
            hint=f"see 'blaze-hammer init' for an example {PROJECT_FILE_NAME}",
        )
    return _normalize(data, base_dir=path.resolve().parent)


def _yaml_error(path: Path, exc: Exception) -> ConfigurationError:
    """Wrap a YAML syntax error with line/column when available."""
    mark = getattr(exc, "problem_mark", None)
    where = ""
    if mark is not None:
        where = f" at line {mark.line + 1}, column {mark.column + 1}"
    problem = getattr(exc, "problem", None) or str(exc)
    return ConfigurationError(
        f"Invalid YAML in {path}{where}",
        reason=problem,
        hint="fix the syntax error reported above",
    )


def _check_unknown(keys: Iterable[str], allowed: frozenset[str], where: str, label: str) -> None:
    unknown = [key for key in keys if key not in allowed]
    if not unknown:
        return
    lines = []
    for key in unknown:
        close = difflib.get_close_matches(key, sorted(allowed), n=1, cutoff=0.6)
        suffix = f"\n\nDid you mean:\n  {close[0]}" if close else ""
        lines.append(f"Unknown {label} key: '{key}'{suffix}")
    raise ConfigurationError(
        f"Invalid configuration ({where})",
        reason="\n".join(lines),
        hint=f"check spelling against 'blaze-hammer init' template ({where})",
    )


def _require_mapping(value: Any, section: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(
            "Invalid configuration",
            reason=f"'{section}' must be a mapping, got {type(value).__name__}",
        )
    return value


def _resolve_path(raw: Any, base_dir: Path, key: str) -> Any:
    """Resolve a configured file path relative to the YAML directory."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigurationError(
            "Invalid configuration",
            reason=f"'{key}' must be a non-empty path string",
        )
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve()


def _normalize(data: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    expanded: dict[str, Any] = {}
    for key, value in data.items():
        canonical = TOP_LEVEL_ALIASES.get(key, key)
        expanded[canonical] = value
    _check_unknown(expanded.keys(), ALLOWED_TOP_KEYS, "top level", "configuration")

    out: dict[str, Any] = {}
    for key, value in expanded.items():
        if value is None:
            continue
        if key == "faker":
            faker = _require_mapping(value, "faker")
            _check_unknown(faker.keys(), ALLOWED_FAKER_KEYS, "faker:", "faker")
            if "locale" in faker and faker["locale"] is not None:
                out["faker_locale"] = faker["locale"]
            if "seed" in faker and faker["seed"] is not None:
                out["seed"] = faker["seed"]
        elif key == "output":
            output = _require_mapping(value, "output")
            _check_unknown(output.keys(), ALLOWED_OUTPUT_KEYS, "output:", "output")
            fragment: dict[str, Any] = {}
            for okey, ovalue in output.items():
                if ovalue is None:
                    continue
                canonical_okey = OUTPUT_ALIASES.get(okey, okey)
                if canonical_okey in ("export_path", "log_file") and isinstance(ovalue, str):
                    ovalue = _resolve_path(ovalue, base_dir, f"output.{okey}")
                fragment[canonical_okey] = ovalue
            if fragment:
                out["output"] = fragment
        elif key == "retries":
            if isinstance(value, int) and not isinstance(value, bool):
                out["retries"] = {"max_retries": value}
                continue
            retries = _require_mapping(value, "retries")
            _check_unknown(retries.keys(), ALLOWED_RETRY_KEYS, "retries:", "retries")
            fragment_r = {k: v for k, v in retries.items() if v is not None}
            if fragment_r:
                out["retries"] = fragment_r
        elif key == "preview":
            preview = _require_mapping(value, "preview")
            fragment_p = {k: v for k, v in preview.items() if v is not None}
            if fragment_p:
                out["preview"] = fragment_p
        elif key == "web":
            web = _require_mapping(value, "web")
            _check_unknown(web.keys(), ALLOWED_WEB_KEYS, "web:", "web")
            fragment_w: dict[str, Any] = {}
            for wkey, wvalue in web.items():
                if wvalue is None:
                    continue
                if wkey == "auth":
                    auth = _require_mapping(wvalue, "web.auth")
                    _check_unknown(auth.keys(), ALLOWED_WEB_AUTH_KEYS, "web.auth:", "web.auth")
                    fragment_w["auth"] = {k: v for k, v in auth.items() if v is not None}
                elif wkey == "cors":
                    cors = _require_mapping(wvalue, "web.cors")
                    _check_unknown(cors.keys(), ALLOWED_WEB_CORS_KEYS, "web.cors:", "web.cors")
                    fragment_w["cors"] = {k: v for k, v in cors.items() if v is not None}
                else:
                    fragment_w[wkey] = wvalue
            if fragment_w:
                out["web"] = fragment_w
        elif key == "sensitive_keys":
            out[key] = list(value) if isinstance(value, (list, tuple)) else [value]
        elif key in ("payload_file", "headers_file"):
            out[key] = _resolve_path(value, base_dir, key)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# init template generation
# ---------------------------------------------------------------------------

TEMPLATE_YAML = """\
# Blaze Hammer configuration
# Docs: https://github.com/blaze-hammer (placeholders: blaze-hammer placeholders)

target: "{target}"

method: {method}

requests: {requests}
concurrency: {concurrency}
delay: 0

payload: payload.json
headers: headers.json

post_type: json

timeout: 10
retries: 0

faker:
  locale: en_US

  # Set a number for reproducible generated data (--seed overrides this).
  # seed: 12345

output:
  simple: false

  # Optional result file (.json or .csv)
  # file: results.json

web:
  enabled: true
  host: "127.0.0.1"   # bind 0.0.0.0 only if you understand the risk
  port: 8080

  auth:
    enabled: true
    # username: admin
    # password: change-me          # hashed in memory at startup, never logged
    # password_hash: "$scrypt$..." # pre-hashed alternative

  cors:
    enabled: false
"""

TEMPLATE_PAYLOAD = """\
{
  "username": "{username(length=10)}",
  "email": "{email(prefix=user_, length=12)}",
  "age": "{int(min=18, max=80)}",
  "signup_date": "{date(offset=-30d)}",
  "notes": "{faker.sentence}"
}
"""

TEMPLATE_HEADERS = """\
{
  "Content-Type": "application/json",
  "Accept": "application/json",
  "X-Request-ID": "{uuid}",
  "X-Test-User": "{faker.user_name}"
}
"""


def render_template_yaml(
    *,
    target: str,
    method: str,
    requests: int,
    concurrency: int,
) -> str:
    return TEMPLATE_YAML.format(
        target=target,
        method=method,
        requests=requests,
        concurrency=concurrency,
    )
