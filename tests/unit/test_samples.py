"""Sample store tests: representative collection, truncation, redaction, API."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.config.loader import build_config
from blaze_hammer.engine.client import BodySnapshot
from blaze_hammer.engine.runner import RequestOutcome
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings
from blaze_hammer.web.samples import SampleStore, _body_excerpt, _truncate_text

XRW = {"X-Requested-With": "XMLHttpRequest"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

DEFAULT_OPTS = {
    "enabled": True,
    "max_per_run": 5,
    "max_request_body_size": 1000,
    "max_response_body_size": 1000,
}


def _opts(**overrides: Any) -> type:
    return type("O", (), {**DEFAULT_OPTS, **overrides})()


def _project(tmp_path, target: str, samples_cfg: dict[str, Any] | None = None) -> None:
    yaml_lines = [
        f'target: "{target}"',
        "method: GET",
        "requests: 3",
        "concurrency: 2",
        "payload: payload.json",
        "headers: headers.json",
    ]
    if samples_cfg:
        yaml_lines.append("samples:")
        for key, value in samples_cfg.items():
            yaml_lines.append(f"  {key}: {json.dumps(value)}")
    (tmp_path / "blazehammer.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    (tmp_path / "headers.json").write_text("{}", encoding="utf-8")


def _make_client(tmp_path, server_url: str):
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


def _outcome(
    *,
    index=1,
    status=200,
    ok=True,
    body=None,
    category=None,
    message=None,
    latency=0.1,
    headers=None,
    payload=None,
    cookies=None,
    method="GET",
):
    return RequestOutcome(
        index=index,
        url="https://example.com/api/test",
        method=method,
        ok=ok,
        status_code=status,
        latency_s=latency,
        attempts=1,
        error_category=category,
        error_message=message,
        resolved_headers=headers,
        resolved_payload=payload,
        resolved_cookies=cookies,
        body=body,
    )


# ---------------------------------------------------------------------------
# truncate_text
# ---------------------------------------------------------------------------


def test_truncate_text_short():
    text, truncated = _truncate_text("hello", 100)
    assert text == "hello"
    assert truncated is False


def test_truncate_text_exact():
    text, truncated = _truncate_text("hello", 5)
    assert text == "hello"
    assert truncated is False


def test_truncate_text_over():
    text, truncated = _truncate_text("hello world", 5)
    assert len(text.encode("utf-8")) <= 5
    assert truncated is True


def test_truncate_text_multibyte():
    # 3-byte UTF-8 character (snowman)
    text = "\u2603" * 10  # 30 bytes total
    result, truncated = _truncate_text(text, 10)
    assert truncated is True
    assert len(result.encode("utf-8")) <= 10
    # Must not split a multi-byte char
    decoded = result.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    assert decoded == result


# ---------------------------------------------------------------------------
# body_excerpt
# ---------------------------------------------------------------------------


def test_body_excerpt_none():
    assert _body_excerpt(None, max_size=1000) == {}


def test_body_excerpt_binary():
    result = _body_excerpt("[binary image/png]", max_size=1000, content_type="image/png")
    assert result["text"] == "[binary image/png]"
    assert result["truncated"] is False


def test_body_excerpt_json():
    result = _body_excerpt('{"key":"value"}', max_size=1000, content_type="application/json")
    assert result["parsed"] == {"key": "value"}
    assert result["text"] is None
    assert result["truncated"] is False


def test_body_excerpt_truncated():
    big = "x" * 500
    result = _body_excerpt(big, max_size=100)
    assert result["truncated"] is True
    assert len(result["text"].encode("utf-8")) <= 100


# ---------------------------------------------------------------------------
# SampleStore — classification logic
# ---------------------------------------------------------------------------


class TestSampleStoreClassification:
    def test_first_request_always_captured(self):
        store = SampleStore(_opts())
        store.record(_outcome(index=0))
        assert len(store.samples) == 1
        assert store.samples[0]["reason"] == "first"

    def test_first_success_captured(self):
        store = SampleStore(_opts())
        store.record(_outcome(index=0))  # first
        store.record(_outcome(index=1, ok=True, status=200))  # first_success
        assert len(store.samples) == 2
        assert store.samples[1]["reason"] == "first_success"

    def test_first_failure_captured(self):
        store = SampleStore(_opts())
        store.record(_outcome(index=0))  # first
        store.record(
            _outcome(
                index=1,
                ok=False,
                status=500,
                category="http",
                message="server error",
            )
        )
        assert len(store.samples) == 2
        assert store.samples[1]["reason"] == "first_failure"
        assert store.samples[1]["ok"] is False

    def test_distinct_status_codes_captured(self):
        store = SampleStore(_opts(max_per_run=10))
        store.record(_outcome(index=0))  # first (200, ok=True)
        store.record(_outcome(index=1, ok=False, status=500))  # first_failure
        store.record(_outcome(index=2, status=200))  # first_success
        store.record(_outcome(index=3, status=201))  # status_201
        store.record(_outcome(index=4, status=204))  # status_204
        store.record(_outcome(index=5, status=201))  # duplicate — skipped
        reasons = [s["reason"] for s in store.samples]
        assert "status_201" in reasons
        assert "status_204" in reasons
        assert len(store.samples) == 5

    def test_duplicate_status_not_captured(self):
        store = SampleStore(_opts(max_per_run=10))
        store.record(_outcome(index=0))  # first (200)
        store.record(_outcome(index=1, status=201))
        store.record(_outcome(index=2, status=200))  # duplicate 200 — skipped
        assert len(store.samples) == 2

    def test_respects_max_per_run(self):
        store = SampleStore(_opts(max_per_run=3))
        for i in range(10):
            store.record(_outcome(index=i, status=200 + i))
        assert len(store.samples) == 3

    def test_sample_has_expected_shape(self):
        store = SampleStore(_opts())
        store.record(
            _outcome(
                index=0,
                headers={"Authorization": "Bearer tok"},
                payload={"user": "alice"},
                cookies={"session": "abc123"},
            )
        )
        sample = store.samples[0]
        assert "id" in sample
        assert "timestamp" in sample
        assert sample["reason"] == "first"
        assert sample["request"]["method"] == "GET"
        assert sample["request"]["url"] == "https://example.com/api/test"
        assert "Authorization" in sample["request"]["headers"]
        assert sample["request"]["body"]["parsed"] == {"user": "alice"}
        assert sample["request"]["cookies"]["session"] == "abc123"
        assert sample["response"]["status_code"] == 200


# ---------------------------------------------------------------------------
# SampleStore — redaction
# ---------------------------------------------------------------------------


class TestSampleStoreRedaction:
    def test_cookie_redaction(self):
        store = SampleStore(_opts(), sensitive_names=("session", "token"))
        store.record(
            _outcome(
                index=0,
                cookies={"session": "secret123", "lang": "en"},
            )
        )
        cookies = store.samples[0]["request"]["cookies"]
        assert cookies["session"] != "secret123"
        assert cookies["lang"] == "en"

    def test_header_redaction(self):
        store = SampleStore(_opts(), sensitive_names=("authorization",))
        store.record(
            _outcome(
                index=0,
                headers={"Authorization": "Bearer secret", "Accept": "application/json"},
            )
        )
        headers = store.samples[0]["request"]["headers"]
        assert headers["Authorization"] != "Bearer secret"
        assert headers["Accept"] == "application/json"

    def test_payload_redaction(self):
        store = SampleStore(_opts(), sensitive_names=("password",))
        store.record(
            _outcome(
                index=0,
                payload={"username": "admin", "password": "s3cret"},
            )
        )
        body = store.samples[0]["request"]["body"]["parsed"]
        assert body["username"] == "admin"
        assert body["password"] != "s3cret"


# ---------------------------------------------------------------------------
# SampleStore — body truncation
# ---------------------------------------------------------------------------


class TestSampleStoreTruncation:
    def test_request_body_truncated(self):
        store = SampleStore(_opts(max_request_body_size=50))
        big_payload = {"data": "x" * 200}
        store.record(_outcome(index=0, payload=big_payload))
        body = store.samples[0]["request"]["body"]
        assert body["truncated"] is True

    def test_response_body_truncated(self):
        store = SampleStore(_opts(max_response_body_size=50))
        snap = BodySnapshot("x" * 200, False, 200, "text/plain", {})
        store.record(_outcome(index=0, body=snap))
        body = store.samples[0]["response"]["body"]
        assert body["truncated"] is True


# ---------------------------------------------------------------------------
# SampleStore — response side
# ---------------------------------------------------------------------------


class TestSampleStoreResponse:
    def test_response_fields_populated(self):
        store = SampleStore(_opts())
        snap = BodySnapshot(
            '{"ok":true}',
            False,
            12,
            "application/json",
            {"X-Custom": "val"},
        )
        store.record(_outcome(index=0, body=snap, status=201))
        resp = store.samples[0]["response"]
        assert resp["status_code"] == 201
        assert resp["content_type"] == "application/json"
        assert resp["size"] == 12
        assert resp["headers"]["X-Custom"] == "val"
        assert resp["body"]["parsed"] == {"ok": True}

    def test_no_body(self):
        store = SampleStore(_opts())
        store.record(_outcome(index=0, body=None, status=204))
        resp = store.samples[0]["response"]
        assert resp["body"] is None
        assert resp["size"] == 0


# ---------------------------------------------------------------------------
# integration: live API
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_client(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    yield _make_client(tmp_path, server_url)


def test_samples_endpoint_returns_data(sample_client):
    run_id = _run_and_wait(sample_client, {"requests": 3, "concurrency": 2})
    samples = sample_client.get(f"/api/v1/runs/{run_id}/samples").json()
    assert len(samples) >= 1
    s = samples[0]
    assert "id" in s
    assert "request" in s
    assert "response" in s
    assert s["request"]["method"] == "GET"
    assert s["response"]["status_code"] == 200


def test_sample_count_in_summary(sample_client):
    run_id = _run_and_wait(sample_client, {"requests": 3, "concurrency": 2})
    summary = sample_client.get(f"/api/v1/runs/{run_id}").json()
    assert summary["sample_count"] >= 1


def test_samples_disabled(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo", samples_cfg={"enabled": False})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 2, "concurrency": 1})
    samples = c.get(f"/api/v1/runs/{run_id}/samples").json()
    assert samples == []
    summary = c.get(f"/api/v1/runs/{run_id}").json()
    assert summary["sample_count"] == 0


def test_samples_empty_for_unknown_run(sample_client):
    resp = sample_client.get("/api/v1/runs/nonexistent/samples")
    assert resp.status_code == 200
    assert resp.json() == []


def test_samples_with_failure(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/status/500")
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 2, "concurrency": 1})
    samples = c.get(f"/api/v1/runs/{run_id}/samples").json()
    reasons = [s["reason"] for s in samples]
    assert "first" in reasons
    assert "first_failure" in reasons
    fail_sample = next(s for s in samples if s["reason"] == "first_failure")
    assert fail_sample["ok"] is False
    assert fail_sample["response"]["status_code"] == 500


def test_samples_respects_max_per_run(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo", samples_cfg={"max_per_run": 2})
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 10, "concurrency": 5})
    samples = c.get(f"/api/v1/runs/{run_id}/samples").json()
    assert len(samples) == 2


def test_samples_with_cookies(tmp_path, server_url):
    """When request headers include Cookie, samples capture them."""
    _project(tmp_path, f"{server_url}/echo")
    overrides = {"web": {"auth": {"enabled": True, "username": "a", "password": "b"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings, project_dir=tmp_path, project_file=tmp_path / "blazehammer.yaml"
    )
    c = TestClient(app)
    c.__enter__()
    c.post("/api/v1/auth/login", json={"username": "a", "password": "b"}, headers=XRW)
    run_id = _run_and_wait(c, {"requests": 1, "concurrency": 1})
    samples = c.get(f"/api/v1/runs/{run_id}/samples").json()
    # Cookie header from auth session should be in samples
    assert len(samples) >= 1
    s = samples[0]
    assert "request" in s
    assert "cookies" in s["request"]


def test_samples_summary_shape(tmp_path, server_url):
    """RunSummary includes sample_count."""
    _project(tmp_path, f"{server_url}/echo")
    c = _make_client(tmp_path, server_url)
    run_id = _run_and_wait(c, {"requests": 3, "concurrency": 2})
    summary = c.get(f"/api/v1/runs/{run_id}").json()
    assert "sample_count" in summary
    assert isinstance(summary["sample_count"], int)
    assert summary["sample_count"] >= 1
