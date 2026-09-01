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
    (tmp_path / "profiles").mkdir(exist_ok=True)
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
            "/api/v1/auth/login",
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
    data = anon.get("/api/v1/health").json()
    assert data["status"] == "ok"
    assert data["service"] == "blaze-hammer"


def test_protected_endpoints_require_session(noauth):
    for path in ("/api/v1/me", "/api/v1/config", "/api/v1/profiles", "/api/v1/runs"):
        r = noauth.get(path)
        assert r.status_code == 401, path


def test_login_success_sets_cookie(authed):
    cookies = authed.cookies
    assert SESSION_COOKIE in cookies


def _wait_finished(client, run_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        summary = client.get(f"/api/v1/runs/{run_id}").json()
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
            "/api/v1/auth/login",
            json={"username": "admin", "password": "WRONG"},
            headers=XRW,
        )
        assert r.status_code == 401
        err = r.json()["error"]
        assert err["code"] == "NOT_AUTHENTICATED"
        assert err["message"] == "Invalid credentials"
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
                "/api/v1/auth/login",
                json={"username": "admin", "password": "bad"},
                headers=XRW,
            )
        locked = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "bad"},
            headers=XRW,
        )
        assert locked.status_code == 429
    finally:
        client.__exit__(None, None, None)


def test_logout_invalidates_session(authed):
    assert authed.get("/api/v1/me").json()["username"]
    authed.post("/api/v1/auth/logout", headers=XRW)
    assert authed.get("/api/v1/me").status_code == 401


def test_csrf_header_required(authed):
    r = authed.post("/api/v1/runs", json={})
    assert r.status_code == 403


