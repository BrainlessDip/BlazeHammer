"""Built-in placeholder implementations.

All randomness flows through the run's ``random.Random`` instance so
``--seed`` makes generation reproducible. The legacy keyword order of
``utils/replace_placeholders.py`` is preserved exactly:

    exact:  uuid, timestamp*, bool, ip
    prefix: email, number, str, string, int, float, password,
            pick_line, choice, [datetime], date

(*timestamp moved from exact- to prefix-matching so it can accept an
``offset=`` argument; plain ``{timestamp}`` behaves identically.)

New API-testing placeholders are registered after the legacy chain and are
carefully chosen so they cannot shadow any legacy keyword (prefix matching
is ordered; see ``register_builtins``).

Type preservation: placeholders flagged ``native=True`` return real JSON
types when they occupy an entire template value (``{bool}`` → true,
``{null}`` → null, ``{int(...)}`` → 42, ``{list(...)}`` → [...]). Embedded
in larger strings they coerce to JSON-style text (``true``, ``null``,
``[1,2]``). Legacy placeholders keep returning strings.
"""

from __future__ import annotations

import random
import re
import secrets
import string
import uuid as uuid_module
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from blaze_hammer.errors import TemplateResolutionError

if TYPE_CHECKING:
    from blaze_hammer.templating.registry import (
        ArgsDict,
        Handler,
        PlaceholderRegistry,
        ResolveContext,
    )

CHOICE_PATTERN = re.compile(r"choice\((.*?)\)")

# ---------------------------------------------------------------------------
# Resource limits — reject unreasonable arguments instead of allocating.
# ---------------------------------------------------------------------------

MAX_STRING_LENGTH = 100_000  # hex/digits/letters/alphanumeric/unicode/...
LONG_STRING_MAX = 1_000_000  # dedicated long_string() ceiling (~1 MB)
MAX_LIST_LENGTH = 1_000
MAX_LIST_NESTING = 3
MAX_LARGE_INT_DIGITS = 1_000
MAX_TOKEN_LENGTH = 1_024

LIST_PATTERN = re.compile(
    r"^list\(\s*item\s*=\s*(?P<item>.+?),?\s*length\s*=\s*(?P<length>\d+)\s*\)$",
    re.DOTALL,
)

_OFFSET_RE = re.compile(r"^([+-]?\d+)([smhdw])$")
_OFFSET_UNITS: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "m": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
}

_SLUG_WORDS = (
    "alpha",
    "bravo",
    "delta",
    "echo",
    "kilo",
    "lima",
    "nova",
    "orbit",
    "pixel",
    "quark",
    "raven",
    "solar",
    "tango",
    "umbra",
    "vertex",
    "zephyr",
)


# ---------------------------------------------------------------------------
# Argument helpers — one source of truth for coercion + error text.
# ---------------------------------------------------------------------------


