"""Structured logging setup — diagnostics go to stderr/file, never stdout."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOGGER = logging.getLogger("blaze_hammer")


def setup_logging(*, level: str = "WARNING", log_file: Path | None = None) -> None:
    """Configure the blaze_hammer logger without touching Rich's stdout."""
    resolved = getattr(logging, level.upper(), logging.WARNING)
    _LOGGER.setLevel(resolved)
    _LOGGER.propagate = False
    for handler in list(_LOGGER.handlers):
        _LOGGER.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    new_handler: logging.Handler
    if log_file is not None:
        new_handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        new_handler = logging.StreamHandler(sys.stderr)
    new_handler.setFormatter(formatter)
    _LOGGER.addHandler(new_handler)

    # HTTP client chatter is noise unless explicitly requested.
    for noisy in ("httpx", "httpcore", "h2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
