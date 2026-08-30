"""Config model + loader merge order + validation rules."""

from __future__ import annotations

import json

import pytest

from blaze_hammer.config.loader import build_config as loader_build
from blaze_hammer.config.models import Method, RunConfig
from blaze_hammer.config.validation import ensure_config_valid, validate_config
from blaze_hammer.errors import ConfigurationError, ProfileError


def _build(overrides=None, profile=None, environ=None):
    return loader_build(overrides or {}, profile=profile, environ=environ or {})


def test_defaults():
    cfg = _build({"target": "https://x.test/api"})
    assert cfg.requests == 100 and cfg.concurrency == 100
    assert cfg.method is Method.GET and cfg.timeout == 30.0
    assert cfg.retries.max_retries == 0  # opt-in retries


def test_profile_then_env_then_cli_priority(tmp_path):
    profile_path = tmp_path / "p.json"
    profile_path.write_text(json.dumps({"requests": 10, "concurrency": 7}))
    {"target": "https://x.test", "_profile_str": str(profile_path)}

    cfg = _build({"target": "https://x.test", "profile": str(profile_path)})
    assert cfg.requests == 10 and cfg.concurrency == 7

    profile_only = {"profile": str(profile_path), "target": "https://x.test"}
    cfg = _build(profile_only, environ={"BLAZE_CONCURRENCY": "9"})
    assert cfg.requests == 10 and cfg.concurrency == 9

    cfg = _build(
        {**profile_only, "concurrency": 11},
        environ={"BLAZE_CONCURRENCY": "9"},
    )
    assert cfg.concurrency == 11


def test_unknown_profile_field_rejected(tmp_path):
    profile_path = tmp_path / "bad.json"
    profile_path.write_text(json.dumps({"concurrencyy": 5}))
    with pytest.raises(ConfigurationError) as excinfo:
        _build({"profile": str(profile_path)})
    assert "concurrencyy" in str(excinfo.value.reason)


def test_missing_profile_lists_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "known.json").write_text("{}")
    with pytest.raises(ProfileError) as excinfo:
        _build({"profile": "nope"})
    assert "known" in excinfo.value.hint


def test_post_requires_payload_unless_dry_run():
    cfg = RunConfig(target="https://x.test", method="POST")
    with pytest.raises(ConfigurationError):
        ensure_config_valid(cfg)
    dry = cfg.model_copy(deep=True)
    from blaze_hammer.config.models import PreviewOptions

    dry.preview = PreviewOptions(dry_run=True)
    ensure_config_valid(dry)  # no raise


def test_file_payload_requires_form_post_type():
    cfg = RunConfig(target="https://x.test", file_payload=True)
    outcome = validate_config(cfg)
    assert not outcome.ok
    assert any("multipart" in f for f in outcome.failures)


def test_url_scheme_enforced():
    cfg = RunConfig(target="ftp://x.test")
    outcome = validate_config(cfg)
    assert any("Target URL" in failure for failure in outcome.failures)


def test_contradictory_filters_rejected():
    base = {"target": "https://x.test"}
    cfg = RunConfig(
        **base,
        output={"failed_only": True, "success_only": True},
    )
    outcome = validate_config(cfg)
    assert any("filters" in failure for failure in outcome.failures)