def test_security_headers_present(anon):
    r = anon.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    # API-only server: nothing scriptable is served, so lock everything down.
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_oversized_body_rejected(authed):
    big = "x" * (8 * 1024 * 1024 + 10)
    r = authed.post(
        "/api/v1/config/save", content=big, headers={**XRW, "Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_docs_gated_behind_auth(noauth):
    assert noauth.get("/docs").status_code == 401
    assert noauth.get("/openapi.json").status_code == 401


# ---------------------------------------------------------------------------
# config + profiles api
# ---------------------------------------------------------------------------


def test_config_endpoint_reflects_project(authed):
    data = authed.get("/api/v1/config").json()
    assert data["method"] == "GET"
    assert data["requests"] == 4
    assert data["web_enabled"] is True


def test_templates_endpoint_returns_file_text(authed):
    data = authed.get("/api/v1/config/templates").json()
    assert data["payload_text"] == '{"n": "{int(min=5,max=5)}"}'


def test_profiles_list_and_show(authed):
    listed = authed.get("/api/v1/profiles").json()
    assert [p["name"] for p in listed] == ["heavy"]
    shown = authed.get("/api/v1/profiles/heavy").json()
    assert shown["data"]["requests"] == 9
    assert authed.get("/api/v1/profiles/nope").status_code == 404


def test_config_save_is_a_patch(authed, tmp_path):
    """PATCH semantics: only provided fields change; formatting survives."""
    original = (tmp_path / "blazehammer.yaml").read_text(encoding="utf-8")
    assert "requests:" in original

    resp = authed.post("/api/v1/config/save", json={"requests": 77}, headers=XRW)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["changed"] == ["requests"]
    assert len(body["config_revision"]) == 64

    updated = (tmp_path / "blazehammer.yaml").read_text(encoding="utf-8")
    assert "requests: 77" in updated
    assert "target:" in updated and "concurrency:" in updated
    # Line count unchanged → nothing else was touched.
    assert len(original.splitlines()) == len(updated.splitlines())

    # The patched file must still parse through the real loader.
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
    out = authed.post("/api/v1/preview", json=body, headers=XRW).json()
    assert len(out["plans"]) == 2, out
    for plan in out["plans"]:
        value = plan["body"]["user"]
        assert isinstance(value, str) and len(value) == 3


def test_run_lifecycle_rest_then_ws_events(authed, server_url):
    """Phase 1 (REST): run reaches completion. Phase 2 (WS): live events."""
    # Phase 1 — REST-driven run, poll to completion.
    started = authed.post(
        "/api/v1/runs",
        json={"requests": 4, "concurrency": 2, "seed": 7},
        headers=XRW,
    )
    assert started.status_code == 200, started.text
    first_id = started.json()["run_id"]
    summary = _wait_finished(authed, first_id)
    assert summary["status"] == "completed"
    assert summary["completed"] == 4 and summary["success"] == 4

    log = authed.get(f"/api/v1/runs/{first_id}/log").json()
    assert len(log) == 4
    assert log[0]["ok"] is True
    assert "request_headers" in log[0]

    # Phase 2 — WS subscriber sees live events for a fresh small run.
    # Use a slow enough endpoint so the stats ticker (0.25s interval) fires.
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{server_url}/slow?ms=150"\nmethod: GET\nrequests: 4\nconcurrency: 2\n',
        encoding="utf-8",
    )
    with authed.websocket_connect("/api/v1/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"

        second = authed.post(
            "/api/v1/runs",
            json={"requests": 4, "concurrency": 2},
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
        assert final["completed"] == 4


def test_stop_running_flow(authed, server_url):
    slow_target = f"{server_url}/slow?ms=300"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 50\nconcurrency: 4\n',
        encoding="utf-8",
    )
    started = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    run_id = started["run_id"]
    time.sleep(0.4)
    stopped = authed.post(f"/api/v1/runs/{run_id}/stop", headers=XRW)
    assert stopped.status_code == 200
    summary = _wait_finished(authed, run_id)
    assert summary["status"] == "cancelled"
    assert summary["completed"] < 50


def test_concurrent_runs_run_independently(authed, server_url):
    """Start three runs against a slow target; all execute concurrently."""
    slow_target = f"{server_url}/slow?ms=400"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 12\nconcurrency: 2\n',
        encoding="utf-8",
    )
    started = [authed.post("/api/v1/runs", json={}, headers=XRW).json() for _ in range(3)]
    for s in started:
        assert s["status"] == "running", s
    ids = {s["run_id"] for s in started}
    assert len(ids) == 3

    # All three are live at the same time.
    for rid in ids:
        assert authed.get(f"/api/v1/runs/{rid}").json()["status"] == "running"
    for rid in ids:
        _wait_finished(authed, rid)
    for rid in ids:
        assert authed.get(f"/api/v1/runs/{rid}").json()["status"] == "completed"


def test_cancel_one_run_others_continue(authed, server_url):
    """Cancel B while A and C run; A/C finish, B is cancelled."""
    slow_target = f"{server_url}/slow?ms=400"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 20\nconcurrency: 2\n',
        encoding="utf-8",
    )
    a = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    b = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    c = authed.post("/api/v1/runs", json={}, headers=XRW).json()

    stopped = authed.post(f"/api/v1/runs/{b['run_id']}/stop", headers=XRW)
    assert stopped.status_code == 200
    summary_b = _wait_finished(authed, b["run_id"])
    assert summary_b["status"] == "cancelled"

    # A and C must still be running and eventually complete.
    assert authed.get(f"/api/v1/runs/{a['run_id']}").json()["status"] == "running"
    assert authed.get(f"/api/v1/runs/{c['run_id']}").json()["status"] == "running"
    _wait_finished(authed, a["run_id"])
    _wait_finished(authed, c["run_id"])
    assert authed.get(f"/api/v1/runs/{a['run_id']}").json()["status"] == "completed"
    assert authed.get(f"/api/v1/runs/{c['run_id']}").json()["status"] == "completed"


def test_concurrent_runs_ws_events_carry_run_id(authed, server_url):
    """One WS connection receives interleaved events for concurrent runs."""
    slow_target = f"{server_url}/slow?ms=300"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 6\nconcurrency: 2\n',
        encoding="utf-8",
    )
    with authed.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ids = {authed.post("/api/v1/runs", json={}, headers=XRW).json()["run_id"] for _ in range(3)}
        seen_start: set[str] = set()
        seen_completed: set[str] = set()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "run.started":
                seen_start.add(ev["run_id"])
            elif ev["type"] in ("run.completed", "run.cancelled", "run.error"):
                seen_completed.add(ev["run_id"])
                if seen_completed == ids:
                    break
        assert seen_start == ids
        assert seen_completed == ids


def test_one_run_fails_others_continue(authed, server_url):
    """A config-error run at POST must not disturb concurrent runs."""
    slow_target = f"{server_url}/slow?ms=300"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 24\nconcurrency: 2\n',
        encoding="utf-8",
    )
    a = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    # A config error (bad target) returns 400 deterministically.
    bad = authed.post("/api/v1/runs", json={"target": "not-a-url"}, headers=XRW)
    assert bad.status_code == 400
    c = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    assert c["status"] == "running"
    _wait_finished(authed, a["run_id"])
    _wait_finished(authed, c["run_id"])
    assert authed.get(f"/api/v1/runs/{a['run_id']}").json()["status"] == "completed"
    assert authed.get(f"/api/v1/runs/{c['run_id']}").json()["status"] == "completed"


