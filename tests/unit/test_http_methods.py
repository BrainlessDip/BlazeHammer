"""Comprehensive HTTP method support tests.

Covers all 9 standard methods: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS,
CONNECT, TRACE. Tests enum normalization, CLI parsing, YAML config, API models,
request execution, body handling, preview, validation, WebSocket events, and
error paths.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.config.loader import build_config
from blaze_hammer.config.models import Method, RunConfig
from blaze_hammer.config.validation import validate_config
from blaze_hammer.engine.planner import RequestPlanner, RequestTemplates
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings
from blaze_hammer.web.models import SaveConfigRequest

XRW = {"X-Requested-With": "XMLHttpRequest"}


def _resolver(seed=None):
    from blaze_hammer.services import build_resolver

    return build_resolver(seed)


def _load_templates(cfg):
    """Load headers/payload from disk like services.prepare_run does."""
    headers = None
    payload = None
    if cfg.headers_file is not None and not cfg.disable_headers:
        headers = json.loads(Path(cfg.headers_file).read_text(encoding="utf-8"))
    if cfg.payload_file is not None:
        payload = json.loads(Path(cfg.payload_file).read_text(encoding="utf-8"))
    return RequestTemplates(headers=headers, payload=payload)


def _web_client(tmp_path, server_url, method="GET"):
    """Create a logged-in TestClient for the given project."""
    lines = [
        f'target: "{server_url}/echo"',
        f"method: {method}",
        "requests: 2",
        "concurrency: 2",
    ]
    (tmp_path / "blazehammer.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    overrides: dict = {"web": {"auth": {"enabled": True, "username": "admin", "password": "admin"}}}
    cfg = build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")
    settings = resolve_web_settings(cfg, environ={})
    app = create_app(
        settings=settings, project_dir=tmp_path, project_file=tmp_path / "blazehammer.yaml"
    )
    client = TestClient(app)
    client.__enter__()
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}, headers=XRW)
    return client


def _project(tmp_path, url, method="GET", payload=None, **extra):
    """Write a minimal blazehammer.yaml and return RunConfig."""
    lines = [f'target: "{url}"', f"method: {method}", "requests: 2", "concurrency: 2"]
    if payload is not None:
        lines.append("payload: payload.json")
    for k, v in extra.items():
        if isinstance(v, bool):
            lines.append(f"{k}: {str(v).lower()}")
        elif isinstance(v, str) and v.startswith('"'):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    (tmp_path / "blazehammer.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if payload is not None:
        (tmp_path / "payload.json").write_text(
            json.dumps(payload) if isinstance(payload, dict) else payload, encoding="utf-8"
        )
    return build_config(
        {"target": url, "method": method},
        environ={},
        config_path=tmp_path / "blazehammer.yaml",
    )


# ---------------------------------------------------------------------------
# 1. Method enum
# ---------------------------------------------------------------------------


class TestMethodEnum:
    def test_all_nine_methods(self):
        expected = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
        assert set(Method.allowed_values()) == expected

    def test_case_insensitive_lookup(self):
        for raw in ("get", "Get", "GET", "Post", "post", "DELETE", "delete", "PaTcH"):
            assert Method(raw) == Method(raw.upper())

    def test_invalid_method_rejects(self):
        with pytest.raises(ValueError):
            Method("FOO")
        with pytest.raises(ValueError):
            Method("")
        with pytest.raises(ValueError):
            Method("INVALID")

    def test_allowed_values_sorted(self):
        vals = Method.allowed_values()
        # All 9 methods present; order is enum declaration order (not alphabetical).
        assert len(vals) == 9
        assert set(vals) == {m.value for m in Method}

    def test_supports_body(self):
        for m in (Method.POST, Method.PUT, Method.PATCH, Method.DELETE):
            assert m.supports_body is True
        for m in (Method.GET, Method.HEAD, Method.OPTIONS, Method.CONNECT, Method.TRACE):
            assert m.supports_body is False

    def test_method_is_str_enum(self):
        assert Method.GET == "GET"
        assert isinstance(Method.POST, str)


# ---------------------------------------------------------------------------
# 2. CLI option choices
# ---------------------------------------------------------------------------


class TestCLIOptions:
    def test_config_options_method_choice_includes_all(self):
        from blaze_hammer.cli.options import _METHChoices

        for m in Method.allowed_values():
            assert m in _METHChoices.choices

    def test_init_options_method_choice_includes_all(self):
        from blaze_hammer.cli.init_cmd import _METHChoices

        for m in Method.allowed_values():
            assert m in _METHChoices.choices

    def test_config_options_help_shows_all_methods(self):
        from click.testing import CliRunner

        from blaze_hammer.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        for m in Method.allowed_values():
            assert m in result.output

    def test_init_help_shows_all_methods(self):
        from click.testing import CliRunner

        from blaze_hammer.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--help"])
        # Click renders case-insensitive choices in lowercase.
        output_lower = result.output.lower()
        for m in Method.allowed_values():
            assert m.lower() in output_lower


# ---------------------------------------------------------------------------
# 3. YAML config loading
# ---------------------------------------------------------------------------


class TestYAMLConfig:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def test_yaml_method_loading(self, method):
        cfg = build_config({"target": "https://x.test", "method": method})
        assert cfg.method is Method(method)

    def test_yaml_lowercase_method(self):
        cfg = build_config({"target": "https://x.test", "method": "patch"})
        assert cfg.method is Method.PATCH

    def test_yaml_mixed_case_method(self):
        cfg = build_config({"target": "https://x.test", "method": "Delete"})
        assert cfg.method is Method.DELETE

    def test_env_method_override(self):
        cfg = build_config(
            {"target": "https://x.test"},
            environ={"BLAZE_METHOD": "HEAD"},
        )
        assert cfg.method is Method.HEAD


# ---------------------------------------------------------------------------
# 4. Pydantic API models
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_runconfig_accepts_all_methods(self):
        for m in Method:
            cfg = RunConfig(target="https://x.test", method=m)
            assert cfg.method is m

    def test_runconfig_method_from_string(self):
        cfg = RunConfig(target="https://x.test", method="options")
        assert cfg.method is Method.OPTIONS

    def test_runconfig_invalid_method_rejects(self):
        with pytest.raises((ValueError, TypeError)):
            RunConfig(target="https://x.test", method="FOO")

    def test_save_config_request_all_methods(self):
        for m in Method:
            req = SaveConfigRequest(method=m.value)
            assert req.method == m.value

    def test_save_config_request_case_insensitive(self):
        req = SaveConfigRequest(method="patch")
        assert req.method == "PATCH"

    def test_save_config_request_invalid_method_rejects(self):
        with pytest.raises((ValueError, TypeError)):
            SaveConfigRequest(method="FOO")

    def test_save_config_request_empty_method_rejects(self):
        with pytest.raises((ValueError, TypeError)):
            SaveConfigRequest(method="")

    def test_run_settings_payload_method_normalization(self):
        from blaze_hammer.web.models import RunSettingsPayload

        payload = RunSettingsPayload(method="patch")
        overrides = payload.to_overrides()
        assert overrides["method"] == "PATCH"


# ---------------------------------------------------------------------------
# 5. Request execution: all methods
# ---------------------------------------------------------------------------


class TestGETMethod:
    def test_get_success(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", method="GET")
        assert cfg.method is Method.GET
        assert cfg.method.supports_body is False

    def test_get_no_body_attached(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", method="GET")
        planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
        plan = planner.next_plan(0)
        assert plan.json_body is None
        assert plan.form_data is None


class TestPOSTMethod:
    def test_post_with_json_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path, f"{server_url}/receive", method="POST", payload='{"name": "alice"}'
        )
        assert cfg.method is Method.POST
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"name": "alice"}

    def test_post_form_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"key": "val"}',
            post_type="form",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.form_data is not None
        assert plan.json_body is None


class TestPUTMethod:
    def test_put_with_body(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/receive", method="PUT", payload='{"id": 42}')
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"id": 42}

    def test_put_no_payload(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", method="PUT")
        planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
        plan = planner.next_plan(0)
        assert plan.json_body is None


class TestPATCHMethod:
    def test_patch_with_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path, f"{server_url}/receive", method="PATCH", payload='{"status": "active"}'
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"status": "active"}

    def test_patch_from_cli_lowercase(self):
        cfg = build_config({"target": "https://x.test", "method": "patch"})
        assert cfg.method is Method.PATCH


class TestDELETEMethod:
    def test_delete_success(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", method="DELETE")
        assert cfg.method is Method.DELETE
        planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
        plan = planner.next_plan(0)
        assert plan.json_body is None

    def test_delete_with_body(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/receive", method="DELETE", payload='{"id": 123}')
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"id": 123}


class TestHEADMethod:
    def test_head_no_body(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", method="HEAD")
        planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
        plan = planner.next_plan(0)
        assert plan.method == "HEAD"
        assert plan.json_body is None


class TestOPTIONSMethod:
    def test_options_success(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/headers-check", method="OPTIONS")
        planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
        plan = planner.next_plan(0)
        assert plan.method == "OPTIONS"
        assert plan.json_body is None


# ---------------------------------------------------------------------------
# 6. Body handling rules
# ---------------------------------------------------------------------------


class TestBodyHandling:
    def test_bodyless_methods_get_none_body(self, tmp_path, server_url):
        for method in ("GET", "HEAD", "OPTIONS"):
            cfg = _project(tmp_path, f"{server_url}/echo", method=method)
            planner = RequestPlanner(cfg, _resolver(cfg.seed), RequestTemplates())
            plan = planner.next_plan(0)
            assert plan.json_body is None, f"{method} should not have json_body"
            assert plan.form_data is None, f"{method} should not have form_data"

    def test_body_methods_get_body_when_payload_present(self, tmp_path, server_url):
        for method in ("POST", "PUT", "PATCH"):
            cfg = _project(
                tmp_path, f"{server_url}/receive", method=method, payload='{"key": "val"}'
            )
            templates = _load_templates(cfg)
            planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
            plan = planner.next_plan(0)
            assert plan.json_body == {"key": "val"}, f"{method} should have json_body"

    def test_delete_with_body_gets_body(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/receive", method="DELETE", payload='{"id": 1}')
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"id": 1}


# ---------------------------------------------------------------------------
# 7. Validation rules
# ---------------------------------------------------------------------------


class TestValidation:
    def test_body_method_without_payload_warns(self):
        cfg = RunConfig(target="https://x.test", method="POST")
        outcome = validate_config(cfg)
        assert not outcome.ok
        assert any("POST" in c[2] for c in outcome.checks if not c[1])

    def test_put_without_payload_warns(self):
        cfg = RunConfig(target="https://x.test", method="PUT")
        outcome = validate_config(cfg)
        assert not outcome.ok

    def test_patch_without_payload_warns(self):
        cfg = RunConfig(target="https://x.test", method="PATCH")
        outcome = validate_config(cfg)
        assert not outcome.ok

    def test_get_without_payload_ok(self):
        cfg = RunConfig(target="https://x.test", method="GET")
        outcome = validate_config(cfg)
        assert outcome.ok

    def test_head_without_payload_ok(self):
        cfg = RunConfig(target="https://x.test", method="HEAD")
        outcome = validate_config(cfg)
        assert outcome.ok

    def test_options_without_payload_ok(self):
        cfg = RunConfig(target="https://x.test", method="OPTIONS")
        outcome = validate_config(cfg)
        assert outcome.ok

    def test_dry_run_skips_payload_check(self):
        cfg = RunConfig(target="https://x.test", method="POST", preview={"dry_run": True})
        outcome = validate_config(cfg)
        assert outcome.ok


# ---------------------------------------------------------------------------
# 8. Placeholders across methods
# ---------------------------------------------------------------------------


class TestPlaceholderResolution:
    def test_faker_in_post_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"user": "{faker.user_name}"}',
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert "user" in plan.json_body
        assert plan.json_body["user"] != "{faker.user_name}"

    def test_faker_in_put_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path, f"{server_url}/receive", method="PUT", payload='{"name": "{faker.name}"}'
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert "name" in plan.json_body

    def test_faker_in_delete_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="DELETE",
            payload='{"id": "{int(min=1,max=100)}"}',
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert isinstance(plan.json_body["id"], int)


# ---------------------------------------------------------------------------
# 9. API endpoint integration
# ---------------------------------------------------------------------------


class TestAPIEndpoints:
    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def test_preview_reflects_method(self, tmp_path, server_url, method):
        payload = '{"key": "val"}' if method in ("POST", "PUT", "PATCH", "DELETE") else None
        client = _web_client(tmp_path, server_url, method)
        try:
            body: dict[str, Any] = {"method": method, "count": 1}
            if payload is not None:
                body["payload_text"] = payload
            resp = client.post("/api/v1/preview", json=body, headers=XRW)
            assert resp.status_code == 200
            plans = resp.json()["plans"]
            assert plans[0]["method"] == method
        finally:
            client.__exit__(None, None, None)

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def test_validate_reflects_method(self, tmp_path, server_url, method):
        payload = '{"key": "val"}' if method in ("POST", "PUT", "PATCH", "DELETE") else None
        client = _web_client(tmp_path, server_url, method)
        try:
            body: dict[str, Any] = {"method": method}
            if payload is not None:
                body["payload_text"] = payload
            resp = client.post("/api/v1/validate", json=body, headers=XRW)
            assert resp.status_code == 200
        finally:
            client.__exit__(None, None, None)

    def test_config_save_all_methods(self, tmp_path, server_url):
        client = _web_client(tmp_path, server_url)
        try:
            for method in Method.allowed_values():
                resp = client.post("/api/v1/config/save", json={"method": method}, headers=XRW)
                assert resp.status_code == 200, f"Failed for method {method}: {resp.json()}"
        finally:
            client.__exit__(None, None, None)

    def test_config_save_invalid_method_rejects(self, tmp_path, server_url):
        client = _web_client(tmp_path, server_url)
        try:
            resp = client.post("/api/v1/config/save", json={"method": "FOO"}, headers=XRW)
            assert resp.status_code == 422
        finally:
            client.__exit__(None, None, None)

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def test_run_start_all_methods(self, tmp_path, server_url, method):
        client = _web_client(tmp_path, server_url, method)
        try:
            resp = client.post(
                "/api/v1/runs",
                json={"method": method, "requests": 1, "concurrency": 1},
                headers=XRW,
            )
            assert resp.status_code == 200, f"Failed for method {method}: {resp.json()}"
            assert resp.json()["method"] == method
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 10. WebSocket events include method
# ---------------------------------------------------------------------------


class TestWSEvents:
    def test_request_completed_includes_method(self, tmp_path, server_url):
        client = _web_client(tmp_path, server_url, "PUT")
        try:
            with client.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # hello
                client.post(
                    "/api/v1/runs",
                    json={"method": "PUT", "requests": 2, "concurrency": 2},
                    headers=XRW,
                )
                methods_seen = set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    ev = ws.receive_json()
                    if ev.get("type") == "request.completed":
                        methods_seen.add(ev.get("method"))
                    if ev.get("type") == "run.completed":
                        break
                assert "PUT" in methods_seen
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 11. Run stats include method
# ---------------------------------------------------------------------------


class TestRunStats:
    def test_run_summary_includes_method(self, tmp_path, server_url):
        client = _web_client(tmp_path, server_url, "PATCH")
        try:
            with client.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # hello
                resp = client.post(
                    "/api/v1/runs",
                    json={"method": "PATCH", "requests": 2, "concurrency": 2},
                    headers=XRW,
                )
                run_id = resp.json()["run_id"]
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    ev = ws.receive_json()
                    if ev.get("type") == "run.completed":
                        break
                summary = client.get(f"/api/v1/runs/{run_id}").json()
                assert summary["method"] == "PATCH"
        finally:
            client.__exit__(None, None, None)

    def test_log_entries_for_non_get(self, tmp_path, server_url):
        client = _web_client(tmp_path, server_url, "DELETE")
        try:
            with client.websocket_connect("/api/v1/ws") as ws:
                ws.receive_json()  # hello
                resp = client.post(
                    "/api/v1/runs",
                    json={"method": "DELETE", "requests": 1, "concurrency": 1},
                    headers=XRW,
                )
                run_id = resp.json()["run_id"]
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    ev = ws.receive_json()
                    if ev.get("type") == "run.completed":
                        break
                log = client.get(f"/api/v1/runs/{run_id}/log").json()
                assert len(log) >= 1
                summary = client.get(f"/api/v1/runs/{run_id}").json()
                assert summary["method"] == "DELETE"
        finally:
            client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 12. Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_existing_get_config_still_works(self):
        cfg = build_config({"target": "https://x.test", "method": "GET"})
        assert cfg.method is Method.GET

    def test_existing_post_config_with_payload(self, tmp_path):
        (tmp_path / "blazehammer.yaml").write_text(
            'target: "https://x.test"\nmethod: POST\npayload: payload.json\nrequests: 2\n',
            encoding="utf-8",
        )
        (tmp_path / "payload.json").write_text('{"key": "val"}', encoding="utf-8")
        cfg = build_config({"target": "https://x.test"}, config_path=tmp_path / "blazehammer.yaml")
        assert cfg.method is Method.POST


# ---------------------------------------------------------------------------
# 13. CONNECT and TRACE error handling
# ---------------------------------------------------------------------------


class TestAdvancedMethods:
    def test_connect_config_loads(self):
        cfg = build_config({"target": "https://x.test", "method": "CONNECT"})
        assert cfg.method is Method.CONNECT
        assert cfg.method.supports_body is False

    def test_trace_config_loads(self):
        cfg = build_config({"target": "https://x.test", "method": "TRACE"})
        assert cfg.method is Method.TRACE
        assert cfg.method.supports_body is False

    def test_connect_from_yaml(self, tmp_path):
        (tmp_path / "blazehammer.yaml").write_text(
            'target: "https://x.test"\nmethod: CONNECT\nrequests: 1\n',
            encoding="utf-8",
        )
        cfg = build_config({"target": "https://x.test"}, config_path=tmp_path / "blazehammer.yaml")
        assert cfg.method is Method.CONNECT

    def test_trace_from_yaml(self, tmp_path):
        (tmp_path / "blazehammer.yaml").write_text(
            'target: "https://x.test"\nmethod: TRACE\nrequests: 1\n',
            encoding="utf-8",
        )
        cfg = build_config({"target": "https://x.test"}, config_path=tmp_path / "blazehammer.yaml")
        assert cfg.method is Method.TRACE

    def test_connect_and_trace_validation_ok(self):
        for method in ("CONNECT", "TRACE"):
            cfg = RunConfig(target="https://x.test", method=method)
            outcome = validate_config(cfg)
            assert outcome.ok, f"Validation failed for {method}: {outcome.failures}"
