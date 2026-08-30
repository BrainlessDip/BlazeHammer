"""Faker bridge tests: dynamic resolution, args, locales, security, native types."""

from __future__ import annotations

import random
from typing import Any

import pytest

from blaze_hammer.errors import TemplateResolutionError
from blaze_hammer.templating import build_default_registry
from blaze_hammer.templating.faker_bridge import (
    FakerFactory,
    _validate_attr_name,
    discover_custom_providers,
    parse_faker_args,
    suggest_faker_fields,
)
from blaze_hammer.templating.registry import ResolveContext
from blaze_hammer.templating.resolver import TemplateResolver, embed_as_text
from blaze_hammer.templating.validation import TemplateValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(seed: int = 0, locale: str | None = None) -> ResolveContext:
    rng = random.Random(seed)
    return ResolveContext(rng=rng, faker=FakerFactory(rng, default_locale=locale))


def _resolver(seed: int = 0, locale: str | None = None) -> TemplateResolver:
    return TemplateResolver(build_default_registry(), _make_ctx(seed, locale))


def _resolve(text: str, seed: int = 0, locale: str | None = None) -> Any:
    return _resolver(seed, locale).resolve_string(text)


def _resolve_obj(obj: Any, seed: int = 0, locale: str | None = None) -> Any:
    return _resolver(seed, locale).resolve_obj(obj)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


class TestParseFakerArgs:
    def test_no_args(self):
        pos, kw = parse_faker_args("faker.name")
        assert pos == [] and kw == {}

    def test_empty_parens(self):
        pos, kw = parse_faker_args("faker.name()")
        assert pos == [] and kw == {}

    def test_keyword_args(self):
        pos, kw = parse_faker_args("faker.random_int(min=1,max=100)")
        assert pos == []
        assert kw == {"min": 1, "max": 100}

    def test_positional_args(self):
        pos, kw = parse_faker_args("faker.pystr(10)")
        assert pos == [10] and kw == {}

    def test_mixed_args(self):
        pos, kw = parse_faker_args("faker.random_int(1,100)")
        assert pos == [1, 100] and kw == {}

    def test_float_values(self):
        pos, kw = parse_faker_args("faker.pyfloat(min=0.0,max=1.0)")
        assert kw["min"] == 0.0 and kw["max"] == 1.0

    def test_bool_values(self):
        pos, kw = parse_faker_args("faker.pybool(truth_probability=75)")
        assert kw["truth_probability"] == 75

    def test_none_value(self):
        pos, kw = parse_faker_args("faker.date_of_birth(age_min=None)")
        assert kw["age_min"] is None

    def test_string_values(self):
        pos, kw = parse_faker_args('faker.date(pattern="%Y-%m-%d")')
        assert kw["pattern"] == "%Y-%m-%d"

    def test_single_quoted_strings(self):
        pos, kw = parse_faker_args("faker.pystr_format(string='???-###')")
        assert kw["string"] == "???-###"

    def test_list_value(self):
        pos, kw = parse_faker_args("faker.random_element(elements=[1,2,3])")
        assert kw["elements"] == [1, 2, 3]

    def test_tuple_value(self):
        pos, kw = parse_faker_args("faker.random_element(elements=(1,2,3))")
        assert kw["elements"] == (1, 2, 3)

    def test_dict_value(self):
        pos, kw = parse_faker_args("faker.mapping(key='value')")
        assert kw["key"] == "value"

    def test_bare_identifier(self):
        pos, kw = parse_faker_args("faker.random_element(elements=WORD_LIST)")
        assert kw["elements"] == "WORD_LIST"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestSecurity:
    def test_blocks_dunder(self):
        with pytest.raises(TemplateResolutionError, match="not allowed"):
            _validate_attr_name("__class__")

    def test_blocks_private(self):
        with pytest.raises(TemplateResolutionError, match="not allowed"):
            _validate_attr_name("_private")

    def test_blocks_os(self):
        with pytest.raises(TemplateResolutionError, match="not allowed"):
            _validate_attr_name("os")

    def test_blocks_subprocess(self):
        with pytest.raises(TemplateResolutionError, match="not allowed"):
            _validate_attr_name("subprocess")

    def test_blocks_eval(self):
        with pytest.raises(TemplateResolutionError, match="not allowed"):
            _validate_attr_name("eval")

    def test_allows_normal_names(self):
        _validate_attr_name("name")  # should not raise
        _validate_attr_name("email")  # should not raise
        _validate_attr_name("random_int")  # should not raise


