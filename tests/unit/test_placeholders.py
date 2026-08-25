"""Placeholder resolution tests: builtins, faker bridge, env expansion."""

from __future__ import annotations

import random

import pytest

from blaze_hammer.errors import MissingEnvVarError, TemplateResolutionError
from blaze_hammer.templating import (
    FakerFactory,
    ResolveContext,
    TemplateResolver,
    build_default_registry,
)


@pytest.fixture()
def resolver(monkeypatch):
    def _make(seed: int = 42, environ=None) -> TemplateResolver:
        registry = build_default_registry()
        rng = random.Random(seed)
        ctx = ResolveContext(rng=rng, faker=FakerFactory(rng))
        return TemplateResolver(registry, ctx, environ=environ)

    return _make


def test_uuid_is_valid_and_seed_reproducible(resolver):
    first = resolver(1).resolve_string("{uuid}")
    second = resolver(1).resolve_string("{uuid}")
    third = resolver(2).resolve_string("{uuid}")
    assert len(first) == 36 and first.count("-") == 4
    assert first == second
    assert first != third


def test_exact_tokens_ip_bool_timestamp(resolver):
    out = resolver().resolve_obj({"ip": "{ip}", "flag": "{bool}", "ts": "{timestamp}"})
    assert out["ip"].count(".") == 3
    assert isinstance(out["flag"], bool)  # native JSON boolean
    assert out["ts"].isdigit()


def test_email_placeholder_args(resolver):
    value = resolver().resolve_string("{email(prefix=user_, length=10, domains=a.com*b.com)}")
    local, domain = value.split("@")
    assert local.startswith("user_") and len(local) == 15
    assert domain in ("a.com", "b.com")


def test_number_start_length(resolver):
    value = resolver().resolve_string("{number(start=080, length=10)}")
    assert len(value) == 10 and value.startswith("080")


def test_number_invalid_length_fails_validation_message(resolver):
    with pytest.raises(TemplateResolutionError):
        resolver().resolve_string("{number(start=01900, length=3)}")


def test_int_float_str_password_choice_date(resolver):
    obj = {
        "i": "{int(min=5, max=5)}",
        "f": "{float(min=1.0, max=2.0, precision=1)}",
        "s": "{str(length=20)}",
        "p": "{password(length=12, digits=false, uppercase=false)}",
        "c": "{choice(alpha, beta)}",
        "d": "{date(format=%Y)}",
    }
    out = resolver().resolve_obj(obj)
    assert out["i"] == 5  # native int: full-value placeholders keep JSON types
    assert isinstance(out["f"], float) and 1.0 <= out["f"] <= 2.0
    assert len(out["s"]) == 20
    assert len(out["p"]) == 12 and out["p"].islower()
    assert out["c"] in ("alpha", "beta")
    assert len(out["d"]) == 4


def test_embedded_placeholders_remain_strings(resolver):
    value = resolver().resolve_string("id-{int(min=7, max=7)}-on:{bool}")
    assert value.startswith("id-7-on:")
    assert value.split("-on:")[1] in ("true", "false")  # JSON-style embedding


def test_pick_line_reads_file(resolver, tmp_path):
    target = tmp_path / "lines.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    token = "{pick_line(file=" + str(target).replace("\\", "/") + ")}"
    out = resolver().resolve_string(token)
    assert out in ("one", "two", "three")


def test_unknown_token_left_verbatim(resolver):
    assert resolver().resolve_string("{definitely_not_a_placeholder}") == (
        "{definitely_not_a_placeholder}"
    )


def test_nested_structures_resolved(resolver):
    obj = {"a": [{"u": "{uuid}"}], "b": {"c": {"e": "{str(length=4)}"}}}
    out = resolver().resolve_obj(obj)
    assert "-" in out["a"][0]["u"]
    assert len(out["b"]["c"]["e"]) == 4


def test_seed_makes_full_object_generation_reproducible(resolver):
    template = {
        "u": "{uuid}",
        "e": "{email(prefix=x_, length=6)}",
        "n": "{number(start=9, length=7)}",
        "name": "{faker.name}",
        "prov": "{faker.providers.internet.email}",
    }
    assert resolver(7).resolve_obj(template) == resolver(7).resolve_obj(template)


def test_env_expansion_and_missing_error(resolver):
    environ = {"MY_TOKEN": "abc123"}
    r = resolver(environ=environ)
    assert r.resolve_string("Bearer ${MY_TOKEN}") == "Bearer abc123"
    with pytest.raises(MissingEnvVarError) as excinfo:
        resolver(environ={}).resolve_string("${NOPE_1} ${NOPE_2}")
    assert set(excinfo.value.variables) == {"NOPE_1", "NOPE_2"}


# -- faker bridge -----------------------------------------------------------


def test_faker_flat_and_provider_paths(resolver):
    out = resolver().resolve_obj({"flat": "{faker.job}", "deep": "{faker.providers.address.city}"})
    assert out["flat"] and out["deep"]


def test_faker_custom_with_locale(resolver):
    value = resolver().resolve_string("{faker.custom(field=job, locale=en_US)}")
    assert isinstance(value, str) and value


def test_faker_custom_unknown_field_raises_with_suggestion(resolver):
    with pytest.raises(TemplateResolutionError) as excinfo:
        resolver().resolve_string("{faker.custom(field=nmae)}")
    assert any("name" in s for s in excinfo.value.suggestions)


def test_faker_profile_field(resolver):
    value = resolver().resolve_string("{faker.profile(field=job)}")
    assert isinstance(value, str) and value


def test_custom_provider_auto_discovered(resolver):
    value = resolver().resolve_string("{faker.simple_example(category=greetings)}")
    assert value in ("hi", "hello", "hey", "howdy", "g'day")


def test_kwargs_typed_via_literal_eval(resolver):
    value = resolver().resolve_string("{faker.date_of_birth(minimum_age=30, maximum_age=30)}")
    from datetime import datetime

    # Native type: datetime.date object
    assert hasattr(value, "year")
    assert datetime.now().year - value.year == 30
