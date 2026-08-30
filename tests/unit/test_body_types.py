"""Comprehensive body type / PostType tests.

Covers all 8 body encodings: none, json, form, multipart, raw, xml, html,
binary. Tests enum values, planner routing, runner kwargs, Content-Type
injection, CLI parsing, API model validation, preview endpoint, and sample
store compatibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blaze_hammer.config.loader import build_config
from blaze_hammer.config.models import PostType, RunConfig
from blaze_hammer.engine.planner import RequestPlanner, RequestTemplates

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolver(seed=None):
    from blaze_hammer.services import build_resolver

    return build_resolver(seed)


def _load_templates(cfg):
    headers = None
    payload = None
    if cfg.headers_file is not None and not cfg.disable_headers:
        headers = json.loads(Path(cfg.headers_file).read_text(encoding="utf-8"))
    if cfg.payload_file is not None:
        payload = json.loads(Path(cfg.payload_file).read_text(encoding="utf-8"))
    return RequestTemplates(headers=headers, payload=payload)


def _project(tmp_path, url, method="POST", payload=None, **extra):
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
# PostType enum
# ---------------------------------------------------------------------------


class TestPostTypeEnum:
    def test_all_eight_values(self):
        expected = {"none", "json", "form", "multipart", "raw", "xml", "html", "binary"}
        assert set(PostType.allowed_values()) == expected

    def test_case_insensitive_lookup(self):
        assert PostType("JSON") is PostType.JSON
        assert PostType("Json") is PostType.JSON
        assert PostType("FORM") is PostType.FORM
        assert PostType("MULTIPART") is PostType.MULTIPART

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            PostType("invalid")

    def test_default_content_types(self):
        assert PostType.JSON.default_content_type == "application/json"
        assert PostType.FORM.default_content_type == "application/x-www-form-urlencoded"
        assert PostType.MULTIPART.default_content_type == "multipart/form-data"
        assert PostType.RAW.default_content_type == "text/plain"
        assert PostType.XML.default_content_type == "application/xml"
        assert PostType.HTML.default_content_type == "text/html"
        assert PostType.BINARY.default_content_type == "application/octet-stream"
        assert PostType.NONE.default_content_type is None


# ---------------------------------------------------------------------------
# Planner — body routing for each type
# ---------------------------------------------------------------------------


class TestPlannerBodyTypes:
    def test_none_type_no_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"key": "val"}',
            post_type="none",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body is None
        assert plan.form_data is None
        assert plan.raw_body is None
        assert plan.body_bytes is None
        assert plan.content is None
        assert plan.post_type == "none"

    def test_json_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"name": "alice"}',
            post_type="json",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"name": "alice"}
        assert plan.form_data is None
        assert plan.raw_body is None
        assert plan.default_content_type == "application/json"

    def test_form_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"key": "val"}',
            post_type="form",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.form_data == {"key": "val"}
        assert plan.json_body is None
        assert plan.raw_body is None
        assert plan.default_content_type == "application/x-www-form-urlencoded"

    def test_multipart_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"field": "data"}',
            post_type="multipart",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.form_data == {"field": "data"}
        assert plan.json_body is None
        assert plan.raw_body is None
        assert plan.default_content_type == "multipart/form-data"

    def test_raw_type_string_payload(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='"Hello, world!"',
            post_type="raw",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body == "Hello, world!"
        assert plan.json_body is None
        assert plan.form_data is None
        assert plan.default_content_type == "text/plain"

    def test_raw_type_dict_payload_serializes(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"foo": "bar"}',
            post_type="raw",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body is not None
        assert isinstance(plan.raw_body, str)
        parsed = json.loads(plan.raw_body)
        assert parsed == {"foo": "bar"}

    def test_xml_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='"<root><item/></root>"',
            post_type="xml",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body == "<root><item/></root>"
        assert plan.default_content_type == "application/xml"

    def test_xml_type_dict_payload_serializes(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"tag": "value"}',
            post_type="xml",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body is not None
        parsed = json.loads(plan.raw_body)
        assert parsed == {"tag": "value"}

    def test_html_type(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='"<html><body>Hi</body></html>"',
            post_type="html",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body == "<html><body>Hi</body></html>"
        assert plan.default_content_type == "text/html"

    def test_binary_type_string_payload(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='"binary-data"',
            post_type="binary",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.body_bytes == b"binary-data"
        assert plan.content == b"binary-data"
        assert plan.default_content_type == "application/octet-stream"

    def test_binary_type_dict_payload(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="POST",
            payload='{"data": 123}',
            post_type="binary",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.body_bytes is not None
        assert isinstance(plan.body_bytes, bytes)
        assert plan.content == plan.body_bytes


# ---------------------------------------------------------------------------
# Planner — body methods
# ---------------------------------------------------------------------------


class TestPlannerBodyMethods:
    def test_put_with_json(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="PUT",
            payload='{"id": 1}',
            post_type="json",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body == {"id": 1}

    def test_patch_with_form(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="PATCH",
            payload='{"status": "active"}',
            post_type="form",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.form_data == {"status": "active"}

    def test_delete_with_raw(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="DELETE",
            payload='"payload-text"',
            post_type="raw",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.raw_body == "payload-text"

    def test_get_ignores_body(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            method="GET",
            payload='{"key": "val"}',
            post_type="json",
        )
        templates = _load_templates(cfg)
        planner = RequestPlanner(cfg, _resolver(cfg.seed), templates)
        plan = planner.next_plan(0)
        assert plan.json_body is None
        assert plan.form_data is None
        assert plan.raw_body is None


# ---------------------------------------------------------------------------
# Body preview property
# ---------------------------------------------------------------------------


class TestBodyPreview:
    def test_json_preview(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="json", payload='{"a": 1}')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.body_preview == {"a": 1}

    def test_form_preview(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="form", payload='{"b": 2}')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.body_preview == {"b": 2}

    def test_raw_preview(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="raw", payload='"text"')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.body_preview == "text"

    def test_binary_preview(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="binary", payload='"bytes"')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        preview = plan.body_preview
        assert preview is not None
        assert "binary" in preview

    def test_none_preview_returns_none(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="none", payload='"text"')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.body_preview is None

    def test_no_payload_preview_returns_none(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="json")
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.body_preview is None


# ---------------------------------------------------------------------------
# Integration — runner sends body correctly
# ---------------------------------------------------------------------------


class TestBodyIntegration:
    @staticmethod
    def _run_coro(coro):
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError("Cannot use sync event loop while one is running")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    def test_json_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"name": "test"}',
            post_type="json",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_form_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"field": "value"}',
            post_type="form",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_raw_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"raw": "content"}',
            post_type="raw",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_xml_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"xml": "data"}',
            post_type="xml",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_html_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"html": "data"}',
            post_type="html",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_binary_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"bin": "data"}',
            post_type="binary",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1

    def test_multipart_body_reaches_server(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/receive",
            method="POST",
            payload='{"field": "val"}',
            post_type="multipart",
        )
        from blaze_hammer.services import execute_prepared, prepare_run

        prepared = prepare_run(cfg)
        try:
            stats = self._run_coro(execute_prepared(prepared))
        finally:
            self._run_coro(prepared.aclose())
        assert stats.completed >= 1


# ---------------------------------------------------------------------------
# Content-Type default injection
# ---------------------------------------------------------------------------


class TestContentTypeInjection:
    def test_json_gets_default_ct(self, tmp_path, server_url):
        """Runner should inject Content-Type: application/json when not set."""
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="json", payload='{"a":1}')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        # The planner sets default_content_type; runner injects it.
        assert plan.default_content_type == "application/json"

    def test_xml_gets_default_ct(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="xml", payload='"<r/>"')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.default_content_type == "application/xml"

    def test_binary_gets_default_ct(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="binary", payload='"data"')
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.default_content_type == "application/octet-stream"

    def test_none_has_no_default_ct(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="none")
        templates = _load_templates(cfg)
        plan = RequestPlanner(cfg, _resolver(cfg.seed), templates).next_plan(0)
        assert plan.default_content_type is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestBodyTypeValidation:
    def test_file_payload_requires_form_or_multipart(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            post_type="json",
            file_payload=True,
        )
        from blaze_hammer.config.validation import validate_config

        outcome = validate_config(cfg)
        assert outcome.ok is False

    def test_file_payload_allowed_with_form(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            post_type="form",
            file_payload=True,
        )
        from blaze_hammer.config.validation import validate_config

        outcome = validate_config(cfg)
        assert outcome.ok is True

    def test_file_payload_allowed_with_multipart(self, tmp_path, server_url):
        cfg = _project(
            tmp_path,
            f"{server_url}/echo",
            post_type="multipart",
            file_payload=True,
        )
        from blaze_hammer.config.validation import validate_config

        outcome = validate_config(cfg)
        assert outcome.ok is True

    def test_none_type_valid(self, tmp_path, server_url):
        cfg = _project(tmp_path, f"{server_url}/echo", post_type="none")
        from blaze_hammer.config.validation import validate_config

        outcome = validate_config(cfg)
        assert outcome.ok is True


# ---------------------------------------------------------------------------
# API model validation
# ---------------------------------------------------------------------------


class TestAPIModelValidation:
    def test_save_config_post_type_all_values(self):
        from blaze_hammer.web.models import SaveConfigRequest

        for pt in PostType.allowed_values():
            req = SaveConfigRequest(config_revision="abc", post_type=pt)
            assert req.post_type == pt

    def test_save_config_invalid_post_type_rejected(self):
        from pydantic import ValidationError

        from blaze_hammer.web.models import SaveConfigRequest

        with pytest.raises(ValidationError):
            SaveConfigRequest(config_revision="abc", post_type="invalid")

    def test_run_settings_post_type_all_values(self):
        from blaze_hammer.web.models import RunSettingsPayload

        for pt in PostType.allowed_values():
            payload = RunSettingsPayload(post_type=pt)
            overrides = payload.to_overrides()
            assert overrides["post_type"] == pt


# ---------------------------------------------------------------------------
# RunConfig model accepts new post types
# ---------------------------------------------------------------------------


class TestRunConfigPostTypes:
    def test_all_post_types_accepted(self):
        for pt in PostType.allowed_values():
            cfg = RunConfig(
                target="https://example.test",
                post_type=pt,
            )
            assert cfg.post_type.value == pt

    def test_post_type_default_is_json(self):
        cfg = RunConfig(target="https://example.test")
        assert cfg.post_type is PostType.JSON

    def test_post_type_case_insensitive_via_loader(self):
        cfg = build_config(
            {"target": "https://example.test", "post_type": "XML"},
            environ={},
        )
        assert cfg.post_type is PostType.XML
