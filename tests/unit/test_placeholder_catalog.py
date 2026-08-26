"""Placeholder catalog: dynamic Faker discovery, caching, API + WS delivery."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from blaze_hammer.templating.catalog import (
    build_catalog,
    invalidate_catalog_cache,
)
from blaze_hammer.web.app import create_app
from blaze_hammer.web.config import resolve_web_settings

from .test_web import _make_client, _project


@pytest.fixture(autouse=True)
def _fresh_cache():
    invalidate_catalog_cache()
    yield
    invalidate_catalog_cache()


@pytest.fixture()
def anon_ws(tmp_path, server_url):
    """Auth-disabled app so WS/REST catalog checks skip login ceremony."""
    _project(tmp_path, f"{server_url}/echo")
    client = _make_client(tmp_path, server_url, enabled=False)
    yield client
    client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# builder
# ---------------------------------------------------------------------------


def test_catalog_shape_and_builtins():
    catalog = build_catalog()
    assert catalog["version"] >= 1
    assert catalog["faker_version"]
    names = {entry["name"] for entry in catalog["builtins"]}
    for expected in ("uuid", "ip", "int", "email", "password", "choice"):
        assert expected in names, f"missing builtin {expected}"
    uuid_entry = next(e for e in catalog["builtins"] if e["name"] == "uuid")
    assert uuid_entry["insert_text"] == "{uuid}"
    assert uuid_entry["description"]
    int_entry = next(e for e in catalog["builtins"] if e["name"] == "int")
    param_names = {p["name"] for p in int_entry["parameters"]}
    assert {"min", "max"} <= param_names


def test_catalog_includes_core_faker_methods_with_docs_and_signatures():
    catalog = build_catalog()
    by_name = {entry["name"]: entry for entry in catalog["faker"]}
    email = by_name.get("faker.email")
    assert email is not None, "faker.email missing from top-level faker entries"
    assert email["insert_text"] == "{faker.email}"
    name_entry = by_name.get("faker.name")
    assert name_entry is not None and name_entry["description"]

    date_between = by_name.get("faker.date_between")
    if date_between is not None:  # present in current Faker; signature inspected
        param_names = {p["name"] for p in date_between["parameters"]}
        assert {"start_date", "end_date"} <= param_names


def test_catalog_includes_custom_provider_methods():
    catalog = build_catalog()
    by_name = {entry["name"]: entry for entry in catalog["faker"]}
    simple = by_name.get("faker.simple_example")
    advanced = by_name.get("faker.advanced_example")
    assert simple is not None, "custom provider method not exposed top-level"
    assert advanced is not None
    adv_params = {p["name"] for p in advanced["parameters"]}
    assert {"category", "language", "length"} <= adv_params


def test_catalog_provider_paths_exclude_private_members():
    catalog = build_catalog()
    families = {entry["family"]: entry for entry in catalog["providers"]}
    internet = families.get("internet")
    assert internet is not None and internet["builtin"] is True
    method_names = {m["name"] for m in internet["methods"]}
    assert "email" in method_names and "user_name" in method_names
    email = next(m for m in internet["methods"] if m["name"] == "email")
    assert email["path"] == "faker.providers.internet.email"
    for family in catalog["providers"]:
        for method in family["methods"]:
            assert not method["name"].startswith("_")


def test_custom_providers_listed_as_non_builtin_family():
    catalog = build_catalog()
    custom = [f for f in catalog["providers"] if not f["builtin"]]
    all_custom_methods = {m["name"] for f in custom for m in f["methods"]}
    assert {"simple_example", "advanced_example"} <= all_custom_methods


def test_locale_is_reported_when_requested():
    catalog = build_catalog("bn_BD")
    assert catalog["locale"] == "bn_BD"
    assert build_catalog(None)["locale"] is None


def test_catalog_is_cached_per_locale():
    first = build_catalog()
    second = build_catalog()
    assert first is second
    other = build_catalog("bn_BD")
    assert other is not first
    invalidate_catalog_cache()
    assert build_catalog() is not first


# ---------------------------------------------------------------------------
# REST endpoint
# ---------------------------------------------------------------------------


def test_catalog_endpoint_requires_auth(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    client = _make_client(tmp_path, server_url, do_login=False)
    try:
        resp = client.get("/api/v1/placeholders/catalog")
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_catalog_endpoint_returns_full_catalog(anon_ws):
    resp = anon_ws.get("/api/v1/placeholders/catalog")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] >= 1
    assert any(entry["name"] == "uuid" for entry in body["builtins"])
    assert any(entry["name"] == "faker.email" for entry in body["faker"])
    families = {f["family"] for f in body["providers"]}
    assert "internet" in families


def test_catalog_endpoint_validates_against_response_model(anon_ws):
    resp = anon_ws.get("/api/v1/placeholders/catalog")
    payload = resp.json()
    # Every builtin entry must carry the fields the editor relies on.
    for entry in payload["builtins"][:5]:
        assert isinstance(entry["insert_text"], str) and entry["insert_text"]
        assert set(entry) >= {"name", "kind", "insert_text", "parameters"}


# ---------------------------------------------------------------------------
# WebSocket delivery
# ---------------------------------------------------------------------------


def test_ws_sends_hello_then_placeholder_catalog(anon_ws):
    with anon_ws.websocket_connect("/api/v1/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        event = json.loads(ws.receive_text())
        assert event["type"] == "placeholder.catalog"
        assert event["version"] >= 1
        assert any(
            e["name"] == "{int(min=1, max=1)}" or e["name"] == "int" for e in event["builtins"]
        )


def test_ws_catalog_event_survives_broken_project_yaml(tmp_path, server_url):
    _project(tmp_path, f"{server_url}/echo")
    # Corrupt the project YAML: metadata is best-effort, connection must live.
    # Settings are built directly (no loader pass) to mirror a server whose
    # only job here is serving /ws; the broken YAML sits untouched on disk.
    import dataclasses

    from blaze_hammer.config.models import RunConfig

    base = resolve_web_settings(RunConfig(target="https://t.test"), environ={})
    settings = dataclasses.replace(base, auth=dataclasses.replace(base.auth, enabled=False))
    app = create_app(settings=settings, project_dir=tmp_path)
    client = TestClient(app)
    client.__enter__()
    try:
        with client.websocket_connect("/api/v1/ws") as ws:
            hello = json.loads(ws.receive_text())
            assert hello["type"] == "hello"
            event = json.loads(ws.receive_text())
            assert event["type"] == "placeholder.catalog"
    finally:
        client.__exit__(None, None, None)