def _int_error(key: str, raw: str | None, lo: int, hi: int) -> str | None:
    """Return an error message for a bad integer argument, or None if OK."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return f"Argument '{key}' must be an integer (got {raw!r})"
    if value < lo:
        if lo == 1:
            return f"Argument '{key}' must be a positive integer."
        return f"Argument '{key}' must be an integer >= {lo}."
    if value > hi:
        return f"Argument '{key}' must be an integer between {lo} and {hi}."
    return None


def _check_length_arg(
    content: str, args: ArgsDict, key: str = "length", hi: int = MAX_STRING_LENGTH
) -> str | None:
    return _int_error(key, args.get(key), 1, hi)


def _require_int(
    args: ArgsDict,
    key: str,
    default: int,
    *,
    lo: int,
    hi: int,
    token: str,
) -> int:
    raw = args.get(key)
    problem = _int_error(key, raw, lo, hi)
    if problem is not None:
        raise TemplateResolutionError(problem, token=token)
    return int(raw) if raw is not None else default


def _as_bool(value: str) -> bool:
    return value.lower() == "true"


def _as_int_legacy(args: ArgsDict, key: str, default: int, *, token: str) -> int:
    raw = args.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise TemplateResolutionError(f"Invalid integer for '{key}': {raw!r}", token=token) from exc


def _as_float_legacy(args: ArgsDict, key: str, default: float, *, token: str) -> float:
    raw = args.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise TemplateResolutionError(f"Invalid number for '{key}': {raw!r}", token=token) from exc


# ---------------------------------------------------------------------------
# Offset parsing ({date(offset=-7d)}, {timestamp(offset=-1h)}, ...)
# ---------------------------------------------------------------------------


def parse_offset(raw: str | None, *, token: str) -> timedelta | None:
    if raw is None:
        return None
    match = _OFFSET_RE.match(raw.strip())
    if match is None:
        raise TemplateResolutionError(
            f"Invalid offset {raw!r}",
            token=token,
            hint="offsets look like -7d, +30m, -1h, +2w (units: s, m, h, d, w)",
        )
    count = int(match.group(1))
    unit = match.group(2)
    return timedelta(**{_OFFSET_FIELD[unit]: count})


def _offset_problem(raw: str | None) -> str | None:
    if raw is None:
        return None
    if _OFFSET_RE.match(raw.strip()) is None:
        return f"Invalid offset {raw!r} (supported units: s, m, h, d, w)"
    return None


def _offset_problem_for(content: str, args: ArgsDict) -> str | None:
    """Validator view: also catches ``offset=`` present but empty."""
    raw = args.get("offset")
    if raw is None and "offset=" in content:
        return "Argument 'offset' must not be empty."
    return _offset_problem(raw)


def _offset_from_args(content: str, args: ArgsDict) -> timedelta | None:
    """Runtime view of _offset_problem_for."""
    raw = args.get("offset")
    if raw is None and "offset=" in content:
        raise TemplateResolutionError("Argument 'offset' must not be empty.", token=content)
    return parse_offset(raw, token=content)


def _base_now(ctx: ResolveContext, args: ArgsDict, token: str) -> datetime:
    offset = _offset_from_args(token, args) or timedelta()
    return ctx.now() + offset


# ---------------------------------------------------------------------------
# Generation helpers (single source of truth; utils.random_functions
# delegates here for backwards compatibility).
# ---------------------------------------------------------------------------


def generate_random_email(
    rng: random.Random,
    prefix: str = "Brainless_Dip+",
    length: int = 10,
    domains: list[str] | tuple[str, ...] | None = None,
) -> str:
    if domains is None:
        domains = ["gmail.com"]
    charset = string.ascii_letters + string.digits
    local_part = "".join(rng.choice(charset) for _ in range(length))
    domain = rng.choice(list(domains))
    return f"{prefix}{local_part}@{domain}"


def generate_random_number(rng: random.Random, start: str = "013", length: int = 8) -> str:
    if length < len(start):
        raise TemplateResolutionError(
            f"'start' ({start!r}) is longer than 'length' ({length})",
            hint="increase length or shorten start",
        )
    digits = "0123456789"
    rest = "".join(rng.choice(digits) for _ in range(length - len(start)))
    return f"{start}{rest}"


def generate_random_string(rng: random.Random, length: int = 8) -> str:
    charset = string.ascii_letters + string.digits
    return "".join(rng.choice(charset) for _ in range(length))


def generate_random_float(
    rng: random.Random, min_val: float, max_val: float, precision: int = 2
) -> str:
    return str(round(rng.uniform(min_val, max_val), precision))


def generate_password(
    rng: random.Random,
    length: int = 12,
    uppercase: bool = True,
    lowercase: bool = True,
    digits: bool = True,
    symbols: bool = False,
) -> str:
    charset = ""
    if uppercase:
        charset += string.ascii_uppercase
    if lowercase:
        charset += string.ascii_lowercase
    if digits:
        charset += string.digits
    if symbols:
        charset += "!@#$%^&*()-_=+[]{}<>?/"
    if not charset:
        raise TemplateResolutionError(
            "password() has no character sets enabled",
            hint="enable at least one of uppercase/lowercase/digits/symbols",
        )
    return "".join(rng.choice(charset) for _ in range(length))


def pick_line(ctx: ResolveContext, file_path: str | None, rng: random.Random) -> str:
    if not file_path:
        raise TemplateResolutionError("pick_line requires a 'file' argument")
    lines = ctx.file_cache.get(file_path)
    if lines is None:
        try:
            with open(file_path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except OSError as exc:
            raise TemplateResolutionError(f"pick_line cannot read '{file_path}': {exc}") from exc
        lines = [line.rstrip("\n") for line in lines]
        ctx.file_cache[file_path] = lines
    if not lines:
        raise TemplateResolutionError(f"pick_line file '{file_path}' is empty")
    return rng.choice(lines)


def generate_hex(rng: random.Random, length: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(length))


def generate_unicode_string(rng: random.Random, length: int) -> str:
    """Unicode sample data covering several scripts, never lone surrogates."""
    # (start, end) codepoint ranges: latin/greek/cyrillic/CJK/symbols/emoji.
    ranges = (
        (0x00C0, 0x024F),
        (0x0370, 0x03FF),
        (0x0400, 0x04FF),
        (0x4E00, 0x4E00 + 500),
        (0x2200, 0x22FF),
        (0x1F300, 0x1F64F),
    )
    chars: list[str] = []
    while len(chars) < length:
        start, end = rng.choice(ranges)
        codepoint = rng.randint(start, end)
        if 0xD800 <= codepoint <= 0xDFFF:  # surrogates break JSON encoders
            continue
        chars.append(chr(codepoint))
    return "".join(chars)


SPECIAL_CHARS = "!@#$%^&*()-_=+[]{};:'\",.<>/?~`|\\ "

_OFFSET_FIELD = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def generate_slug(rng: random.Random, length: int) -> str:
    word = rng.choice(_SLUG_WORDS)
    second = rng.choice(_SLUG_WORDS)
    tail = str(rng.randint(0, 99)) if rng.random() < 0.7 else ""
    slug = f"{word}-{second}{tail}"
    if len(slug) > length:
        slug = slug[:length].rstrip("-")
    while len(slug) < length:  # pad with safe alphanumerics
        slug += rng.choice(string.ascii_lowercase + string.digits)
    return slug[:length] if len(slug) > length else slug


# ---------------------------------------------------------------------------
# Handlers — legacy set (behavior preserved).
# ---------------------------------------------------------------------------


def _h_uuid(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    # uuid4() uses os.urandom and cannot be seeded; derive from the run RNG
    # so --seed reproduces UUIDs too.
    return str(uuid_module.UUID(int=ctx.rng.getrandbits(128), version=4))


def _h_timestamp(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    offset = _offset_from_args(content, args) or timedelta()
    return str(int((ctx.now() + offset).timestamp()))


def _h_bool(content: str, args: ArgsDict, ctx: ResolveContext) -> bool:
    return ctx.rng.choice([True, False])


def _h_ip(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    rng = ctx.rng
    return ".".join(str(rng.randint(0, 255)) for _ in range(4))


def _h_email(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return generate_random_email(
        ctx.rng,
        prefix=args.get("prefix", "Human"),
        length=_as_int_legacy(args, "length", 5, token=content),
        domains=args.get("domains", "gmail.com").split("*"),
    )


def _h_number(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return generate_random_number(
        ctx.rng,
        start=args.get("start", "019"),
        length=_as_int_legacy(args, "length", 11, token=content),
    )


def _make_string_handler() -> Callable[[str, ArgsDict, ResolveContext], str]:
    def handler(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
        return generate_random_string(ctx.rng, _as_int_legacy(args, "length", 8, token=content))

    return handler


def _h_int(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    minimum = _as_int_legacy(args, "min", 1, token=content)
    maximum = _as_int_legacy(args, "max", 100, token=content)
    return ctx.rng.randint(minimum, maximum)


def _h_float(content: str, args: ArgsDict, ctx: ResolveContext) -> float:
    minimum = _as_float_legacy(args, "min", 0.0, token=content)
    maximum = _as_float_legacy(args, "max", 1.0, token=content)
    precision = _as_int_legacy(args, "precision", 2, token=content)
    return round(ctx.rng.uniform(minimum, maximum), precision)


def _h_password(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return generate_password(
        ctx.rng,
        length=_as_int_legacy(args, "length", 8, token=content),
        uppercase=_as_bool(args.get("uppercase", "true")),
        lowercase=_as_bool(args.get("lowercase", "true")),
        digits=_as_bool(args.get("digits", "true")),
        symbols=_as_bool(args.get("symbols", "false")),
    )


def _h_pick_line(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return pick_line(ctx, args.get("file"), ctx.rng)


def _h_choice(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    match = CHOICE_PATTERN.search(content)
    if not match:
        # Legacy behavior: leave the token untouched when options are absent.
        return f"{{{content}}}"
    options = [item.strip() for item in match.group(1).split(",")]
    return ctx.rng.choice(options)


def _h_date(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    fmt = args.get("format", "%Y-%m-%d")
    moment = _base_now(ctx, args, content)
    try:
        return moment.strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise TemplateResolutionError(
            f"Invalid date format {fmt!r}: {exc}",
            token=content,
            hint="use Python datetime format codes, e.g. %Y-%m-%d",
        ) from exc


def _h_datetime(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    fmt = args.get("format", "%Y-%m-%dT%H:%M:%S")
    moment = _base_now(ctx, args, content)
    try:
        return moment.strftime(fmt)
    except (ValueError, TypeError) as exc:
        raise TemplateResolutionError(
            f"Invalid datetime format {fmt!r}: {exc}",
            token=content,
            hint="use Python datetime format codes, e.g. %Y-%m-%dT%H:%M:%S",
        ) from exc


def _h_unix(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    moment = _base_now(ctx, args, content)
    return int(moment.timestamp())


# ---------------------------------------------------------------------------
# Handlers — new API-testing set.
# ---------------------------------------------------------------------------


def _h_null(content: str, args: ArgsDict, ctx: ResolveContext) -> None:
    return None


def _h_hex(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 16, lo=1, hi=MAX_STRING_LENGTH, token=content)
    return generate_hex(ctx.rng, length)


def _h_digits(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 6, lo=1, hi=MAX_STRING_LENGTH, token=content)
    return "".join(ctx.rng.choice(string.digits) for _ in range(length))


_CASE_MAP = {
    "mixed": string.ascii_letters,
    "lower": string.ascii_lowercase,
    "upper": string.ascii_uppercase,
}


def _letters_problem(content: str, args: ArgsDict) -> str | None:
    problem = _check_length_arg(content, args)
    if problem is not None:
        return problem
    case = args.get("case", "mixed")
    if case not in _CASE_MAP:
        return f"Argument 'case' must be one of: mixed, lower, upper (got {case!r})"
    return None


def _h_letters(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 10, lo=1, hi=MAX_STRING_LENGTH, token=content)
    case = args.get("case", "mixed")
    if case not in _CASE_MAP:
        raise TemplateResolutionError(
            f"Argument 'case' must be one of: mixed, lower, upper (got {case!r})",
            token=content,
        )
    charset = _CASE_MAP[case]
    return "".join(ctx.rng.choice(charset) for _ in range(length))


def _h_alphanumeric(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 12, lo=1, hi=MAX_STRING_LENGTH, token=content)
    charset = string.ascii_letters + string.digits
    return "".join(ctx.rng.choice(charset) for _ in range(length))


def _h_slug(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 12, lo=1, hi=1000, token=content)
    return generate_slug(ctx.rng, length)


USERNAME_MIN = 6  # "user_" prefix plus at least one character


def _h_username(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 10, lo=USERNAME_MIN, hi=100, token=content)
    charset = string.ascii_letters + string.digits
    tail = "".join(ctx.rng.choice(charset) for _ in range(length - len("user_")))
    return f"user_{tail}"


def _h_token(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 32, lo=1, hi=MAX_TOKEN_LENGTH, token=content)
    if ctx.seeded:
        # Deterministic mode (--seed): reproducibility beats cryptographic
        # strength. Values come from the seeded run RNG like everything else.
        alphabet = string.ascii_letters + string.digits
        return "".join(ctx.rng.choice(alphabet) for _ in range(length))
    # No seed requested: use OS-grade randomness for auth-style tokens.
    return secrets.token_hex((length + 1) // 2)[:length]


def _h_otp(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 6, lo=1, hi=18, token=content)
    return "".join(ctx.rng.choice(string.digits) for _ in range(length))


def _h_ipv6(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return ":".join(f"{ctx.rng.getrandbits(16):x}" for _ in range(8))


def _port_problem(content: str, args: ArgsDict) -> str | None:
    for key in ("min", "max"):
        problem = _int_error(key, args.get(key), 1, 65535)
        if problem is not None:
            return problem
    if args.get("min") and args.get("max") and int(args["min"]) > int(args["max"]):
        return "Argument 'min' must be less than or equal to 'max'."
    return None


def _h_port(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    minimum = _require_int(args, "min", 1, lo=1, hi=65535, token=content)
    maximum = _require_int(args, "max", 65535, lo=1, hi=65535, token=content)
    if minimum > maximum:
        raise TemplateResolutionError(
            "Argument 'min' must be less than or equal to 'max'.", token=content
        )
    return ctx.rng.randint(minimum, maximum)


def _h_user_agent(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return str(ctx.faker.get().user_agent())


def _percent_problem(content: str, args: ArgsDict) -> str | None:
    return _int_error("precision", args.get("precision"), 0, 6)


def _h_percent(content: str, args: ArgsDict, ctx: ResolveContext) -> int | float:
    raw_precision = args.get("precision")
    if raw_precision is None:
        return ctx.rng.randint(0, 100)
    precision = int(raw_precision)
    return round(ctx.rng.uniform(0, 100), precision)


def _float_pair(content: str, args: ArgsDict) -> tuple[float, float, int]:
    minimum = _as_float_legacy(args, "min", 0.0, token=content)
    maximum = _as_float_legacy(args, "max", 100.0, token=content)
    precision = _as_int_legacy(args, "precision", 2, token=content)
    return minimum, maximum, precision


def _price_problem(content: str, args: ArgsDict) -> str | None:
    try:
        minimum = float(args["min"]) if "min" in args else 0.0
        maximum = float(args["max"]) if "max" in args else 100.0
    except ValueError as exc:
        return str(exc)
    if minimum < 0:
        return "Argument 'min' must be >= 0."
    if minimum > maximum:
        return "Argument 'min' must be less than or equal to 'max'."
    return _int_error("precision", args.get("precision"), 0, 6)


def _h_price(content: str, args: ArgsDict, ctx: ResolveContext) -> float:
    minimum, maximum, precision = _float_pair(content, args)
    if minimum < 0:
        raise TemplateResolutionError("Argument 'min' must be >= 0.", token=content)
    if minimum > maximum:
        raise TemplateResolutionError(
            "Argument 'min' must be less than or equal to 'max'.", token=content
        )
    return round(ctx.rng.uniform(minimum, maximum), precision)


def _h_negative(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    return -ctx.rng.randint(1, 1_000_000)


def _h_positive(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    return ctx.rng.randint(1, 1_000_000)


def _h_zero(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    return 0


def _h_empty_string(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    return ""


def _h_whitespace(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 5, lo=0, hi=MAX_STRING_LENGTH, token=content)
    return " " * length


def _h_unicode(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 10, lo=1, hi=MAX_STRING_LENGTH, token=content)
    return generate_unicode_string(ctx.rng, length)


def _h_special_chars(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 10, lo=1, hi=MAX_STRING_LENGTH, token=content)
    return "".join(ctx.rng.choice(SPECIAL_CHARS) for _ in range(length))


def _h_long_string(content: str, args: ArgsDict, ctx: ResolveContext) -> str:
    length = _require_int(args, "length", 1000, lo=1, hi=LONG_STRING_MAX, token=content)
    pattern = "Lorem ipsum dolor sit amet consectetur adipiscing elit "
    repeats = length // len(pattern) + 1
    return (pattern * repeats)[:length]


def _h_large_int(content: str, args: ArgsDict, ctx: ResolveContext) -> int:
    digits = _require_int(args, "digits", 20, lo=1, hi=MAX_LARGE_INT_DIGITS, token=content)
    first = ctx.rng.choice("123456789")
    rest = "".join(ctx.rng.choice(string.digits) for _ in range(digits - 1))
    return int(first + rest)


def _h_list(content: str, args: ArgsDict, ctx: ResolveContext) -> list:
    match = LIST_PATTERN.match(content.strip())
    if match is None:
        raise TemplateResolutionError(
            "list requires item=<placeholder> and length=<n>",
            token=content,
            hint="{list(item=int(min=1,max=100), length=5)}",
        )
    item_expr = match.group("item").strip()
    if "{" in item_expr or "}" in item_expr:
        raise TemplateResolutionError(
            "list item expressions must not contain braces "
            "(write item=faker.word or item=int(min=1,max=9), not item={faker.word})",
            token=content,
        )
    length = int(match.group("length"))
    if not 1 <= length <= MAX_LIST_LENGTH:
        raise TemplateResolutionError(
            f"Argument 'length' must be an integer between 1 and {MAX_LIST_LENGTH}.",
            token=content,
        )
    if ctx.resolver is None:  # pragma: no cover - resolver always wires itself
        raise TemplateResolutionError("list() unavailable outside resolution", token=content)
    if ctx.list_depth >= MAX_LIST_NESTING:
        raise TemplateResolutionError(
            f"list nesting deeper than {MAX_LIST_NESTING} levels", token=content
        )
    results: list[object] = []
    ctx.list_depth += 1
    try:
        for _ in range(length):
            results.append(ctx.resolver.resolve_string("{" + item_expr + "}"))
    finally:
        ctx.list_depth -= 1
    return results


# ---------------------------------------------------------------------------
# Registration.
# ---------------------------------------------------------------------------


def register_builtins(registry: PlaceholderRegistry) -> None:
    """Register every built-in in dispatch order.

    Order matters: prefix matching walks entries top-down, so the legacy
    chain keeps its original relative order and ``datetime`` sits before
    ``date`` (otherwise ``{datetime}`` would be swallowed by ``date``).
    """
    from blaze_hammer.templating.registry import PlaceholderSpec

    exact = [
        PlaceholderSpec(
            keyword="uuid",
            exact=True,
            syntax="{uuid}",
            description="Random UUIDv4 (seed-aware).",
            example="230439fe-6a38-4c1e-8f00-bfbedf05743",
        ),
        PlaceholderSpec(
            keyword="bool",
            exact=True,
            syntax="{bool}",
            description="Random boolean. Native JSON true/false.",
            returns="boolean",
            native=True,
            example="{bool} -> true",
        ),
        PlaceholderSpec(
            keyword="ip",
            exact=True,
            syntax="{ip}",
            description="Random IPv4 address (seed-aware). Alias of {ipv4}.",
            returns="string",
            example="{ip} -> 248.86.114.112",
        ),
        PlaceholderSpec(
            keyword="null",
            exact=True,
            syntax="{null}",
            description="Real JSON null value.",
            returns="null",
            native=True,
            example="{null} -> null",
        ),
        PlaceholderSpec(
            keyword="empty_string",
            exact=True,
            syntax="{empty_string}",
            description="Empty string for required-field validation.",
            returns="string",
            example='{empty_string} -> ""',
        ),
        PlaceholderSpec(
            keyword="ipv4",
            exact=True,
            syntax="{ipv4}",
            description="Random valid IPv4 address.",
            returns="string",
            example="{ipv4} -> 192.168.42.17",
        ),
        PlaceholderSpec(
            keyword="ipv6",
            exact=True,
            syntax="{ipv6}",
            description="Random valid IPv6 address.",
            returns="string",
            example="{ipv6} -> 2001:db8::8a2e:370:7334",
        ),
        PlaceholderSpec(
            keyword="user_agent",
            exact=True,
            syntax="{user_agent}",
            description="Realistic User-Agent header via Faker.",
            returns="string",
            example="{user_agent} -> Mozilla/5.0 ...",
        ),
        PlaceholderSpec(
            keyword="zero",
            exact=True,
            syntax="{zero}",
            description="Numeric zero for boundary tests.",
            returns="integer",
            native=True,
            example="{zero} -> 0",
        ),
        PlaceholderSpec(
            keyword="positive",
            exact=True,
            syntax="{positive}",
            description="Random positive integer (1..1,000,000).",
            returns="integer",
            native=True,
            example="{positive} -> 48213",
        ),
        PlaceholderSpec(
            keyword="negative",
            exact=True,
            syntax="{negative}",
            description="Random negative integer (-1,000,000..-1).",
            returns="integer",
            native=True,
            example="{negative} -> -48213",
        ),
    ]
    prefix = [
        # Legacy {timestamp} was exact-matched; it moves to the front of the
        # prefix chain so it can accept offset= without shadowing anything
        # (no legacy keyword starts with "timestamp").
        PlaceholderSpec(
            keyword="timestamp",
            syntax="{timestamp(offset=-1h)}",
            description="UNIX timestamp in seconds; optional relative offset.",
            arguments="offset  e.g. -1h, +30m (units s/m/h/d/w)",
            default="offset: none",
            returns="string",
            example="{timestamp(offset=-1h)} -> 1719296400",
            validator=_offset_problem_for,
        ),
        # --- legacy chain (original order preserved) ------------------------
        PlaceholderSpec(
            keyword="unix",
            syntax="{unix(offset=-1h)}",
            description="UNIX timestamp as a native integer; optional offset.",
            arguments="offset  e.g. -1h, +30m (units s/m/h/d/w)",
            default="offset: none",
            returns="integer",
            native=True,
            example="{unix} -> 1719300000",
            validator=_offset_problem_for,
        ),
        PlaceholderSpec(
            keyword="email",
            syntax="{email(prefix=user_, length=10, domains=gmail.com*hotmail.com)}",
            description="Random e-mail; domains separated by '*'.",
            arguments="prefix  string\nlength  positive integer\ndomains  '*'-separated list",
            default="prefix: Human, length: 5, domains: gmail.com",
            returns="string",
            example="user_szQKmHblxH@yahoo.com",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="number",
            syntax="{number(start=019, length=11)}",
            description="Digit string with fixed prefix (leading zeros kept).",
            arguments="start  digit prefix\nlength  total digits",
            default="start: 019, length: 11",
            returns="string",
            example="08019677871",
            validator=_number_validator,
        ),
        PlaceholderSpec(
            keyword="str",
            syntax="{str(length=16)}",
            description="Random alphanumeric string ('{string(...)}' also works).",
            arguments="length  positive integer",
            default="length: 8",
            returns="string",
            example="tFL4U1Y6tqFe9jaa",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="string",
            syntax="{string(length=16)}",
            description="Alias of {str(...)}.",
            arguments="length  positive integer",
            default="length: 8",
            returns="string",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="int",
            syntax="{int(min=10, max=100)}",
            description="Random integer in [min, max]. Native type when alone.",
            arguments="min  integer\nmax  integer",
            default="min: 1, max: 100",
            returns="integer",
            native=True,
            example="{int(min=1, max=100)} -> 42",
            validator=_int_range_validator,
        ),
        PlaceholderSpec(
            keyword="float",
            syntax="{float(min=1, max=5, precision=1)}",
            description="Random float rounded to precision. Native when alone.",
            arguments="min  number\nmax  number\nprecision  integer 0-6",
            default="min: 0, max: 1, precision: 2",
            returns="number",
            native=True,
            example="{float(min=1, max=5, precision=1)} -> 3.5",
            validator=_float_validator,
        ),
        PlaceholderSpec(
            keyword="password",
            syntax=(
                "{password(length=10, digits=true, uppercase=true, lowercase=false, symbols=false)}"
            ),
            description="Random password from enabled character sets.",
            arguments="length  integer\nuppercase/lowercase/digits/symbols  true/false",
            default="length: 8, all sets on except symbols",
            returns="string",
            example="K7WMJZDbQx",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="pick_line",
            syntax="{pick_line(file=path/to/file.txt)}",
            description="Random line from a text file (cached per run).",
            arguments="file  path to an existing UTF-8 text file",
            returns="string",
            example="{pick_line(file=words.txt)} -> third line",
            validator=_pick_line_validator,
        ),
        PlaceholderSpec(
            keyword="choice",
            syntax="{choice(hey, hi, bye)}",
            description="Random item from comma-separated options.",
            arguments="options  comma-separated values inside choice(...)",
            returns="string",
            example="{choice(red, green, blue)} -> blue",
            validator=_choice_validator,
        ),
        PlaceholderSpec(
            keyword="datetime",
            syntax="{datetime(offset=-7d, format=%Y-%m-%dT%H:%M:%S)}",
            description="Full datetime; supports relative offsets.",
            arguments="offset  e.g. -7d, +30m\nformat  strftime codes",
            default="format: %Y-%m-%dT%H:%M:%S",
            returns="string",
            example="2026-08-25T14:03:00",
            validator=_offset_validator,
        ),
        PlaceholderSpec(
            keyword="date",
            syntax="{date(offset=-7d, format=%A %d %B %Y)}",
            description="Date via strftime codes; supports relative offsets.",
            arguments="offset  e.g. -7d, +30d\nformat  strftime codes",
            default="format: %Y-%m-%d",
            returns="string",
            example="2026-08-25",
            validator=_offset_validator,
        ),
        # --- new API-testing set (cannot shadow any keyword above) ----------
        PlaceholderSpec(
            keyword="hex",
            syntax="{hex(length=16)}",
            description="Lowercase hexadecimal string.",
            arguments="length  positive integer",
            default="length: 16",
            returns="string",
            example="a8f31c92de71ab44",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="digits",
            syntax="{digits(length=6)}",
            description="Exactly N numeric digits; leading zeros preserved.",
            arguments="length  positive integer",
            default="length: 6",
            returns="string",
            example="048392",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="letters",
            syntax="{letters(length=10, case=mixed)}",
            description="Alphabetic characters; case: mixed/lower/upper.",
            arguments="length  positive integer\ncase  mixed|lower|upper",
            default="length: 10, case: mixed",
            returns="string",
            example="jKxPqLmZta",
            validator=_letters_validator,
        ),
        PlaceholderSpec(
            keyword="alphanumeric",
            syntax="{alphanumeric(length=12)}",
            description="Letters and numbers.",
            arguments="length  positive integer",
            default="length: 12",
            returns="string",
            example="8xK29mQaP1zL",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="slug",
            syntax="{slug(length=12)}",
            description="URL-safe lowercase slug (letters, digits, hyphens).",
            arguments="length  positive integer",
            default="length: 12",
            returns="string",
            example="hello-world8",
            validator=lambda c, a: _check_length_arg(c, a, "length", hi=1000),
        ),
        PlaceholderSpec(
            keyword="username",
            syntax="{username(length=10)}",
            description="'user_'-prefixed test username.",
            arguments="length  integer 6-100",
            default="length: 10",
            returns="string",
            example="user_8xK29mQ",
            validator=lambda c, a: _check_length_arg(c, a, "length", hi=100),
        ),
        PlaceholderSpec(
            keyword="token",
            syntax="{token(length=32)}",
            description=(
                "Auth-style token. Without --seed uses cryptographic "
                "randomness; with --seed becomes deterministic (documented "
                "trade-off, see README)."
            ),
            arguments="length  integer 1-1024",
            default="length: 32",
            returns="string",
            example="9f86d081884c7d659a2f...",
            validator=lambda c, a: _check_length_arg(c, a, "length", hi=MAX_TOKEN_LENGTH),
        ),
        PlaceholderSpec(
            keyword="otp",
            syntax="{otp(length=6)}",
            description="Numeric OTP-style value; leading zeros preserved.",
            arguments="length  integer 1-18",
            default="length: 6",
            returns="string",
            example="049218",
            validator=lambda c, a: _check_length_arg(c, a, "length", hi=18),
        ),
        PlaceholderSpec(
            keyword="port",
            syntax="{port(min=1024, max=65535)}",
            description="TCP/UDP port number. Native integer.",
            arguments="min  1-65535\nmax  1-65535",
            default="min: 1, max: 65535",
            returns="integer",
            native=True,
            example="{port} -> 43821",
            validator=_port_problem,
        ),
        PlaceholderSpec(
            keyword="percent",
            syntax="{percent(precision=2)}",
            description="0-100 value; integer unless precision is given.",
            arguments="precision  integer 0-6",
            default="precision: none (integer result)",
            returns="integer|number",
            native=True,
            example="{percent} -> 73",
            validator=_percent_problem,
        ),
        PlaceholderSpec(
            keyword="price",
            syntax="{price(min=10, max=500, precision=2)}",
            description="Price-like non-negative float. Native number.",
            arguments="min  number >= 0\nmax  number\nprecision  integer 0-6",
            default="min: 0, max: 100, precision: 2",
            returns="number",
            native=True,
            example="{price(min=10, max=500)} -> 149.99",
            validator=_price_problem,
        ),
        PlaceholderSpec(
            keyword="large_int",
            syntax="{large_int(digits=20)}",
            description="Very large integer for numeric boundary tests.",
            arguments="digits  integer 1-1000",
            default="digits: 20",
            returns="integer",
            native=True,
            example="{large_int(digits=20)} -> 48392048102938475619",
            validator=lambda c, a: _check_length_arg(c, a, "digits", hi=MAX_LARGE_INT_DIGITS),
        ),
        PlaceholderSpec(
            keyword="whitespace",
            syntax="{whitespace(length=5)}",
            description="Exactly N space characters.",
            arguments="length  integer 0-100000",
            default="length: 5",
            returns="string",
            example='"{whitespace(length=3)}" -> "   "',
            validator=lambda c, a: _int_error("length", a.get("length"), 0, MAX_STRING_LENGTH),
        ),
        PlaceholderSpec(
            keyword="unicode",
            syntax="{unicode(length=10)}",
            description="Multi-script Unicode sample data (surrogate-safe).",
            arguments="length  positive integer",
            default="length: 10",
            returns="string",
            example="héllo世界🎉",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="special_chars",
            syntax="{special_chars(length=10)}",
            description="Special characters for input-validation testing.",
            arguments="length  positive integer",
            default="length: 10",
            returns="string",
            example="!@#$%^&*()",
            validator=lambda c, a: _check_length_arg(c, a, "length"),
        ),
        PlaceholderSpec(
            keyword="long_string",
            syntax="{long_string(length=1000)}",
            description=f"Exactly N filler characters (max {LONG_STRING_MAX}).",
            arguments=f"length  integer 1-{LONG_STRING_MAX}",
            default="length: 1000",
            returns="string",
            example="Lorem ipsum dolor sit amet ...",
            validator=lambda c, a: _check_length_arg(c, a, "length", hi=LONG_STRING_MAX),
        ),
        PlaceholderSpec(
            keyword="list",
            syntax="{list(item=int(min=1,max=100), length=5)}",
            description="Native JSON array by resolving item repeatedly.",
            arguments=(
                "item  placeholder expression (built-ins or faker; nested "
                "list() allowed up to depth 3)\nlength  integer 1-1000"
            ),
            default="length: 5",
            returns="array",
            native=True,
            example="[42, 17, 83, 4, 91]",
            validator=_list_validator,
        ),
    ]
    handlers: dict[str, Handler] = {
        "uuid": _h_uuid,
        "timestamp": _h_timestamp,
        "bool": _h_bool,
        "ip": _h_ip,
        "null": _h_null,
        "empty_string": _h_empty_string,
        "ipv4": _h_ip,
        "ipv6": _h_ipv6,
        "user_agent": _h_user_agent,
        "zero": _h_zero,
        "positive": _h_positive,
        "negative": _h_negative,
        "email": _h_email,
        "number": _h_number,
        "str": _make_string_handler(),
        "string": _make_string_handler(),
        "int": _h_int,
        "float": _h_float,
        "password": _h_password,
        "pick_line": _h_pick_line,
        "choice": _h_choice,
        "datetime": _h_datetime,
        "date": _h_date,
        "unix": _h_unix,
        "hex": _h_hex,
        "digits": _h_digits,
        "letters": _h_letters,
        "alphanumeric": _h_alphanumeric,
        "slug": _h_slug,
        "username": _h_username,
        "token": _h_token,
        "otp": _h_otp,
        "port": _h_port,
        "percent": _h_percent,
        "price": _h_price,
        "large_int": _h_large_int,
        "whitespace": _h_whitespace,
        "unicode": _h_unicode,
        "special_chars": _h_special_chars,
        "long_string": _h_long_string,
        "list": _h_list,
    }
    for spec in exact:
        registry.register(spec, handlers[spec.keyword])
    for spec in prefix:
        registry.register(spec, handlers[spec.keyword])


# ---------------------------------------------------------------------------
# Validators used by pre-flight checks (mirrors runtime coercion exactly).
# ---------------------------------------------------------------------------


def _int_range_validator(content: str, args: ArgsDict) -> str | None:
    try:
        int(args.get("min", 1))
        int(args.get("max", 100))
    except ValueError as exc:
        return str(exc)
    return None


def _float_validator(content: str, args: ArgsDict) -> str | None:
    try:
        float(args.get("min", 0.0))
        float(args.get("max", 1.0))
        int(args.get("precision", 2))
    except ValueError as exc:
        return str(exc)
    return None


def _number_validator(content: str, args: ArgsDict) -> str | None:
    length_raw = args.get("length", "11")
    try:
        length = int(length_raw)
    except ValueError:
        return f"Invalid integer for 'length': {length_raw!r}"
    start = args.get("start", "019")
    if len(start) > length:
        return f"'start' ({start!r}) is longer than 'length' ({length})"
    return None


def _pick_line_validator(content: str, args: ArgsDict) -> str | None:
    file_path = args.get("file")
    if not file_path:
        return "pick_line requires a 'file' argument"
    try:
        with open(file_path, encoding="utf-8") as handle:
            if not handle.readline():
                return f"pick_line file '{file_path}' is empty"
    except OSError as exc:
        return f"pick_line cannot read '{file_path}': {exc}"
    return None


def _choice_validator(content: str, args: ArgsDict) -> str | None:
    if not CHOICE_PATTERN.search(content):
        return "choice requires options, e.g. {choice(a, b, c)}"
    return None


def _offset_validator(content: str, args: ArgsDict) -> str | None:
    problem = _offset_problem_for(content, args)
    if problem is not None:
        return problem
    fmt = args.get("format")
    if fmt is not None:
        try:
            datetime.now().strftime(fmt)
        except (ValueError, TypeError) as exc:
            return f"Invalid format {fmt!r}: {exc}"
    return None


def _letters_validator(content: str, args: ArgsDict) -> str | None:
    return _letters_problem(content, args)


def _list_validator(content: str, args: ArgsDict) -> str | None:
    match = LIST_PATTERN.match(content.strip())
    if match is None:
        return (
            "list requires item=<placeholder> and length=<n>, "
            "e.g. {list(item=int(min=1,max=100), length=5)}"
        )
    length = int(match.group("length"))
    if not 1 <= length <= MAX_LIST_LENGTH:
        return f"Argument 'length' must be an integer between 1 and {MAX_LIST_LENGTH}."
    item_expr = match.group("item").strip()
    if "{" in item_expr or "}" in item_expr:
        return (
            "list item expressions must not contain braces "
            "(write item=faker.word, not item={faker.word})"
        )
    return None