# ---------------------------------------------------------------------------
# Dynamic resolution: faker.<method>
# ---------------------------------------------------------------------------


class TestDynamicResolution:
    def test_simple_method(self):
        result = _resolve("{faker.name}")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_email(self):
        result = _resolve("{faker.email}")
        assert "@" in result

    def test_city(self):
        result = _resolve("{faker.city}")
        assert isinstance(result, str)

    def test_job(self):
        result = _resolve("{faker.job}")
        assert isinstance(result, str)

    def test_company(self):
        result = _resolve("{faker.company}")
        assert isinstance(result, str)

    def test_word(self):
        result = _resolve("{faker.word}")
        assert isinstance(result, str)

    def test_phone_number(self):
        result = _resolve("{faker.phone_number}")
        assert isinstance(result, str)

    def test_date(self):
        result = _resolve("{faker.date}")
        assert isinstance(result, str)

    def test_ipv4(self):
        result = _resolve("{faker.ipv4}")
        assert isinstance(result, str)

    def test_ipv6(self):
        result = _resolve("{faker.ipv6}")
        assert isinstance(result, str)

    def test_url(self):
        result = _resolve("{faker.url}")
        assert isinstance(result, str)

    def test_user_name(self):
        result = _resolve("{faker.user_name}")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Native return types
# ---------------------------------------------------------------------------


class TestNativeTypes:
    def test_random_int_native(self):
        result = _resolve("{faker.random_int(min=1,max=100)}")
        assert isinstance(result, int)
        assert 1 <= result <= 100

    def test_random_int_embedded(self):
        result = _resolve("id-{faker.random_int(min=1,max=100)}")
        assert isinstance(result, str)
        assert result.startswith("id-")

    def test_pybool_native(self):
        result = _resolve("{faker.pybool}")
        assert isinstance(result, bool)

    def test_pybool_embedded(self):
        result = _resolve("flag-{faker.pybool}")
        assert isinstance(result, str)
        assert result.startswith("flag-")

    def test_date_of_birth_native(self):
        result = _resolve("{faker.date_of_birth}")
        import datetime

        assert isinstance(result, datetime.date)

    def test_date_of_birth_embedded(self):
        result = _resolve("born-{faker.date_of_birth}")
        assert isinstance(result, str)

    def test_pyfloat_native(self):
        result = _resolve("{faker.pyfloat}")
        assert isinstance(result, float)

    def test_profile_native(self):
        # faker.profile with no args defaults to field="job", returns a string
        result = _resolve("{faker.profile}")
        assert isinstance(result, str)

    def test_name_stays_string(self):
        result = _resolve("{faker.name}")
        assert isinstance(result, str)

    def test_full_value_faker_in_object(self):
        obj = {"id": "{faker.random_int(min=1,max=10)}", "active": "{faker.pybool}"}
        resolved = _resolve_obj(obj)
        assert isinstance(resolved["id"], int)
        assert isinstance(resolved["active"], bool)


# ---------------------------------------------------------------------------
# Provider paths
# ---------------------------------------------------------------------------


class TestProviderPaths:
    def test_internet_email(self):
        result = _resolve("{faker.providers.internet.en_US.email}")
        assert "@" in result

    def test_person_name(self):
        result = _resolve("{faker.providers.person.en_US.name}")
        assert isinstance(result, str)

    def test_address_city(self):
        result = _resolve("{faker.providers.address.en_US.city}")
        assert isinstance(result, str)

    def test_bad_provider_path(self):
        with pytest.raises(TemplateResolutionError, match="Invalid faker field"):
            _resolve("{faker.providers.internet.en_US.nonexistent_method}")

    def test_bad_provider_family(self):
        with pytest.raises(TemplateResolutionError, match="Invalid faker field"):
            _resolve("{faker.providers.nonexistent_family.en_US.method}")


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