def test_start_new_run_while_others_active(authed, server_url):
    """Starting a fresh run while several run remains healthy."""
    slow_target = f"{server_url}/slow?ms=350"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 10\nconcurrency: 2\n',
        encoding="utf-8",
    )
    first = [authed.post("/api/v1/runs", json={}, headers=XRW).json() for _ in range(3)]
    fourth = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    assert fourth["status"] == "running"
    for s in first + [fourth]:
        _wait_finished(authed, s["run_id"])
    for s in first + [fourth]:
        assert authed.get(f"/api/v1/runs/{s['run_id']}").json()["status"] == "completed"


def test_cancel_immediately_after_start(authed, server_url):
    """Cancelling a run right after starting it still ends cancelled."""
    slow_target = f"{server_url}/slow?ms=1000"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 50\nconcurrency: 2\n',
        encoding="utf-8",
    )
    s = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    rid = s["run_id"]
    authed.post(f"/api/v1/runs/{rid}/stop", headers=XRW)
    summary = _wait_finished(authed, rid)
    assert summary["status"] == "cancelled"


def test_concurrent_runs_cleanup_keeps_bounded_history(authed, server_url):
    """Many finished runs stay bounded by the history limit."""
    from blaze_hammer.web.runs import HISTORY_LIMIT

    echo = f"{server_url}/echo"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{echo}"\nmethod: GET\nrequests: 2\nconcurrency: 2\n',
        encoding="utf-8",
    )
    ids = set()
    for _ in range(HISTORY_LIMIT + 5):
        s = authed.post("/api/v1/runs", json={}, headers=XRW).json()
        ids.add(s["run_id"])
        _wait_finished(authed, s["run_id"])
    remaining = authed.get("/api/v1/runs").json()["runs"]
    assert len(remaining) <= HISTORY_LIMIT


def test_shutdown_stops_active_runs(authed, server_url):
    """Graceful shutdown requests stop on every active run."""
    slow_target = f"{server_url}/slow?ms=500"
    (authed.app.state.web.project_dir / "blazehammer.yaml").write_text(
        f'target: "{slow_target}"\nmethod: GET\nrequests: 100\nconcurrency: 4\n',
        encoding="utf-8",
    )
    s = authed.post("/api/v1/runs", json={}, headers=XRW).json()
    rid = s["run_id"]
    manager = authed.app.state.web.manager
    assert manager is not None
    import asyncio

    asyncio.run(manager.shutdown())
    summary = _wait_finished(authed, rid)
    assert summary["status"] in ("cancelled", "completed")


