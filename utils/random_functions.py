"""Deprecated helpers kept for backwards compatibility.

Generation logic now lives in ``blaze_hammer.templating.builtins`` with an
explicitly injected ``random.Random`` (required for ``--seed`` support).
These wrappers use an unseeded module-level Random; prefer the new API or
the placeholder system for anything reproducible.
"""

from __future__ import annotations

import random as _random_module

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.templating.builtins import generate_password as _password_seedable
from blaze_hammer.templating.builtins import (
    generate_random_email as _email_seedable,
)
from blaze_hammer.templating.builtins import (
    generate_random_float as _float_seedable,
)
from blaze_hammer.templating.builtins import (
    generate_random_number as _number_seedable,
)
from blaze_hammer.templating.builtins import (
    generate_random_string as _string_seedable,
)

_rng = _random_module.Random()


def generate_random_email(prefix="Brainless_Dip+", length=10, domains=None):
    return _email_seedable(_rng, prefix=prefix, length=length, domains=domains)


def generate_random_number(start="013", length=8):
    return _number_seedable(_rng, start=start, length=length)


def generate_random_string(length=8):
    return _string_seedable(_rng, length=length)


def generate_random_float(min_val, max_val, precision=2):
    return _float_seedable(_rng, min_val, max_val, precision)


def generate_password(length=12, uppercase=True, lowercase=True, digits=True, symbols=False):
    try:
        return _password_seedable(
            _rng,
            length=length,
            uppercase=uppercase,
            lowercase=lowercase,
            digits=digits,
            symbols=symbols,
        )
    except TemplateResolutionError:
        # Legacy behavior returned a marker string instead of raising.
        return "[Invalid password settings: no character sets enabled]"


# Per-file cache retained for legacy callers of pick_line().
cache: dict = {}


def pick_line(file):
    if file not in cache:
        try:
            with open(file, encoding="utf-8", errors="replace") as handle:
                cache[file] = [line.rstrip("\n") for line in handle.readlines()]
        except Exception as exc:  # noqa: BLE001 - legacy string-return behavior
            return str(exc)
    lines = cache[file]
    if not lines:
        return "File is empty"
    return _rng.choice(lines)
