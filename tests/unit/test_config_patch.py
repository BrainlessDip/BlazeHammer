"""PATCH-style config save tests: preservation, conflicts, WS sync, safety."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.config.editor import compute_revision, read_config
from blaze_hammer.config.loader import build_config
from blaze_hammer.config.project import load_project_yaml
from blaze_hammer.errors import ConfigurationError
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings

XRW = {"X-Requested-With": "XMLHttpRequest"}

RICH_YAML = """\
# Blaze Hammer configuration

target: "https://example.com/api"   # staging
method: POST

# Performance
requests: 100
concurrency: 10
delay: 0.5

timeout: 30
retries: 2

faker:
  locale: en_US

custom_setting: keep-me
web:
  enabled: true
  host: "127.0.0.1"
  port: 8080
"""


@pytest.fixture()
def rich(tmp_path):
    """Authed client over a config file full of comments/custom keys."""
    import dataclasses

    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.web.auth import hash_password
    from blaze_hammer.web.config import ResolvedAuth

    (tmp_path / "blazehammer.yaml").write_text(RICH_YAML, encoding="utf-8")
    base = resolve_web_settings(
        RunConfig(target="https://example.com/api", method="POST"), environ={}
    )
    settings = dataclasses.replace(
        base,
        auth=ResolvedAuth(enabled=True, username="a", password_hash=hash_password("b")),
    )
    app = create_app(
        settings=settings,
        project_dir=tmp_path,
        project_file=tmp_path / "blazehammer.yaml",
    )
    client = TestClient(app)
    client.__enter__()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "a", "password": "b"},
        headers=XRW,
    )
    assert login.status_code == 200
    yield client
    client.__exit__(None, None, None)


def _patch(client: TestClient, body: dict[str, Any]):
    return client.post("/api/v1/config/save", json=body, headers=XRW)


def _file(tmp_path) -> str:
    return (tmp_path / "blazehammer.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# core PATCH semantics
# ---------------------------------------------------------------------------


def test_single_field_patch_changes_only_that_field(rich, tmp_path):
    before = _file(tmp_path)
    resp = _patch(rich, {"requests": 500})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] == ["requests"]
    after = _file(tmp_path)

    # Only the value moved; every other line is byte-identical.
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    diffs = [(b, a) for b, a in zip(before_lines, after_lines, strict=False) if b != a]
    assert len(diffs) == 1
    assert diffs[0][0].startswith("requests:") and "100" in diffs[0][0]
    assert diffs[0][1].startswith("requests:") and "500" in diffs[0][1]
    assert len(before_lines) == len(after_lines)


def test_multi_field_patch_changes_only_those_fields(rich, tmp_path):
    resp = _patch(rich, {"requests": 250, "concurrency": 20})
    assert resp.status_code == 200
    assert sorted(resp.json()["changed"]) == ["concurrency", "requests"]
    text = _file(tmp_path)
    assert "requests: 250" in text and "concurrency: 20" in text
    assert "delay: 0.5" in text and "retries: 2" in text


def test_omitted_fields_untouched_and_comments_survive(rich, tmp_path):
    resp = _patch(rich, {"delay": 1.25})
    assert resp.status_code == 200
    text = _file(tmp_path)
    for fragment in (
        "# Blaze Hammer configuration",
        "# Performance",
        '#target: "https://example.com/api"   # staging'.replace("#target", "target"),
        "custom_setting: keep-me",
        "faker:",
        "locale: en_US",
    ):
        assert fragment in text, fragment
    assert "delay: 1.25" in text


def test_unknown_custom_keys_survive(rich, tmp_path):
    _patch(rich, {"timeout": 45})
    text = _file(tmp_path)
    assert "custom_setting: keep-me" in text
    # And the loader still rejects it strictly (CLI behavior unchanged).
    with pytest.raises(ConfigurationError):
        load_project_yaml(tmp_path / "blazehammer.yaml")


def test_key_ordering_preserved(rich, tmp_path):
    original_order = [
        line.split(":")[0]
        for line in RICH_YAML.splitlines()
        if line and not line.startswith(("#", " "))
    ]
    _patch(rich, {"requests": 321})
    new_order = [
        line.split(":")[0]
        for line in _file(tmp_path).splitlines()
        if line and not line.startswith(("#", " "))
    ]
    assert [k for k in new_order if k in ("target", "method", "requests")] == [
        k for k in original_order if k in ("target", "method", "requests")
    ]


def test_nested_web_patch_merges(rich, tmp_path):
    resp = _patch(rich, {"web": {"port": 9000}})
    assert resp.status_code == 200
    assert resp.json()["changed"] == ["web.port"]
    text = _file(tmp_path)
    assert "port: 9000" in text
    assert "enabled: true" in text
    assert 'host: "127.0.0.1"' in text


# ---------------------------------------------------------------------------
# null handling / empty patch
# ---------------------------------------------------------------------------


def test_explicit_null_clears_optional_rate(rich, tmp_path):
    resp = _patch(rich, {"rate": 42.5})
    assert resp.json()["changed"] == ["rate"]
    assert "rate: 42.5" in _file(tmp_path)

    resp = _patch(rich, {"rate": None})
    assert resp.status_code == 200
    assert resp.json()["changed"] == ["rate"]
    rate_line = next(line for line in _file(tmp_path).splitlines() if line.startswith("rate:"))
    assert rate_line.split(":", 1)[1].strip() in ("null", "~", "")


def test_empty_patch_changes_nothing(rich, tmp_path):
    before_text = _file(tmp_path)
    before_rev = compute_revision(before_text)
    resp = _patch(rich, {})
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] == []
    assert body["config_revision"] == before_rev
    assert _file(tmp_path) == before_text  # byte-identical, not rewritten


def test_same_value_is_a_no_op(rich, tmp_path):
    before = _file(tmp_path)
    resp = _patch(rich, {"concurrency": 10})
    assert resp.json()["changed"] == []
    assert _file(tmp_path) == before


# ---------------------------------------------------------------------------
# validation & conflicts
# ---------------------------------------------------------------------------


def test_invalid_value_rejected_before_write(rich, tmp_path):
    """Range errors are caught by the request schema (422), file untouched."""
    resp = _patch(rich, {"requests": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "requests: 100" in _file(tmp_path)


def test_method_normalized_and_validated(rich, tmp_path):
    resp = _patch(rich, {"method": "get"})
    assert resp.status_code == 200
    assert "method: GET" in _file(tmp_path)


def test_revision_conflict_returns_409(rich, tmp_path):
    loaded = _patch(rich, {}).json()["config_revision"]
    (tmp_path / "blazehammer.yaml").write_text(
        RICH_YAML.replace("concurrency: 10", "concurrency: 77"), encoding="utf-8"
    )
    resp = _patch(rich, {"requests": 500, "config_revision": loaded})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "CONFIG_CONFLICT"
    assert err["current_revision"] == compute_revision(_file(tmp_path))
    assert "concurrency: 77" in _file(tmp_path)  # external edit intact


def test_matching_revision_saves(rich, tmp_path):
    rev = _patch(rich, {}).json()["config_revision"]
    resp = _patch(rich, {"requests": 600, "config_revision": rev})
    assert resp.status_code == 200
    on_disk_rev = read_config(tmp_path / "blazehammer.yaml")[1]
    assert resp.json()["config_revision"] == on_disk_rev


# ---------------------------------------------------------------------------
# websocket + atomicity + authz
# ---------------------------------------------------------------------------


def test_ws_emits_only_changed_field_names(rich):
    with rich.websocket_connect("/api/v1/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        catalog = ws.receive_json()  # placeholder.catalog push
        assert catalog["type"] == "placeholder.catalog"

        resp = _patch(rich, {"requests": 811, "concurrency": 33})
        assert resp.status_code == 200

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "config.changed":
                assert sorted(ev["changed"]) == ["concurrency", "requests"]
                assert "yaml" not in ev and "content" not in ev
                return
        raise AssertionError("config.changed not received")


def test_atomic_failure_keeps_original_file(rich, tmp_path, monkeypatch):
    import blaze_hammer.config.editor as editor_mod

    def explode(path, updates):
        raise OSError("disk full")

    monkeypatch.setattr(editor_mod, "update_fields", explode)
    resp = _patch(rich, {"requests": 900})
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "CONFIG_WRITE_FAILED"
    assert "requests: 100" in _file(tmp_path)


def test_unauthorized_patch_rejected():
    # Minimal no-login client against an auth-enabled app.
    import tempfile
    from pathlib import Path

    from click.testing import CliRunner  # noqa: F401 - parity guard only

    tmp = Path(tempfile.mkdtemp())
    (tmp / "blazehammer.yaml").write_text('target: "https://t"\n', encoding="utf-8")
    overrides = {"web": {"auth": {"enabled": True, "username": "a", "password": "b"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(settings=settings, project_dir=tmp, project_file=tmp / "blazehammer.yaml")
    client = TestClient(app)
    client.__enter__()
    try:
        resp = client.post("/api/v1/config/save", json={"requests": 5}, headers=XRW)
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_get_config_exposes_revision(rich):
    data = rich.get("/api/v1/config").json()
    assert len(data["config_revision"]) == 64