def test_ws_unauthenticated_closed(noauth):
    from starlette.websockets import WebSocketDisconnect

    try:
        with noauth.websocket_connect("/api/v1/ws"):
            raise AssertionError("expected pre-accept rejection")
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_clear_history(authed, server):
    authed.post(
        "/api/v1/runs",
        json={"requests": 1, "concurrency": 1},
        headers=XRW,
    ).json()
    # wait briefly for completion
    runs = authed.get("/api/v1/runs").json()["runs"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and runs and runs[0]["status"] == "running":
        time.sleep(0.1)
        runs = authed.get("/api/v1/runs").json()["runs"]
    cleared = authed.delete("/api/v1/runs", headers=XRW)
    assert cleared.status_code == 200
    assert authed.get("/api/v1/runs").json()["runs"] == []


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_web_routing_forms_equivalent():
    assert _route_legacy(["--web"]) == ["web"]
    assert _route_legacy(["--web", "--port", "9000"]) == ["web", "--port", "9000"]
    assert _route_legacy(["web"])[0] == "web"
    assert _route_legacy(["--version"]) == ["--version"]


# ---------------------------------------------------------------------------
# API-only server contract (v1.5 split)
# ---------------------------------------------------------------------------


def test_root_is_json_api_info(anon):
    r = anon.get("/")
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert data["name"] == "Blaze Hammer"
    assert data["status"] == "ok"
    assert data["api"] == "/api/v1"
    assert data["websocket"] == "/api/v1/ws"
    assert "<html" not in r.text.lower()


def test_no_frontend_assets_served(anon):
    assert anon.get("/index.html").status_code == 404
    assert anon.get("/static/app.js").status_code == 404


def test_info_endpoint(anon):
    data = anon.get("/api/v1/info").json()
    assert data["api_version"] == "v1"
    assert data["features"]["websocket"] is True
    assert data["features"]["profiles"] is True


def test_error_envelope_shape(anon, authed):
    missing = anon.get("/api/v1/runs/does-not-exist")
    body = missing.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert isinstance(body["error"]["message"], str)

    bad = authed.post("/api/v1/runs", json={"requests": -5}, headers=XRW)
    err = bad.json()["error"]
    assert err["code"] in ("VALIDATION_ERROR", "BAD_REQUEST")


def test_config_exposes_no_secrets(authed):
    raw = authed.get("/api/v1/config").text.lower()
    for needle in ("password", "password_hash", "secret-pw"):
        assert needle not in raw


def test_validate_endpoint_ok_and_bad(authed):
    ok = authed.post(
        "/api/v1/validate",
        json={"payload_text": '{"n": "{int(min=1,max=2)}"}'},
        headers=XRW,
    ).json()
    assert ok["ok"] is True

    bad = authed.post(
        "/api/v1/validate",
        json={"payload_text": '{"x": "{otp(length=0)}"}'},
        headers=XRW,
    ).json()
    assert bad["ok"] is False and bad["issues"]


def test_validate_accepts_full_form_payload(authed):
    """Regression: flat `retries` int from GUI forms must not 400 (#user-report)."""
    body = {
        "target": "https://example.com/api",
        "method": "GET",
        "post_type": "json",
        "faker_locale": "en_US",
        "requests": 100,
        "concurrency": 10,
        "delay": 0,
        "timeout": 10,
        "retries": 0,
        "headers_text": '{\n  "X-Test": "{uuid}"\n}\n',
        "payload_text": '{"username": "{username(length=10)}", "age": "{int(min=18, max=80)}"}\n',
    }
    resp = authed.post("/api/v1/validate", json=body, headers=XRW)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_run_start_with_retries_and_error_detail(authed):
    """Start accepts the same shape; config errors surface their reason."""
    ok = authed.post(
        "/api/v1/runs",
        json={"requests": 2, "concurrency": 2, "retries": 1},
        headers=XRW,
    )
    assert ok.status_code == 200, ok.text
    run_id = ok.json()["run_id"]
    authed.post(f"/api/v1/runs/{run_id}/stop", headers=XRW)
    _wait_finished(authed, run_id)

    bad = authed.post(
        "/api/v1/runs",
        json={"target": "not-a-url", "requests": 2},
        headers=XRW,
    )
    assert bad.status_code == 400
    message = bad.json()["error"]["message"]
    assert "Invalid configuration" in message
    assert "target" in message.lower()


def test_multiple_ws_clients_all_receive_broadcast(authed):
    with authed.websocket_connect("/api/v1/ws") as ws_a:
        assert ws_a.receive_json()["type"] == "hello"
        with authed.websocket_connect("/api/v1/ws") as ws_b:
            assert ws_b.receive_json()["type"] == "hello"

            started = authed.post(
                "/api/v1/runs",
                json={"requests": 2, "concurrency": 2},
                headers=XRW,
            )
            run_id = started.json()["run_id"]

            def collect(ws):
                events = []
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    ev = ws.receive_json()
                    events.append(ev)
                    if ev.get("type") in ("run.completed", "run.error"):
                        return events
                return events

            events_a = collect(ws_a)
            events_b = collect(ws_b)
            starts_a = [e for e in events_a if e["type"] == "run.started"]
            starts_b = [e for e in events_b if e["type"] == "run.started"]
            assert starts_a and starts_b
            assert starts_a[0]["run_id"] == run_id == starts_b[0]["run_id"]


def test_cors_headers_respect_origin_list(tmp_path, server_url):

    from fastapi.testclient import TestClient as TC

    from blaze_hammer.web.app import create_app as ca

    _project(tmp_path, f"{server_url}/echo")
    overrides = {"web": {"cors": {"enabled": True}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = ca(settings=settings, project_dir=tmp_path, project_file=tmp_path / "blazehammer.yaml")

    client = TC(app)
    client.__enter__()
    try:
        allowed = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

        denied = client.get("/api/v1/health", headers={"Origin": "https://evil.test"})
        assert "access-control-allow-origin" not in denied.headers
    finally:
        client.__exit__(None, None, None)


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