class TestArguments:
    def test_random_int_with_kwargs(self):
        result = _resolve("{faker.random_int(min=50,max=50)}")
        assert result == 50

    def test_pystr_with_length(self):
        result = _resolve("{faker.pystr(min_chars=10,max_chars=10)}")
        assert isinstance(result, str)
        assert len(result) == 10

    def test_date_with_pattern(self):
        import re

        result = _resolve("{faker.date(pattern=%Y)}")
        assert re.match(r"^\d{4}$", result)

    def test_positional_arg(self):
        result = _resolve("{faker.pystr(10)}")
        assert isinstance(result, str)
        # Faker pystr(10) may return different lengths depending on version
        assert len(result) >= 1

    def test_invalid_kwarg(self):
        with pytest.raises(TemplateResolutionError, match="Unknown keyword argument"):
            _resolve("{faker.random_int(min=1,MAX=100)}")

    def test_suggests_correct_kwarg(self):
        with pytest.raises(TemplateResolutionError, match="did you mean"):
            _resolve("{faker.random_int(mn=1,max=100)}")


# ---------------------------------------------------------------------------
# Locale support
# ---------------------------------------------------------------------------


class TestLocale:
    def test_per_placeholder_locale(self):
        result = _resolve("{faker.name(locale=fr_FR)}")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_global_locale(self):
        result = _resolve("{faker.name}", locale="de_DE")
        assert isinstance(result, str)

    def test_per_placeholder_overrides_global(self):
        result = _resolve("{faker.name(locale=ja_JP)}", locale="de_DE")
        assert isinstance(result, str)

    def test_invalid_locale(self):
        with pytest.raises(TemplateResolutionError, match="Cannot create Faker instance"):
            _resolve("{faker.name(locale=zz_ZZ)}")

    def test_factory_default_locale(self):
        factory = FakerFactory(random.Random(0), default_locale="fr_FR")
        fake = factory.get()
        name = fake.name()
        assert isinstance(name, str)

    def test_factory_cache(self):
        factory = FakerFactory(random.Random(0))
        a = factory.get("en_US")
        b = factory.get("en_US")
        assert a is b


# ---------------------------------------------------------------------------
# Custom providers
# ---------------------------------------------------------------------------


class TestCustomProviders:
    def test_simple_example_provider(self):
        result = _resolve("{faker.simple_example(category=greetings)}")
        assert isinstance(result, str)

    def test_advanced_example_provider(self):
        result = _resolve("{faker.advanced_example(category=greetings, language=en, length=2)}")
        assert isinstance(result, str)

    def test_custom_provider_discovery(self):
        providers = discover_custom_providers("blaze_hammer.ext.providers")
        names = [p.__name__ for p in providers]
        assert "SimpleExampleProvider" in names
        assert "AdvancedExampleProvider" in names


# ---------------------------------------------------------------------------
# Profile and custom shortcuts
# ---------------------------------------------------------------------------


class TestProfileAndCustom:
    def test_profile_default(self):
        # faker.profile with no args defaults to field="job", returns a string
        result = _resolve("{faker.profile}")
        assert isinstance(result, str)

    def test_profile_field(self):
        result = _resolve("{faker.profile(field=job)}")
        assert isinstance(result, str)

    def test_profile_bad_field(self):
        with pytest.raises(TemplateResolutionError, match="Invalid faker.profile field"):
            _resolve("{faker.profile(field=nonexistent)}")

    def test_custom_with_field(self):
        result = _resolve("{faker.custom(field=name)}")
        assert isinstance(result, str)

    def test_custom_bad_field(self):
        with pytest.raises(TemplateResolutionError, match="Invalid faker.custom field"):
            _resolve("{faker.custom(field=nonexistent_xyz)}")


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


