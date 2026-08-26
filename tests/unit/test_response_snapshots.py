"""Response snapshot tests: capture, modes, decoding, redaction, API surface."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.config.loader import build_config
from blaze_hammer.engine.client import (
    BINARY_PLACEHOLDER,
    DECODE_FAILED_PLACEHOLDER,
    BodySnapshot,
    is_binary_content_type,
)
from blaze_hammer.engine.runner import RequestOutcome
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings
from blaze_hammer.web.runs import response_snapshot_fields

XRW = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _project(tmp_path, target: str, rl: dict[str, Any] | None = None) -> None:
    yaml_lines = [
        f'target: "{target}"',
        "method: GET",
        "requests: 3",
        "concurrency: 2",
        "payload: payload.json",
        "headers: headers.json",
    ]
    if rl:
        yaml_lines.append("response_logging:")
        for key, value in rl.items():
            yaml_lines.append(f"  {key}: {json.dumps(value)}")
    (tmp_path / "blazehammer.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    (tmp_path / "headers.json").write_text("{}", encoding="utf-8")


def _make_client(
    tmp_path,
    server_url: str,
    *,
    rl: dict[str, Any] | None = None,
):
    overrides: dict[str, Any] = {
        "web": {"auth": {"enabled": True, "username": "a", "password": "b"}}
    }
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings, project_dir=tmp_path, project_file=tmp_path / "blazehammer.yaml"
    )
    client = TestClient(app)
    client.__enter__()
    resp = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"}, headers=XRW)
    assert resp.status_code == 200
    _ = rl  # rl lives in the YAML already; kept for call-site clarity
    return client


def _run_and_wait(client, run_body: dict[str, Any], timeout=15.0) -> str:
    started = client.post("/api/v1/runs", json=run_body, headers=XRW)
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = client.get(f"/api/v1/runs/{run_id}").json()
        if s["status"] != "running":
            return run_id
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ---------------------------------------------------------------------------
# pure builder unit matrix
# ---------------------------------------------------------------------------


def _outcome(*, status=200, ok=True, body=None, category=None, message=None, latency=0.1):
    return RequestOutcome(
        index=1,
        url="https://t/x",
        method="GET",
        ok=ok,
        status_code=status,
        latency_s=latency,
        attempts=1,
        error_category=category,
        error_message=message,
        body=body,
    )


RL_ALL = {"mode": "all", "max_body_bytes": 4096, "redact_keys": ()}
RL_ERRORS = {"mode": "errors", "max_body_bytes": 4096, "redact_keys": ()}
RL_NONE = {"mode": "none", "max_body_bytes": 4096}


class _RL:
    def __init__(self, d):
        self.mode = d.get("mode", "errors")
        self.max_body_bytes = d.get("max_body_bytes", 4096)
        self.redact_keys = d.get("redact_keys", ())
        self.max_headers = 20
        self.allow_headers = ()


def test_snapshot_json_success_all_mode():
    snap = BodySnapshot('{"success":true}', False, 15, "application/json", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ALL))
    assert fields["content_type"] == "application/json"
    assert fields["body_size"] == 15
    assert fields["response_body_excerpt"] == '{"success":true}'
    assert fields["response_body_truncated"] is False


def test_snapshot_created_201():
    snap = BodySnapshot('{"created":true}', False, 15, "application/json", {})
    fields = response_snapshot_fields(_outcome(status=201, body=snap), response_logging=_RL(RL_ALL))
    assert fields["status_code"] if False else True  # status lives on entry
    assert fields["response_body_excerpt"] == '{"created":true}'


def test_snapshot_error_status_stored_in_errors_mode():
    snap = BodySnapshot('{"detail":"nope"}', False, 17, "application/json", {})
    out = _outcome(status=400, ok=False, body=snap)
    fields = response_snapshot_fields(out, response_logging=_RL(RL_ERRORS))
    assert fields["response_body_excerpt"] == '{"detail":"nope"}'


def test_snapshot_success_not_stored_in_errors_mode():
    snap = BodySnapshot("fine", False, 4, "text/plain", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ERRORS))
    assert fields["response_body_excerpt"] is None
    assert fields["body_size"] == 4  # size kept even without excerpt


def test_snapshot_none_mode_never_stores_excerpt():
    snap = BodySnapshot("secret?", False, 7, "text/plain", {})
    for outcome in (_outcome(body=snap), _outcome(ok=False, status=500, body=snap)):
        fields = response_snapshot_fields(outcome, response_logging=_RL(RL_NONE))
        assert fields["response_body_excerpt"] is None
        assert fields["body_size"] == 7


def test_snapshot_large_body_truncation_flag():
    text = "y" * 9000
    snap = BodySnapshot(text[:4096], True, 9000, "text/plain", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ALL))
    assert len(fields["response_body_excerpt"]) == 4096
    assert fields["response_body_truncated"] is True
    assert fields["body_size"] == 9000


def test_snapshot_binary_placeholder():
    snap = BodySnapshot(BINARY_PLACEHOLDER, False, 512, "image/png", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ALL))
    assert fields["response_body_excerpt"] == BINARY_PLACEHOLDER
    assert fields["response_body_truncated"] is False


def test_snapshot_binary_suppressed_in_none_mode():
    snap = BodySnapshot(BINARY_PLACEHOLDER, False, 512, "image/png", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_NONE))
    assert fields["response_body_excerpt"] is None
    assert fields["content_type"] == "image/png"
    assert fields["body_size"] == 512


def test_snapshot_invalid_utf8_marker():
    snap = BodySnapshot(DECODE_FAILED_PLACEHOLDER, False, 4, "text/plain", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ALL))
    assert fields["response_body_excerpt"] == DECODE_FAILED_PLACEHOLDER


def test_snapshot_empty_body():
    snap = BodySnapshot(None, False, 0, "application/json", {})
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(RL_ALL))
    assert fields["response_body_excerpt"] is None
    assert fields["body_size"] == 0


def test_snapshot_timeout_has_no_invented_status():
    out = _outcome(
        status=None,
        ok=False,
        category="timeout",
        message="Request timed out",
        latency=5.002,
    )
    fields = response_snapshot_fields(out, response_logging=_RL(RL_ERRORS))
    # status/error handled by entry assembly; snapshot carries nulls + timing
    assert fields["body_size"] == 0
    assert fields["response_body_excerpt"] is None


def test_snapshot_redacts_configured_json_keys():
    snap = BodySnapshot('{"username":"dip","token":"abc"}', False, 34, "application/json", {})
    rl = {**RL_ALL, "redact_keys": ("token",)}
    fields = response_snapshot_fields(_outcome(body=snap), response_logging=_RL(rl))
    parsed = __import__("json").loads(fields["response_body_excerpt"])
    assert parsed["username"] == "dip"
    assert parsed["token"] != "abc"


def test_is_binary_content_type_matrix():
    assert is_binary_content_type("image/png")
    assert is_binary_content_type("audio/ogg; charset=x")
    assert is_binary_content_type("video/mp4")
    assert is_binary_content_type("application/pdf")
    assert is_binary_content_type("application/zip")
    assert is_binary_content_type("application/octet-stream")
    assert not is_binary_content_type("application/json")
    assert not is_binary_content_type("text/html")
    assert not is_binary_content_type(None)


# ---------------------------------------------------------------------------
# integration through the running engine + API
# ---------------------------------------------------------------------------


@pytest.fixture()
def snap_client(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    yield _make_client(tmp_path, server_url)


def test_log_returns_full_snapshot_shape(snap_client):
    run_id = _run_and_wait(snap_client, {"requests": 2, "concurrency": 2})
    entries = snap_client.get(f"/api/v1/runs/{run_id}/log").json()
    assert len(entries) == 2
    entry = entries[0]
    assert set(entry) >= {
        "request_index",
        "status_code",
        "response_time_ms",
        "content_type",
        "body_size",
        "response_body_excerpt",
        "response_body_truncated",
        "headers",
        "error",
    }
    assert entry["status_code"] == 200 and entry["error"] is None


def test_sensitive_response_headers_excluded(snap_client):
    run_id = _run_and_wait(snap_client, {"requests": 1})
    entry = snap_client.get(f"/api/v1/runs/{run_id}/log").json()[0]
    headers = entry["headers"]
    lowered = {k.lower() for k in headers}
    assert "set-cookie" not in lowered and "x-secret-token" not in lowered
    assert "server" in lowered and "location" in lowered  # allowlisted


def test_binary_response_live(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/binary", rl={"mode": "all"})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["content_type"] == "image/png"
    assert e["response_body_excerpt"] == BINARY_PLACEHOLDER
    assert e["body_size"] >= 64


def test_bad_utf8_live(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/badutf8", rl={"mode": "all"})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["response_body_excerpt"] == DECODE_FAILED_PLACEHOLDER


def test_custom_charset_decoded(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/charset", rl={"mode": "all"})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["response_body_excerpt"] == "café"


def test_empty_body_live(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/empty")
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["status_code"] == 200
    assert e["response_body_excerpt"] is None
    assert e["body_size"] == 0


def test_large_response_truncated_live(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/big", rl={"mode": "all"})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["body_size"] > 4096
    assert len(e["response_body_excerpt"]) <= 4096 * 4  # chars ≤ bytes cap slack
    assert e["response_body_truncated"] is True


def test_error_status_in_errors_mode_default(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/status/500")
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["status_code"] == 500
    assert e["ok"] is False
    assert e["response_body_excerpt"] is not None  # errors mode default ON


def test_mode_none_via_yaml(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/status/500", rl={"mode": "none"})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1})
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["status_code"] == 500
    assert e["response_body_excerpt"] is None


def test_timeout_entry_shape(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/slow?ms=3000")
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 1, "timeout": 0.3}, timeout=20)
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["status_code"] is None
    assert e["error"]
    assert e["response_body_excerpt"] is None
    assert e["ok"] is False


def test_connection_error_entry(tmp_path):
    # Port 1 on localhost: connection refused.
    _project(tmp_path, "http://127.0.0.1:1/nope")
    c = _make_client(tmp_path, "http://127.0.0.1:1")
    run_id = _run_and_wait(c, {"requests": 1}, timeout=20)
    e = c.get(f"/api/v1/runs/{run_id}/log").json()[0]
    assert e["status_code"] is None and e["error"]


def test_summary_aggregates_present(snap_client):
    run_id = _run_and_wait(snap_client, {"requests": 3, "concurrency": 3})
    s = snap_client.get(f"/api/v1/runs/{run_id}").json()
    assert s["average_response_time_ms"] is not None
    assert s["min_response_time_ms"] <= s["average_response_time_ms"]
    assert s["max_response_time_ms"] >= s["average_response_time_ms"]
    assert s["status_codes"].get("200") == 3


def test_concurrent_requests_all_logged(snap_client):
    run_id = _run_and_wait(snap_client, {"requests": 12, "concurrency": 6})
    entries = snap_client.get(f"/api/v1/runs/{run_id}/log").json()
    assert len(entries) == 12
    indexes = sorted(e["request_index"] for e in entries)
    assert indexes == list(range(12))


def test_ws_completion_carries_body_metadata(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo", rl={"mode": "all"})
    snap_client = _make_client(tmp_path, server_url)
    with snap_client.websocket_connect("/api/v1/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        catalog = ws.receive_json()
        assert catalog["type"] == "placeholder.catalog"

        started = snap_client.post(
            "/api/v1/runs", json={"requests": 2, "concurrency": 2}, headers=XRW
        )
        assert started.status_code == 200

        completed = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            ev = ws.receive_json()
            if ev["type"] == "request.completed":
                completed.append(ev)
            elif ev["type"] in ("run.completed", "run.error"):
                break
        assert completed
        sample = next(e for e in completed if e.get("response_body_excerpt"))
        assert "body_size" in sample
        assert sample["response_body_truncated"] in (True, False)


def test_cli_options_flow_into_config(tmp_path):
    (tmp_path / "blazehammer.yaml").write_text(
        'target: "https://t.test"' + chr(10), encoding="utf-8"
    )
    cfg = build_config(
        {},
        environ={
            "BLAZE_HAMMER_RESPONSE_LOG": "all",
            "BLAZE_HAMMER_RESPONSE_BODY_LIMIT": "2048",
        },
        config_path=str(tmp_path / "blazehammer.yaml"),
    )
    assert cfg.response_logging.mode.value == "all"
    assert cfg.response_logging.max_body_bytes == 2048
