"""Service-layer tests for the web server launcher and info services."""

from __future__ import annotations

from typing import Any

import pytest

from blaze_hammer.config.loader import build_config
from blaze_hammer.errors import EXIT_OK, ConfigurationError
from blaze_hammer.services import (
    faker_list_providers,
    faker_show_method,
    profiles_create,
    profiles_list,
    run_web_server,
)


def _cfg(tmp_path, **web):
    overrides: dict[str, Any] = {}
    if web:
        overrides["web"] = web
    return build_config(overrides, environ={}, config_path=tmp_path / "blazehammer.yaml")


def _project(tmp_path, *, auth_block: str = "") -> None:
    yaml_text = 'target: "https://t.test"\nmethod: GET\npayload: payload.json\n'
    if auth_block:
        indented = "".join(f"  {line}\n" for line in auth_block.splitlines())
        yaml_text += f"web:\n{indented}"
    (tmp_path / "blazehammer.yaml").write_text(yaml_text, encoding="utf-8")
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")


def test_run_web_server_disabled(tmp_path):
    _project(tmp_path)
    cfg = _cfg(tmp_path, enabled=False)
    with pytest.raises(ConfigurationError, match="disabled"):
        run_web_server(cfg)


def test_run_web_server_auth_without_credentials(tmp_path):
    _project(tmp_path)
    cfg = _cfg(tmp_path, auth={"enabled": True})
    with pytest.raises(ConfigurationError, match="credentials are not configured"):
        run_web_server(cfg)


def test_run_web_server_refuses_unauthenticated_exposure(tmp_path):
    _project(tmp_path)
    cfg = _cfg(tmp_path, host="0.0.0.0", auth={"enabled": False})
    with pytest.raises(ConfigurationError, match="all interfaces"):
        run_web_server(cfg)


def test_run_web_server_happy_path_starts_uvicorn(monkeypatch, tmp_path):
    _project(
        tmp_path,
        auth_block='auth:\n  username: admin\n  password: "pw12345"\n',
    )
    cfg = _cfg(tmp_path)

    called: dict[str, Any] = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, *, host, port, log_level):  # noqa: ANN001
            called["host"] = host
            called["port"] = port

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", FakeUvicorn.run)
    code = run_web_server(cfg)
    assert code == EXIT_OK
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8080


def test_faker_list_and_show_services(capsys):
    assert faker_list_providers(query="email") == EXIT_OK
    out = capsys.readouterr().out
    assert "email" in out.lower()
    assert "Faker methods" in out

    assert faker_show_method(method="random_int") == EXIT_OK
    assert "random_int" in capsys.readouterr().out

    # Unknown method -> suggestions path, exit code 1
    assert faker_show_method(method="totally_not_real") == 1


def test_profiles_service_create_list_show(tmp_path, monkeypatch):
    from blaze_hammer.services import profiles_show

    monkeypatch.chdir(tmp_path)
    assert profiles_create("webtest") == EXIT_OK
    assert (tmp_path / "profiles" / "webtest.json").is_file()

    assert profiles_list() == EXIT_OK
    assert profiles_show("webtest") == EXIT_OK


def test_json_diff_service_reports_missing_file(tmp_path, monkeypatch):
    from blaze_hammer.errors import EXIT_ERROR
    from blaze_hammer.services import json_diff_files

    monkeypatch.chdir(tmp_path)
    # Legacy behavior: always exits EXIT_ERROR even on success.
    src = tmp_path / "d.json"
    src.write_text('{"a": "{int(min=1,max=1)}"}', encoding="utf-8")
    assert json_diff_files([str(src)]) == EXIT_ERROR