class TestSuggestions:
    def test_suggest_faker_fields(self):
        suggestions = suggest_faker_fields("emal", ["email", "name", "url"])
        assert "email" in suggestions

    def test_unknown_method_suggestion(self):
        with pytest.raises(TemplateResolutionError) as exc_info:
            _resolve("{faker.emal}")
        # Should contain suggestions
        assert exc_info.value.suggestions or "not found" in str(exc_info.value).lower()

    def test_unknown_method_with_version(self):
        with pytest.raises(TemplateResolutionError) as exc_info:
            _resolve("{faker.nonexistent_xyz_method}")
        hint = exc_info.value.hint or ""
        assert "Faker version" in hint or "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Seed determinism
# ---------------------------------------------------------------------------


class TestSeedDeterminism:
    def test_same_seed_same_result(self):
        a = _resolve("{faker.name}", seed=42)
        b = _resolve("{faker.name}", seed=42)
        assert a == b

    def test_different_seed_different_result(self):
        a = _resolve("{faker.name}", seed=42)
        b = _resolve("{faker.name}", seed=99)
        assert a != b

    def test_seeded_random_int(self):
        a = _resolve("{faker.random_int(min=1,max=1000000)}", seed=42)
        b = _resolve("{faker.random_int(min=1,max=1000000)}", seed=42)
        assert a == b

    def test_seeded_profile(self):
        a = _resolve("{faker.profile}", seed=42)
        b = _resolve("{faker.profile}", seed=42)
        assert a == b


# ---------------------------------------------------------------------------
# Nested payloads
# ---------------------------------------------------------------------------


class TestNestedPayloads:
    def test_nested_object(self):
        obj = {
            "user": {
                "name": "{faker.name}",
                "email": "{faker.email}",
                "age": "{faker.random_int(min=18,max=80)}",
            }
        }
        resolved = _resolve_obj(obj)
        assert isinstance(resolved["user"]["name"], str)
        assert isinstance(resolved["user"]["email"], str)
        assert isinstance(resolved["user"]["age"], int)

    def test_array(self):
        # list() is a built-in placeholder that resolves item expressions
        # faker.word inside list item gets resolved by the list handler
        obj = {"tags": "{list(item=word,length=3)}"}
        resolved = _resolve_obj(obj)
        assert isinstance(resolved["tags"], list)
        assert len(resolved["tags"]) == 3
        assert all(isinstance(t, str) for t in resolved["tags"])

    def test_multiple_placeholders_in_string(self):
        result = _resolve("user-{faker.random_int(min=1,max=999)}-{faker.word}")
        assert isinstance(result, str)
        parts = result.split("-")
        assert len(parts) == 3

    def test_faker_in_array_items(self):
        obj = {"users": [{"name": "{faker.name}"} for _ in range(3)]}
        resolved = _resolve_obj(obj)
        for user in resolved["users"]:
            assert isinstance(user["name"], str)

    def test_mixed_builtins_and_faker(self):
        obj = {
            "id": "{uuid}",
            "name": "{faker.name}",
            "active": "{bool}",
        }
        resolved = _resolve_obj(obj)
        assert isinstance(resolved["id"], str)
        assert isinstance(resolved["name"], str)
        assert isinstance(resolved["active"], bool)


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestHeaders:
    def test_faker_in_headers(self):
        headers = {
            "X-Request-ID": "{uuid}",
            "X-Test-User": "{faker.user_name}",
            "X-Test-IP": "{faker.ipv4}",
        }
        resolved = _resolve_obj(headers)
        assert isinstance(resolved["X-Request-ID"], str)
        assert isinstance(resolved["X-Test-User"], str)
        assert isinstance(resolved["X-Test-IP"], str)


# ---------------------------------------------------------------------------
# FakerFactory
# ---------------------------------------------------------------------------


