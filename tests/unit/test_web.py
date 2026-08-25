"""Web GUI tests: auth, config merge, API, WebSocket, security, CLI wiring."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.cli.main import _route_legacy
from blaze_hammer.config.loader import build_config
from blaze_hammer.web.app import create_app
from blaze_hammer.web.auth import SESSION_COOKIE, hash_password, verify_password
from blaze_hammer.web.config import resolve_web_settings

XRW = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _project(tmp_path, target: str) -> None:
    (tmp_path / "blazehammer.yaml").write_text(
        f'target: "{target}"\nmethod: GET\nrequests: 4\nconcurrency: 2\n'
        "payload: payload.json\nheaders: headers.json\n",
        encoding="utf-8",
    )
    (tmp_path / "payload.json").write_text('{"n": "{int(min=5,max=5)}"}', encoding="utf-8")
    (tmp_path / "headers.json").write_text('{"X-Test": "{uuid}"}', encoding="utf-8")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "heavy.json").write_text('{"requests": 9}', encoding="utf-8")


def _make_client(
    tmp_path,
    server_url: str,
    *,
    username: str = "admin",
    password: str = "secret-pw",
    enabled: bool = True,
    do_login: bool = True,
):
    overrides: dict[str, Any] = {}
    if enabled:
        overrides["web"] = {
            "auth": {
                "enabled": True,
                "username": username,
                "password": password,
            }
        }
    else:
        overrides["web"] = {"auth": {"enabled": False}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings, project_dir=tmp_path, project_file=tmp_path / "blazehammer.yaml"
    )
    # 'with' enters the FastAPI lifespan (starts the stats ticker).
    client = TestClient(app)
    client.__enter__()
    if enabled and do_login:
        resp = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=XRW,
        )
        assert resp.status_code == 200, resp.text
    return client


@pytest.fixture()
def noauth(tmp_path, server_url):
    """Auth enabled but never logged in -> 401 territory."""
    _project(tmp_path, f"{server_url}/echo")
    client = _make_client(tmp_path, server_url, do_login=False)
    yield client
    client.__exit__(None, None, None)


@pytest.fixture()
def anon(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    client = _make_client(tmp_path, server_url)
    yield client
    client.__exit__(None, None, None)


@pytest.fixture()
def authed(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    client = _make_client(tmp_path, server_url)
    yield client
    client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# hashing and settings
# ---------------------------------------------------------------------------


def test_password_hash_roundtrip():
    encoded = hash_password("s3cret!")
    assert encoded.startswith("scrypt$")
    assert verify_password("s3cret!", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("s3cret!", None)
    assert not verify_password("s3cret!", "garbage")


def test_resolve_web_settings_env_and_hashing():
    from blaze_hammer.config.models import RunConfig

    base = RunConfig(target="https://t.test")
    merged = base.model_copy(
        update={
            "web": base.web.model_copy(
                update={
                    "host": "0.0.0.0",
                    "port": 1234,
                    "auth": base.web.auth.model_copy(update={"password": "plain-pw"}),
                }
            )
        }
    )
    settings = resolve_web_settings(merged, environ={})
    assert settings.host == "0.0.0.0" and settings.port == 1234
    assert settings.auth.password_hash is not None
    assert "plain-pw" not in (settings.auth.password_hash or "")
    assert verify_password("plain-pw", settings.auth.password_hash)


def test_bh_web_short_env_prefix():
    from blaze_hammer.config.models import RunConfig

    base = RunConfig(target="https://t.test")
    settings = resolve_web_settings(
        base,
        environ={"BH_WEB_PORT": "9999", "BH_WEB_USERNAME": "bob", "BH_WEB_PASSWORD": "pw"},
    )
    assert settings.port == 9999
    assert settings.auth.username == "bob"


# ---------------------------------------------------------------------------
# authentication + security
# ---------------------------------------------------------------------------


def test_health_is_public(anon):
    assert anon.get("/api/health").json()["ok"] is True


def test_protected_endpoints_require_session(noauth):
    for path in ("/api/me", "/api/config", "/api/profiles", "/api/runs"):
        r = noauth.get(path)
        assert r.status_code == 401, path


def test_login_success_sets_cookie(authed):
    cookies = authed.cookies
    assert SESSION_COOKIE in cookies


def _wait_finished(client, run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        summary = client.get(f"/api/runs/{run_id}").json()
        if summary["status"] != "running":
            return summary
        time.sleep(0.1)
    raise AssertionError("run did not finish in time")


def test_login_failure_uniform_message(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    app = create_app_from_tmp(tmp_path)
    client = TestClient(app)
    client.__enter__()
    try:
        r = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "WRONG"},
            headers=XRW,
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "Invalid credentials"
    finally:
        client.__exit__(None, None, None)


def test_login_lockout_after_failures(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    app = create_app_from_tmp(tmp_path)
    client = TestClient(app)
    client.__enter__()
    try:
        for _ in range(5):
            client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "bad"},
                headers=XRW,
            )
        locked = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
            headers=XRW,
        )
        assert locked.status_code == 429
    finally:
        client.__exit__(None, None, None)


def test_logout_invalidates_session(authed):
    assert authed.get("/api/me").json()["username"]
    authed.post("/api/auth/logout", headers=XRW)
    assert authed.get("/api/me").status_code == 401


def test_csrf_header_required(authed):
    r = authed.post("/api/runs", json={})
    assert r.status_code == 403


def test_security_headers_present(anon):
    r = anon.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_oversized_body_rejected(authed):
    big = "x" * (8 * 1024 * 1024 + 10)
    r = authed.post(
        "/api/config/save", content=big, headers={**XRW, "Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_docs_gated_behind_auth(noauth):
    assert noauth.get("/docs").status_code == 401
    assert noauth.get("/openapi.json").status_code == 401


# ---------------------------------------------------------------------------
# config + profiles api
# ---------------------------------------------------------------------------


def test_config_endpoint_reflects_project(authed):
    data = authed.get("/api/config").json()
    assert data["method"] == "GET"
    assert data["requests"] == 4
    assert data["web_enabled"] is True


def test_templates_endpoint_returns_file_text(authed):
    data = authed.get("/api/config/templates").json()
    assert data["payload_text"] == '{"n": "{int(min=5,max=5)}"}'


def test_profiles_list_and_show(authed):
    listed = authed.get("/api/profiles").json()
    assert [p["name"] for p in listed] == ["heavy"]
    shown = authed.get("/api/profiles/heavy").json()
    assert shown["data"]["requests"] == 9
    assert authed.get("/api/profiles/nope").status_code == 404


def test_config_save_requires_confirm_then_writes(authed, tmp_path):
    pre = authed.post("/api/config/save", json={}, headers=XRW)
    assert pre.status_code == 428
    ok = authed.post(
        "/api/config/save",
        json={"confirm": True, "requests": 77},
        headers=XRW,
    )
    assert ok.status_code == 200
    text = (tmp_path / "blazehammer.yaml").read_text(encoding="utf-8")
    assert "requests: 77" in text
    # The regenerated file must still parse through the real loader.
    build_config({}, environ={}, config_path=str(tmp_path / "blazehammer.yaml"))


# ---------------------------------------------------------------------------
# preview + runs + websocket
# ---------------------------------------------------------------------------


def test_preview_resolves_placeholders(authed):
    body = {
        "method": "POST",
        "payload_text": '{"user": "{letters(length=3)}"}',
        "count": 2,
    }
    out = authed.post("/api/preview", json=body, headers=XRW).json()
    assert len(out["plans"]) == 2, out
    for plan in out["plans"]:
        value = plan["body"]["user"]
        assert isinstance(value, str) and len(value) == 3


def test_run_lifecycle_rest_then_ws_events(authed):
    """Phase 1 (REST): run reaches completion. Phase 2 (WS): live events."""
    # Phase 1 — REST-driven run, poll to completion.
    started = authed.post(
        "/api/runs",
        json={"requests": 4, "concurrency": 2, "seed": 7},
        headers=XRW,
    )
    assert started.status_code == 200, started.text
    first_id = started.json()["run_id"]
    summary = _wait_finished(authed, first_id)
    assert summary["status"] == "completed"
    assert summary["completed"] == 4 and summary["success"] == 4

    log = authed.get(f"/api/runs/{first_id}/log").json()["entries"]
    assert len(log) == 4
    assert log[0]["ok"] is True
    assert "request_headers" in log[0]

    # Phase 2 — WS subscriber sees live events for a fresh small run.
    with authed.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"

        second = authed.post(
            "/api/runs",
            json={"requests": 2, "concurrency": 2},
            headers=XRW,
        )
        second_id = second.json()["run_id"]

        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            events.append(ws.receive_json())
            if events[-1].get("type") in ("run.completed", "run.error"):
                break
        types = [e["type"] for e in events]
        assert "run.started" in types
        assert any(t == "stats.updated" for t in types)
        assert sum(t == "request.completed" for t in types) >= 2
        final = events[-1]
        assert final["type"] == "run.completed"
        assert final["run_id"] == second_id
        assert final["completed"] == 2


def test_stop_running_flow(authed, server_url):
    slow_target = f"{server_url}/slow?ms=300"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 50\nconcurrency: 4\n',
        encoding="utf-8",
    )
    started = authed.post("/api/runs", json={}, headers=XRW).json()
    run_id = started["run_id"]
    time.sleep(0.4)
    stopped = authed.post(f"/api/runs/{run_id}/stop", headers=XRW)
    assert stopped.status_code == 200
    summary = _wait_finished(authed, run_id)
    assert summary["status"] == "stopped"
    assert summary["completed"] < 50


def test_second_start_rejected_while_running(authed, server_url):
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{server_url}/slow?ms=200"\nmethod: GET\nrequests: 30\nconcurrency: 2\n',
        encoding="utf-8",
    )
    first = authed.post("/api/runs", json={}, headers=XRW)
    assert first.status_code == 200
    second = authed.post("/api/runs", json={}, headers=XRW)
    assert second.status_code == 400
    rid = first.json()["run_id"]
    authed.post(f"/api/runs/{rid}/stop", headers=XRW)
    _wait_finished(authed, rid)


def test_ws_unauthenticated_closed(noauth):
    from starlette.websockets import WebSocketDisconnect

    try:
        with noauth.websocket_connect("/ws"):
            raise AssertionError("expected pre-accept rejection")
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_clear_history(authed, server):
    authed.post(
        "/api/runs",
        json={"requests": 1, "concurrency": 1},
        headers=XRW,
    ).json()
    # wait briefly for completion
    runs = authed.get("/api/runs").json()["runs"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and runs and runs[0]["status"] == "running":
        time.sleep(0.1)
        runs = authed.get("/api/runs").json()["runs"]
    cleared = authed.delete("/api/runs", headers=XRW)
    assert cleared.status_code == 200
    assert authed.get("/api/runs").json()["runs"] == []


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_web_routing_forms_equivalent():
    assert _route_legacy(["--web"]) == ["web"]
    assert _route_legacy(["--web", "--port", "9000"]) == ["web", "--port", "9000"]
    assert _route_legacy(["web"])[0] == "web"
    assert _route_legacy(["--version"]) == ["--version"]


# ---------------------------------------------------------------------------
# shared helper
# ---------------------------------------------------------------------------


def create_app_from_tmp(tmp_path):
    """App with default credentials admin/secret-pw for negative tests."""
    overrides = {"web": {"auth": {"enabled": True, "username": "admin", "password": "secret-pw"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    return create_app(
        settings=settings,
        project_dir=tmp_path,
        project_file=tmp_path / "blazehammer.yaml",
    )


_ = json
