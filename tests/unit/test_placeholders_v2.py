"""Comprehensive tests for the expanded built-in placeholder set."""

from __future__ import annotations

import json
import random
import re
import string

import pytest

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.templating import (
    FakerFactory,
    ResolveContext,
    TemplateResolver,
    TemplateValidator,
    build_default_registry,
)
from blaze_hammer.templating.builtins import (
    LONG_STRING_MAX,
    MAX_LIST_LENGTH,
    MAX_STRING_LENGTH,
)


@pytest.fixture()
def resolver():
    def _make(seed: int | None = 42, seeded: bool = True) -> TemplateResolver:
        registry = build_default_registry()
        rng = random.Random(seed)
        ctx = ResolveContext(rng=rng, faker=FakerFactory(rng), seeded=seeded)
        return TemplateResolver(registry, ctx)

    return _make


def _validate(text: str):
    validator = TemplateValidator(build_default_registry())
    return validator.validate({"payload.json": ({"raw": text}, text)})


# -- booleans / null ----------------------------------------------------------


def test_bool(resolver):
    value = resolver().resolve_string("{bool}")
    assert isinstance(value, bool)


def test_bool_json_serializes_natively(resolver):
    payload = resolver().resolve_obj({"active": "{bool}"})
    text = json.dumps(payload)
    assert '"active": true' in text or '"active": false' in text


def test_null(resolver):
    assert resolver().resolve_string("{null}") is None
    payload = resolver().resolve_obj({"middle_name": "{null}"})
    assert json.dumps(payload) == '{"middle_name": null}'


def test_embedded_bool_is_json_style_text(resolver):
    value = resolver().resolve_string("flag={bool}!")
    assert value in ("flag=true!", "flag=false!")


# -- random strings ------------------------------------------------------------


def test_hex(resolver):
    value = resolver().resolve_string("{hex(length=16)}")
    assert len(value) == 16
    assert all(ch in "0123456789abcdef" for ch in value)


def test_hex_default_length(resolver):
    assert len(resolver().resolve_string("{hex}")) == 16


def test_digits_leading_zeros_preserved(resolver):
    for _ in range(20):
        value = resolver().resolve_string("{digits(length=6)}")
        assert len(value) == 6 and value.isdigit()


def test_letters_case_control(resolver):
    upper = resolver().resolve_string("{letters(length=12, case=upper)}")
    lower = resolver().resolve_string("{letters(length=12, case=lower)}")
    mixed = resolver().resolve_string("{letters(length=12)}")
    assert upper.isalpha() and upper.isupper()
    assert lower.isalpha() and lower.islower()
    assert mixed.isalpha()


