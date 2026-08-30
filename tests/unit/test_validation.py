"""TemplateValidator: pre-flight checks with suggestions."""

from __future__ import annotations

import json

from blaze_hammer.templating import TemplateValidator, build_default_registry


def _validate(text: str, label: str = "payload.json"):
    validator = TemplateValidator(build_default_registry())
    return validator.validate({label: (json.loads(text), text)})


def test_valid_payload_passes():
    report = _validate('{"a": "{uuid}", "b": "{faker.name}", "c": "{choice(x, y)}"}')
    assert report.ok, report.render()


def test_unknown_token_reported_with_suggestion():
    report = _validate('{"a": "{emal(prefix=x_)}"}')
    assert not report.ok
    rendered = report.render()
    assert "Unknown placeholder" in rendered
    assert any("email" in s for s in report.issues[0].suggestions)


def test_bad_faker_provider_attribute_suggested():
    report = _validate('{"a": "{faker.providers.internet.user_nam}"}')
    assert not report.ok
    assert "user_nam" in report.issues[0].token
    assert report.issues[0].suggestions


def test_missing_pick_line_file_detected():
    report = _validate('{"a": "{pick_line(file=definitely/missing.txt)}"}')
    assert not report.ok
    assert "cannot read" in report.issues[0].problem or "No such" in report.issues[0].problem


def test_number_start_longer_than_length_detected():
    report = _validate('{"a": "{number(start=01900, length=3)}"}')
    assert not report.ok
    assert "longer than" in report.issues[0].problem


def test_invalid_date_format_detected():
    report = _validate('{"a": "{date(format=%Q)}"}')
    assert not report.ok


def test_choice_without_options_detected():
    report = _validate('{"a": "{choice}"}')
    assert not report.ok
    assert "choice requires options" in report.issues[0].problem


def test_line_numbers_preserved():
    text = '\n\n\n{"a": "{badbadtoken}"}'
    report = _validate(text)
    assert report.issues[0].line == 4