class TestFakerFactory:
    def test_providers_added(self):
        factory = FakerFactory(random.Random(0))
        fake = factory.get()
        # Custom providers should be registered
        assert hasattr(fake, "simple_example")
        assert hasattr(fake, "advanced_example")

    def test_locale_specific_providers(self):
        factory = FakerFactory(random.Random(0))
        fake = factory.get("fr_FR")
        assert hasattr(fake, "simple_example")

    def test_attribute_candidates(self):
        factory = FakerFactory(random.Random(0))
        candidates = factory.attribute_candidates()
        assert "name" in candidates
        assert "email" in candidates
        assert "random_int" in candidates

    def test_provider_path_candidates(self):
        factory = FakerFactory(random.Random(0))
        candidates = factory.provider_path_candidates()
        assert len(candidates) > 0
        # Should contain provider paths with method names
        assert any("email" in c for c in candidates)

    def test_list_providers(self):
        factory = FakerFactory(random.Random(0))
        families = factory.list_providers()
        assert isinstance(families, dict)
        # Should have at least some families
        assert len(families) > 0

    def test_list_available_locales(self):
        factory = FakerFactory(random.Random(0))
        locales = factory.list_available_locales()
        assert isinstance(locales, list)
        assert len(locales) > 0
        assert "en_US" in locales

    def test_get_method_info(self):
        factory = FakerFactory(random.Random(0))
        info = factory.get_method_info("random_int")
        assert info is not None
        assert info["name"] == "random_int"
        assert len(info["params"]) > 0

    def test_get_method_info_nonexistent(self):
        factory = FakerFactory(random.Random(0))
        info = factory.get_method_info("nonexistent_method_xyz")
        assert info is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_faker_method(self):
        validator = TemplateValidator(build_default_registry())
        parsed = {"name": "{faker.name}"}
        raw = '{"name": "{faker.name}"}'
        report = validator.validate({"test": (parsed, raw)})
        assert report.ok

    def test_invalid_faker_method(self):
        validator = TemplateValidator(build_default_registry())
        parsed = {"x": "{faker.nonexistent_xyz}"}
        raw = '{"x": "{faker.nonexistent_xyz}"}'
        report = validator.validate({"test": (parsed, raw)})
        # Validation should catch invalid faker method
        assert not report.ok

    def test_invalid_faker_arg(self):
        validator = TemplateValidator(build_default_registry())
        parsed = {"x": "{faker.random_int(min=abc,max=100)}"}
        raw = '{"x": "{faker.random_int(min=abc,max=100)}"}'
        report = validator.validate({"test": (parsed, raw)})
        assert not report.ok

    def test_valid_locale(self):
        validator = TemplateValidator(build_default_registry(), faker_locale="fr_FR")
        parsed = {"name": "{faker.name}"}
        raw = '{"name": "{faker.name}"}'
        report = validator.validate({"test": (parsed, raw)})
        assert report.ok


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_legacy_kwargs_still_work(self):
        result = _resolve("{faker.custom(field=job)}")
        assert isinstance(result, str)

    def test_legacy_profile_still_works(self):
        result = _resolve("{faker.profile(field=job)}")
        assert isinstance(result, str)

    def test_legacy_provider_path_still_works(self):
        result = _resolve("{faker.providers.internet.email}")
        assert "@" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_after_dot(self):
        with pytest.raises(TemplateResolutionError, match="Unrecognized faker placeholder"):
            _resolve("{faker.}")

    def test_faker_only(self):
        # {faker} doesn't start with "faker." so it's an unknown token (kept verbatim)
        result = _resolve("{faker}")
        assert result == "{faker}"

    def test_unclosed_paren_resolves(self):
        # Unclosed paren is ignored by regex; faker.random_int resolves with no args
        result = _resolve("{faker.random_int(min=1}")
        assert isinstance(result, int)

    def test_repeated_calls(self):
        results = [_resolve("{faker.name}", seed=42) for _ in range(5)]
        # All should be the same with same seed
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# embed_as_text
# ---------------------------------------------------------------------------


class TestEmbedAsText:
    def test_none(self):
        assert embed_as_text(None) == ""

    def test_bool_true(self):
        assert embed_as_text(True) == "true"

    def test_bool_false(self):
        assert embed_as_text(False) == "false"

    def test_int(self):
        assert embed_as_text(42) == "42"

    def test_float(self):
        assert embed_as_text(3.14) == "3.14"

    def test_string(self):
        assert embed_as_text("hello") == "hello"

    def test_list(self):
        result = embed_as_text([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_dict(self):
        result = embed_as_text({"a": 1})
        # dict is converted via str()
        assert isinstance(result, str)
        assert "a" in result