def test_letters_invalid_case_rejected(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{letters(length=4, case=weird)}")


def test_alphanumeric(resolver):
    value = resolver().resolve_string("{alphanumeric(length=12)}")
    assert len(value) == 12 and value.isalnum()


# -- identifiers ----------------------------------------------------------------


def test_slug_url_safe(resolver):
    for _ in range(10):
        value = resolver().resolve_string("{slug(length=14)}")
        assert re.fullmatch(r"[a-z0-9-]{14}", value), value


def test_username_prefixed_and_seeded(resolver):
    first = resolver(5).resolve_string("{username(length=10)}")
    second = resolver(5).resolve_string("{username(length=10)}")
    third = resolver(6).resolve_string("{username(length=10)}")
    assert first.startswith("user_") and len(first) == 10
    assert first == second and first != third


def test_token_deterministic_with_seed(resolver):
    a = resolver(seed=123, seeded=True).resolve_string("{token(length=32)}")
    b = resolver(seed=123, seeded=True).resolve_string("{token(length=32)}")
    c = resolver(seed=124, seeded=True).resolve_string("{token(length=32)}")
    assert a == b and len(a) == 32
    assert a != c


def test_token_unseeded_varies_across_instances():
    registry = build_default_registry()

    def make() -> str:
        ctx = ResolveContext(rng=random.Random(), faker=FakerFactory(random.Random()))
        return TemplateResolver(registry, ctx).resolve_string("{token(length=32)}")

    tokens = {make() for _ in range(5)}
    assert len(tokens) == 5  # cryptographic mode: fresh values every time


def test_otp_length_and_leading_zeros(resolver):
    values = {resolver().resolve_string("{otp(length=6)}") for _ in range(30)}
    for value in values:
        assert len(value) == 6 and value.isdigit()


# -- network ----------------------------------------------------------------------


def test_ipv4(resolver):
    value = resolver().resolve_string("{ipv4}")
    parts = value.split(".")
    assert len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    # legacy alias still works
    assert resolver().resolve_string("{ip}").count(".") == 3


def test_ipv6(resolver):
    value = resolver().resolve_string("{ipv6}")
    groups = value.split(":")
    assert len(groups) == 8
    for group in groups:
        assert 0 <= int(group, 16) <= 0xFFFF


def test_port_range_and_args(resolver):
    value = resolver().resolve_string("{port}")
    assert 1 <= value <= 65535
    pinned = resolver().resolve_string("{port(min=1024, max=1024)}")
    assert pinned == 1024


def test_port_invalid_min_max(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{port(min=99999)}")
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{port(min=5000, max=1000)}")


def test_user_agent(resolver):
    value = resolver().resolve_string("{user_agent}")
    assert value.startswith(("Mozilla/5.0", "Mozilla/4.0")) or "compatible" in value


# -- date/time ---------------------------------------------------------------------


def test_datetime_offset(resolver):
    base = resolver().resolve_string("{datetime(offset=-7d)}")
    plain = resolver().resolve_string("{datetime}")
    assert "T" in base and "-" in base
    assert base != plain  # offsets move the timestamp


def test_timestamp_offset_stays_string_legacy(resolver):
    value = resolver().resolve_string("{timestamp(offset=-1h)}")
    assert value.isdigit()  # legacy {timestamp} remains a digit string
    assert abs(int(value) - resolver().resolve_obj({"u": "{unix}"})["u"]) < 3700


def test_unix_native_int_and_offset(resolver):
    value = resolver().resolve_string("{unix}")
    assert isinstance(value, int)
    shifted = resolver().resolve_string("{unix(offset=-1h)}")
    assert 3500 < value - shifted < 3700


def test_empty_offset_argument_rejected(resolver):
    for template in ("{timestamp(offset=)}", "{unix(offset=)}", "{date(offset=)}"):
        with pytest.raises(TemplateResolutionError):
            resolver().resolve_string(template)
        report = _validate(template)
        assert not report.ok


def test_date_offset_days_and_weeks(resolver):
    from datetime import timedelta

    class Fixed:
        @staticmethod
        def now():
            from datetime import datetime as dt

            return dt(2026, 8, 25, 12, 0, 0)

    registry = build_default_registry()
    rng = random.Random(1)
    ctx = ResolveContext(rng=rng, faker=FakerFactory(rng), now=Fixed.now)
    r = TemplateResolver(registry, ctx)
    assert r.resolve_string("{date(offset=-7d)}") == "2026-08-18"
    assert r.resolve_string("{date(offset=+2w)}") == "2026-09-08"
    _ = timedelta


def test_malformed_offsets_fail_everywhere(resolver):
    for template in ("{date(offset=-7q)}", "{datetime(offset=x)}", "{timestamp(offset=)}"):
        with pytest.raises(TemplateResolutionError):
            resolver().resolve_string(template)
        report = _validate(template)
        assert not report.ok


# -- numbers -------------------------------------------------------------------------


def test_percent_integer_by_default_float_with_precision(resolver):
    assert isinstance(resolver().resolve_string("{percent}"), int)
    value = resolver().resolve_string("{percent(precision=2)}")
    assert isinstance(value, float) and 0 <= value <= 100


def test_price_bounds_precision_type(resolver):
    value = resolver().resolve_string("{price(min=10, max=500, precision=2)}")
    assert isinstance(value, float) and 10 <= value <= 500


def test_price_validation(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{price(min=100, max=10)}")
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{price(min=-5)}")


def test_negative_positive_zero_types(resolver):
    out = resolver().resolve_obj({"n": "{negative}", "p": "{positive}", "z": "{zero}"})
    assert out["n"] < 0 and out["p"] > 0 and out["z"] == 0
    assert json.dumps(out) == json.dumps(out)  # serializable natively


# -- edge cases -----------------------------------------------------------------------


def test_empty_string():
    value = TemplateResolver(
        build_default_registry(),
        ResolveContext(rng=random.Random(1), faker=FakerFactory(random.Random(1))),
    ).resolve_string("{empty_string}")
    assert value == ""


def test_whitespace_exact_count(resolver):
    assert resolver().resolve_string("{whitespace(length=5)}") == "     "
    assert resolver().resolve_string("{whitespace(length=0)}") == ""


def test_unicode_variety_and_surrogate_safety(resolver):
    value = resolver().resolve_string("{unicode(length=64)}")
    assert len(value) == 64
    encoded = json.dumps({"v": value}, ensure_ascii=False)
    assert "\\ud" not in encoded.lower()  # no lone surrogates
    assert any(ord(ch) > 127 for ch in value)


def test_special_chars_no_control_characters(resolver):
    value = resolver().resolve_string("{special_chars(length=40)}")
    assert len(value) == 40
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def test_long_string_exact_length(resolver):
    value = resolver().resolve_string("{long_string(length=1234)}")
    assert len(value) == 1234


def test_long_string_cap_enforced(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string(f"{{long_string(length={LONG_STRING_MAX + 1})}}")
    report = _validate(f"{{long_string(length={LONG_STRING_MAX + 1})}}")
    assert not report.ok


def test_large_int_digits_and_boundaries(resolver):
    value = resolver().resolve_string("{large_int(digits=20)}")
    assert isinstance(value, int) and 10**19 <= value < 10**20
    tiny = resolver().resolve_string("{large_int(digits=1)}")
    assert 1 <= tiny <= 9
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{large_int(digits=0)}")


def test_generic_length_caps(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string(f"{{hex(length={MAX_STRING_LENGTH + 1})}}")
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{hex(length=-1)}")
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{hex(length=abc)}")


# -- collections -------------------------------------------------------------------------


def test_list_native_array_of_ints(resolver):
    value = resolver().resolve_string("{list(item=int(min=1,max=100), length=5)}")
    assert isinstance(value, list) and len(value) == 5
    assert all(isinstance(v, int) and 1 <= v <= 100 for v in value)
    assert len(set(value)) > 1  # items generated independently


def test_list_serializes_as_json_array(resolver):
    payload = resolver().resolve_obj({"tags": "{list(item=int(min=1,max=9),length=3)}"})
    text = json.dumps(payload)
    assert text.startswith('{"tags": [') and text.endswith("]}")


def test_list_supports_faker_items(resolver):
    value = resolver().resolve_string("{list(item=faker.word, length=3)}")
    assert isinstance(value, list) and len(value) == 3
    assert all(isinstance(item, str) and item for item in value)


def test_list_validation_errors():
    report = _validate("{list(length=3)}")
    assert not report.ok
    assert "item=" in report.issues[0].problem

    report = _validate("{list(item=int(min=1,max=9), length=0)}")
    assert "between 1 and" in report.issues[0].problem

    report = _validate(f"{{list(item=int(min=1,max=9), length={MAX_LIST_LENGTH + 1})}}")
    assert not report.ok


def test_list_deep_probe_catches_bad_item():
    report = _validate("{list(item=hex(length=abc),length=2)}")
    assert not report.ok
    assert "must be an integer" in report.issues[0].problem


def test_list_runtime_missing_item_raises(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{list(length=3)}")


def test_list_nested_lists_supported_up_to_depth_cap(resolver):
    """Nested list() works via recursive resolution (documented depth cap)."""
    value = resolver().resolve_string("{list(item=list(item=int(min=1,max=9),length=2),length=2)}")
    assert isinstance(value, list) and len(value) == 2
    assert all(isinstance(inner, list) and len(inner) == 2 for inner in value)


def test_list_braced_item_rejected_with_guidance(resolver):
    """Braced items would stage into junk values; unbraced form is required."""
    template = "{list(item={faker.word},length=3)}"
    report = _validate(template)
    assert not report.ok
    assert "must not contain braces" in report.issues[0].problem


def test_list_handler_direct_brace_guard():
    """Defense-in-depth: the handler itself refuses braced item expressions."""
    import re as re_module

    from blaze_hammer.templating.registry import PLACEHOLDER_PATTERN

    registry = build_default_registry()
    rng = random.Random(1)
    ctx = ResolveContext(rng=rng, faker=FakerFactory(rng))
    TemplateResolver(registry, ctx)  # wires ctx.resolver
    content = "list(item={who},length=3)"
    spec, handler = registry.match(content)
    assert spec is not None
    args = dict(re_module.findall(r"(\w+)=([^,{}()]+)", content))
    # Sanity: the full-match pattern used by the resolver cannot see this
    # construct (inner braces), which is why validation owns this rule.
    assert not re_module.fullmatch(PLACEHOLDER_PATTERN, "{" + content + "}")
    with pytest.raises(TemplateResolutionError):
        handler(content, args, ctx)


# -- type preservation & regression --------------------------------------------------------


def test_full_value_vs_embedded_typing(resolver):
    out = resolver().resolve_obj(
        {
            "id": "{int(min=1,max=100)}",
            "active": "{bool}",
            "value": "{null}",
            "name": "{str(length=10)}",
            "message": "User {username(length=8)} has ID {int(min=1,max=9)}",
        }
    )
    assert isinstance(out["id"], int)
    assert isinstance(out["active"], bool)
    assert out["value"] is None
    assert isinstance(out["name"], str)
    assert isinstance(out["message"], str)
    assert out["message"].startswith("User user_")


def test_legacy_placeholders_unchanged(resolver):
    """Regression: the original keyword set keeps its exact semantics."""
    out = resolver().resolve_obj(
        {
            "e": "{email(prefix=u_, length=6)}",
            "num": "{number(start=080, length=10)}",
            "st": "{str(length=8)}",
            "pw": "{password(length=10)}",
            "ch": "{choice(a,b)}",
            "d": "{date(format=%Y)}",
            "ts": "{timestamp}",
            "ip": "{ip}",
            "uuid": "{uuid}",
            "f": "{faker.name}",
        }
    )
    assert out["e"].startswith("u_") and "@" in out["e"]
    assert out["num"].startswith("080") and len(out["num"]) == 10
    assert len(out["st"]) == 8
    assert len(out["pw"]) == 10
    assert out["ch"] in ("a", "b")
    assert len(out["d"]) == 4
    assert out["ts"].isdigit()
    assert out["ip"].count(".") == 3
    assert out["uuid"].count("-") == 4
    assert isinstance(out["f"], str) and out["f"]


def test_unknown_token_still_verbatim_at_runtime(resolver):
    assert resolver().resolve_string("{not_a_real_token}") == "{not_a_real_token}"


def test_seed_reproducibility_across_entire_set():
    template = {
        "hex": "{hex(length=8)}",
        "digits": "{digits(length=4)}",
        "slug": "{slug(length=16)}",
        "user": "{username(length=10)}",
        "token": "{token(length=24)}",
        "otp": "{otp(length=6)}",
        "ipv6": "{ipv6}",
        "port": "{port}",
        "percent": "{percent(precision=3)}",
        "price": "{price(min=1, max=9)}",
        "large": "{large_int(digits=30)}",
        "uni": "{unicode(length=6)}",
        "chars": "{special_chars(length=6)}",
        "long": "{long_string(length=50)}",
        "list": "{list(item=int(min=1,max=50), length=4)}",
        "bool": "{bool}",
    }

    def generate(seed):
        registry = build_default_registry()
        rng = random.Random(seed)
        ctx = ResolveContext(rng=rng, faker=FakerFactory(rng), seeded=True)
        return TemplateResolver(registry, ctx).resolve_obj(dict(template))

    first = generate(777)
    second = generate(777)
    other = generate(778)
    assert first == second
    assert first != other


def test_json_round_trip_all_new_placeholders(resolver):
    payload = resolver().resolve_obj(
        {
            "b": "{bool}",
            "n": "{null}",
            "h": "{hex(length=8)}",
            "l": "{list(item=str(length=3), length=2)}",
            "p": "{price()}",
            "big": "{large_int(digits=25)}",
            "u": "{unicode(length=12)}",
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)
    assert decoded["n"] is None
    assert isinstance(decoded["b"], bool)
    assert isinstance(decoded["l"], list)
    _ = string  # keep import surface used by helpers above


# -- documentation metadata ---------------------------------------------------------------


def test_registry_metadata_complete_for_docs():
    registry = build_default_registry()
    specs = registry.specs()
    keywords = {spec.keyword for spec in specs}
    required = {
        "uuid",
        "email",
        "number",
        "str",
        "int",
        "float",
        "choice",
        "date",
        "timestamp",
        "password",
        "pick_line",
        "bool",
        "null",
        "hex",
        "digits",
        "letters",
        "alphanumeric",
        "slug",
        "username",
        "token",
        "otp",
        "ipv4",
        "ipv6",
        "port",
        "user_agent",
        "datetime",
        "unix",
        "percent",
        "price",
        "negative",
        "positive",
        "zero",
        "large_int",
        "empty_string",
        "whitespace",
        "unicode",
        "special_chars",
        "long_string",
        "list",
    }
    missing = required - keywords
    assert not missing, f"unregistered placeholders: {missing}"
    for spec in specs:
        assert spec.description, spec.keyword
        assert spec.returns, spec.keyword


def test_validators_attached_to_argument_placeholders():
    registry = build_default_registry()
    for spec in registry.specs():
        if "(" in (spec.syntax or "") and spec.keyword not in ("choice",):
            assert spec.validator is not None, f"{spec.keyword} lacks validator"
