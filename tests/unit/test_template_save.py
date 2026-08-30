"""Template save API tests: revisions, atomic writes, conflicts, WS sync."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.config.loader import build_config
from blaze_hammer.files.templates import compute_revision, read_template
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings

XRW = {"X-Requested-With": "XMLHttpRequest"}

PAYLOAD_V1 = '{\n  "username": "{username(length=10)}",\n  "id": "{uuid}"\n}'
HEADERS_V1 = '{\n  "X-Test": "{uuid}"\n}'


@pytest.fixture()
def tauthed(tmp_path, server_url):
    """Authed client whose project has known template contents."""
    (tmp_path / "blazehammer.yaml").write_text(
        f'target: "{server_url}/echo"\nmethod: GET\nrequests: 2\n'
        "payload: payload.json\nheaders: headers.json\n",
        encoding="utf-8",
    )
    (tmp_path / "payload.json").write_text(PAYLOAD_V1, encoding="utf-8")
    (tmp_path / "headers.json").write_text(HEADERS_V1, encoding="utf-8")
    overrides = {"web": {"auth": {"enabled": True, "username": "a", "password": "b"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings,
        project_dir=tmp_path,
        project_file=tmp_path / "blazehammer.yaml",
    )
    client = TestClient(app)
    client.__enter__()
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "a", "password": "b"},
        headers=XRW,
    )
    assert resp.status_code == 200
    yield client
    client.__exit__(None, None, None)


def _get_templates(client: TestClient) -> dict[str, Any]:
    return client.get("/api/v1/config/templates").json()


def _save(client: TestClient, body: dict[str, Any]):
    return client.post("/api/v1/config/templates/save", json=body, headers=XRW)


# ---------------------------------------------------------------------------
# GET with revisions
# ---------------------------------------------------------------------------


def test_get_templates_returns_revisions_and_relative_paths(tauthed, tmp_path):
    data = _get_templates(tauthed)
    assert data["payload_text"] == PAYLOAD_V1
    assert data["payload"] == PAYLOAD_V1  # documented alias
    assert data["payload_revision"] == compute_revision(PAYLOAD_V1)
    assert data["headers_revision"] == compute_revision(HEADERS_V1)
    assert data["payload_file"] == "payload.json"
    assert data["headers_file"] == "headers.json"


# ---------------------------------------------------------------------------
# partial saves
# ---------------------------------------------------------------------------


def test_save_payload_only_leaves_headers_untouched(tauthed, tmp_path):
    new_payload = '{"a": "{faker.word}"}'
    resp = _save(tauthed, {"payload": new_payload})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True and data["saved"] == ["payload"]
    assert data["payload_revision"] == compute_revision(new_payload.rstrip("\n") + "\n")
    assert data["headers_revision"] is None
    # exact formatting preserved + trailing newline added; placeholders intact
    assert (tmp_path / "payload.json").read_text(encoding="utf-8") == new_payload + "\n"
    assert (tmp_path / "headers.json").read_text(encoding="utf-8") == HEADERS_V1


def test_save_headers_only_leaves_payload_untouched(tauthed, tmp_path):
    new_headers = '{"Authorization": "Bearer ${TOKEN}"}'
    resp = _save(tauthed, {"headers": new_headers})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["saved"] == ["headers"]
    assert data["payload_revision"] is None
    assert (tmp_path / "payload.json").read_text(encoding="utf-8") == PAYLOAD_V1
    assert (tmp_path / "headers.json").read_text(encoding="utf-8") == new_headers + "\n"


def test_save_both(tauthed, tmp_path):
    resp = _save(tauthed, {"payload": '{"p": 1}', "headers": '{"h": 2}'})
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved"] == ["payload", "headers"]
    assert data["payload_revision"] and data["headers_revision"]
    assert (tmp_path / "payload.json").read_text() == '{"p": 1}\n'
    assert (tmp_path / "headers.json").read_text() == '{"h": 2}\n'


def test_placeholders_survive_save_verbatim(tauthed, tmp_path):
    tricky = (
        '{\n  "u": "{faker.user_name}",\n  "n": "{int(min=18,max=80)}",'
        '\n  "d": "{date(offset=-30d)}"\n}'
    )
    _save(tauthed, {"payload": tricky})
    on_disk = (tmp_path / "payload.json").read_text(encoding="utf-8")
    for token in ("{faker.user_name}", "{int(min=18,max=80)}", "{date(offset=-30d)}"):
        assert token in on_disk
    # no key reordering / reindentation beyond the trailing newline rule
    assert json.loads(on_disk) == json.loads(tricky)


# ---------------------------------------------------------------------------
# validation errors
# ---------------------------------------------------------------------------


def test_invalid_payload_json_returns_structured_422(tauthed, tmp_path):
    bad = '{\n  "a": 1,\n  "b": \n}'
    resp = _save(tauthed, {"payload": bad})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "INVALID_JSON"
    assert err["file"] == "payload"
    assert isinstance(err.get("line"), int) and isinstance(err.get("column"), int)
    # original file untouched
    assert (tmp_path / "payload.json").read_text(encoding="utf-8") == PAYLOAD_V1


def test_invalid_headers_json_returns_structured_422(tauthed):
    resp = _save(tauthed, {"headers": "{oops"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "INVALID_JSON" and err["file"] == "headers"


def test_empty_body_is_rejected(tauthed):
    resp = _save(tauthed, {})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NOTHING_TO_SAVE"


# ---------------------------------------------------------------------------
# revision conflict
# ---------------------------------------------------------------------------


def test_revision_conflict_returns_409_with_current(tauthed, tmp_path):
    loaded = _get_templates(tauthed)["payload_revision"]

    # Another process changes payload.json behind the editor's back.
    external = '{"external": true}'
    (tmp_path / "payload.json").write_text(external, encoding="utf-8")

    resp = _save(tauthed, {"payload": '{"mine": 1}', "payload_revision": loaded})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "TEMPLATE_CONFLICT"
    assert err["file"] == "payload"
    assert err["current_revision"] == compute_revision(external)
    # The other process's change was NOT clobbered.
    assert (tmp_path / "payload.json").read_text(encoding="utf-8") == external


def test_matching_revision_saves_and_returns_new_revision(tauthed, tmp_path):
    rev = _get_templates(tauthed)["headers_revision"]
    new_headers = '{"X-After": "yes"}'
    resp = _save(tauthed, {"headers": new_headers, "headers_revision": rev})
    assert resp.status_code == 200
    data = resp.json()
    expected = compute_revision(new_headers.rstrip("\n") + "\n")
    assert data["headers_revision"] == expected
    on_disk_rev = read_template(tmp_path / "headers.json")[1]
    assert on_disk_rev == expected


# ---------------------------------------------------------------------------
# websocket synchronization + security
# ---------------------------------------------------------------------------


def test_ws_broadcasts_config_changed_after_save(tauthed):
    with tauthed.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        resp = _save(tauthed, {"payload": '{"after": 1}'})
        assert resp.status_code == 200
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "config.changed":
                assert ev["changed"] == ["payload"]
                assert "payload" not in ev and "headers" not in ev  # no contents
                return
        raise AssertionError("config.changed event not received")


def test_unauthorized_save_rejected(tmp_path, server_url):
    """Auth-enabled app, never logged in -> 401 (local fixture, no cross-module)."""
    (tmp_path / "blazehammer.yaml").write_text(
        f'target: "{server_url}/echo"\nmethod: GET\npayload: payload.json\n',
        encoding="utf-8",
    )
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    overrides = {"web": {"auth": {"enabled": True, "username": "a", "password": "b"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings,
        project_dir=tmp_path,
        project_file=tmp_path / "blazehammer.yaml",
    )
    client = TestClient(app)
    client.__enter__()
    try:
        resp = client.post("/api/v1/config/templates/save", json={"payload": "{}"}, headers=XRW)
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_csrf_missing_rejected(tauthed):
    resp = tauthed.post("/api/v1/config/templates/save", json={"payload": "{}"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# atomicity
# ---------------------------------------------------------------------------


def test_atomic_write_cleans_tmp_on_replace_failure(tmp_path, monkeypatch):
    """os.replace failing must remove the tmp file and keep the original."""
    import os

    from blaze_hammer.files.templates import atomic_write_text

    def boom(src, dst):
        raise OSError("replace denied")

    target = tmp_path / "t.json"
    target.write_text("original", encoding="utf-8")
    monkeypatch.setattr(os, "replace", boom)
    try:
        with pytest.raises(OSError):
            atomic_write_text(target, '{"x": 1}')
    finally:
        monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "original"
    assert not (tmp_path / "t.json.tmp").exists()


# ---------------------------------------------------------------------------
# project/target guards
# ---------------------------------------------------------------------------


def _auth_enabled_app(tmp_path, *, project_file=None):
    """App with auth on (a/b); optionally no project file at all."""
    import dataclasses

    from blaze_hammer.config.models import RunConfig
    from blaze_hammer.web.auth import hash_password
    from blaze_hammer.web.config import ResolvedAuth

    base = resolve_web_settings(RunConfig(target="https://t.test"), environ={})
    settings = dataclasses.replace(
        base,
        auth=ResolvedAuth(
            enabled=True,
            username="a",
            password_hash=hash_password("b"),
        ),
    )
    return create_app(settings=settings, project_dir=tmp_path, project_file=project_file)


def test_save_without_project_returns_structured_error(tmp_path):
    client = TestClient(_auth_enabled_app(tmp_path, project_file=None))
    client.__enter__()
    try:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "a", "password": "b"},
            headers=XRW,
        )
        assert login.status_code == 200
        resp = client.post(
            "/api/v1/config/templates/save",
            json={"payload": "{}"},
            headers=XRW,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "PROJECT_NOT_FOUND"
    finally:
        client.__exit__(None, None, None)


def test_redoc_is_session_gated(tmp_path):
    client = TestClient(_auth_enabled_app(tmp_path, project_file=None))
    client.__enter__()
    try:
        assert client.get("/redoc").status_code == 401
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# CLI wiring: bh web passes overrides into the server launcher
# ---------------------------------------------------------------------------


def test_cli_web_command_overrides_and_launch(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from blaze_hammer.cli.main import cli
    from blaze_hammer.errors import EXIT_OK

    captured: dict[str, Any] = {}

    def fake_server(cfg, *, yes_i_know=False):
        captured["host"] = cfg.web.host
        captured["port"] = cfg.web.port
        return EXIT_OK

    monkeypatch.setattr("blaze_hammer.services.run_web_server", fake_server)
    (tmp_path / "blazehammer.yaml").write_text('target: "https://t.test"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["web", "--host", "127.0.0.1", "--port", "9111"],
        catch_exceptions=False,
    )
    assert result.exit_code == EXIT_OK, result.output
    assert captured["port"] == 9111

    # Defaults flow through when no flags are given.
    result2 = CliRunner().invoke(cli, ["web"], catch_exceptions=False)
    assert result2.exit_code == EXIT_OK


def test_save_unconfigured_target_rejected(tauthed, tmp_path):
    # Remove the headers mapping from the YAML, then try saving headers.
    yaml_path = tmp_path / "blazehammer.yaml"
    text = yaml_path.read_text(encoding="utf-8").replace("headers: headers.json\n", "")
    yaml_path.write_text(text, encoding="utf-8")
    resp = _save(tauthed, {"headers": "{}"})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "TEMPLATE_NOT_CONFIGURED" and err["file"] == "headers"
