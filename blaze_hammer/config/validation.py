"""Cross-field and filesystem validation performed before any traffic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from blaze_hammer.errors import ConfigurationError
from blaze_hammer.security.env import ENV_PATTERN

if TYPE_CHECKING:
    from blaze_hammer.config.models import RunConfig


@dataclass
class ValidationOutcome:
    """Collected named checks; usable by both run (strict) and inspect."""

    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, label: str, ok: bool, detail: str = "") -> None:
        self.checks.append((label, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _label, ok, _detail in self.checks)

    @property
    def failures(self) -> list[str]:
        return [f"{label}: {detail}" for label, ok, detail in self.checks if not ok]


def validate_config(cfg: RunConfig) -> ValidationOutcome:
    """Collect every check without raising (inspect-friendly)."""
    outcome = ValidationOutcome()

    if cfg.target.lower().startswith(("http://", "https://")):
        outcome.add("Target URL", True, cfg.target)
    else:
        outcome.add(
            "Target URL",
            False,
            f"{cfg.target} (expected http:// or https:// scheme)",
        )

    from blaze_hammer.config.models import PostType

    needs_payload = (
        cfg.method.supports_body
        and cfg.post_type != PostType.NONE
        and not cfg.preview.dry_run
        and not cfg.file_payload
        and not cfg.inline_templates
    )
    if needs_payload and cfg.payload_file is None:
        outcome.add(
            "Payload configured",
            False,
            f"{cfg.method.value} with a body requires --payload or --file-payload",
        )
    else:
        outcome.add("Payload configured", True, str(cfg.payload_file or "-"))

    headers_active = cfg.headers_file is not None and not cfg.disable_headers
    outcome.add("Headers", True, str(cfg.headers_file) if headers_active else "disabled")

    if cfg.inline_templates:
        outcome.add("Payload configured", True, "inline (web editor)")
    elif cfg.payload_file is not None and not Path_check(cfg.payload_file):
        outcome.add("Payload file exists", False, str(cfg.payload_file))
    elif cfg.payload_file is not None:
        outcome.add("Payload file exists", True, str(cfg.payload_file))
    if headers_active and cfg.headers_file is not None and not Path_check(cfg.headers_file):
        outcome.add("Headers file exists", False, str(cfg.headers_file))
    elif headers_active:
        outcome.add("Headers file exists", True, str(cfg.headers_file))

    if cfg.file_payload and cfg.post_type.value not in ("form", "multipart"):
        outcome.add(
            "Body type",
            False,
            f"--file-payload requires --post-type form or multipart, got {cfg.post_type.value}",
        )
    else:
        body = "multipart files + form fields" if cfg.file_payload else cfg.post_type.value
        outcome.add("Body type", True, body)

    filters = cfg.output
    active_filters = sum(
        (filters.status_filter is not None, filters.success_only, filters.failed_only)
    )
    if active_filters > 1:
        outcome.add(
            "Output filters",
            False,
            "--status cannot be combined with --success-only/--failed-only",
        )
    else:
        outcome.add("Output filters", True, "off" if active_filters == 0 else "on")

    outcome.add(
        "Rate limit",
        True,
        f"{cfg.rate} req/s" if cfg.rate else "unlimited",
    )
    return outcome


def ensure_config_valid(cfg: RunConfig) -> None:
    """Raise ConfigurationError listing every failed check."""
    outcome = validate_config(cfg)
    if not outcome.ok:
        raise ConfigurationError(
            "Invalid configuration",
            reason="\n".join(f"- {failure}" for failure in outcome.failures),
            hint="run 'blaze-hammer inspect' for a full explanation",
        )


def Path_check(path: str | Path) -> bool:
    return Path(path).is_file()


def summarize_placeholders(text: str) -> dict[str, int]:
    """Counts by placeholder family for the inspector."""
    from blaze_hammer.templating.registry import PLACEHOLDER_PATTERN

    counts = {"faker": 0, "builtin": 0, "env": 0}
    pattern = re.compile(PLACEHOLDER_PATTERN)
    for match in pattern.finditer(text):
        content = match.group(1).strip()
        if ENV_PATTERN.search(match.group(1)):
            counts["env"] += 1
        elif content.startswith("faker."):
            counts["faker"] += 1
        else:
            counts["builtin"] += 1
    return counts
