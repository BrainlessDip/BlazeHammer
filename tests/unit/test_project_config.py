"""Project configuration (blazehammer.yaml) unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from blaze_hammer.config.loader import build_config, collect_env_overrides
from blaze_hammer.config.project import (
    find_project_config,
    load_project_yaml,
)
from blaze_hammer.errors import ConfigurationError


def _write_project(tmp_path: Path, text: str, name: str = "blazehammer.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _cfg(tmp_path: Path, overrides=None, environ=None, config_path=None):
    return build_config(
        overrides or {},
        profile=None,
        environ=environ if environ is not None else {},
        config_path=config_path,
    )


# ---------------------------------------------------------------------------
# Loading and defaults
# ---------------------------------------------------------------------------


def test_yaml_loading_basic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(
        tmp_path,
        'target: "https://api.test/x"\nrequests: 42\nmethod: POST\ntimeout: 7.5\n',
    )
    cfg = _cfg(tmp_path)
    assert cfg.target == "https://api.test/x"
    assert cfg.requests == 42
    assert cfg.method.value == "POST"
    assert cfg.timeout == 7.5


def test_default_values_applied_for_unspecified_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, 'target: "https://api.test/x"\n')
    cfg = _cfg(tmp_path)
    assert cfg.requests == 100
    assert cfg.concurrency == 100
    assert cfg.method.value == "GET"
    assert cfg.timeout == 30.0


def test_empty_yaml_file_is_valid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, "")
    with pytest.raises(ConfigurationError, match="No Blaze Hammer project found"):
        _cfg(tmp_path)


# ---------------------------------------------------------------------------
# Precedence: CLI > env > profile > YAML
# ---------------------------------------------------------------------------


def test_cli_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, 'target: "https://yaml.test"\nrequests: 100\n')
    cfg = _cfg(tmp_path, overrides={"target": "https://cli.test", "requests": 1000})
    assert cfg.target == "https://cli.test"
    assert cfg.requests == 1000


def test_environment_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, 'target: "https://yaml.test"\nrequests: 100\n')
    cfg = _cfg(tmp_path, environ={"BLAZE_REQUESTS": "500"})
    assert cfg.requests == 500
    cfg = _cfg(tmp_path, environ={"BLAZE_HAMMER_REQUESTS": "700"})
    assert cfg.requests == 700


def test_precedence_cli_env_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, 'target: "https://t.test"\nconcurrency: 1\n')
    base = {"target": "https://t.test"}
    cfg = _cfg(tmp_path, dict(base), environ={"BLAZE_CONCURRENCY": "5"})
    assert cfg.concurrency == 5
    cfg = _cfg(
        tmp_path,
        {**base, "concurrency": 9},
        environ={"BLAZE_CONCURRENCY": "5"},
    )
    assert cfg.concurrency == 9


def test_plain_blaze_prefix_wins_over_hammer_prefix(tmp_path):
    env = {
        "BLAZE_HAMMER_REQUESTS": "11",
        "BLAZE_REQUESTS": "22",
    }
    merged = collect_env_overrides(env)
    assert merged["requests"] == 22


def test_new_env_fields_map(tmp_path):
    env = {
        "BLAZE_HAMMER_TARGET": "https://env.test",
        "BLAZE_METHOD": "POST",
        "BLAZE_FAKER_LOCALE": "fr_FR",
    }
    merged = collect_env_overrides(env)
    assert merged["target"] == "https://env.test"
    assert merged["method"] == "POST"
    assert merged["faker_locale"] == "fr_FR"


# ---------------------------------------------------------------------------
# Validation of the YAML itself
# ---------------------------------------------------------------------------


def test_invalid_yaml_syntax_reports_position(tmp_path):
    path = _write_project(tmp_path, "target: [unclosed\n  bad indent\n")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_project_yaml(path)


def test_unknown_top_level_key_rejected_with_suggestion(tmp_path):
    _write_project(tmp_path, "concurency: 20\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_project_yaml(tmp_path / "blazehammer.yaml")
    assert "concurency" in str(excinfo.value.reason)
    assert "concurrency" in str(excinfo.value.reason)


def test_unknown_nested_faker_key_rejected(tmp_path):
    _write_project(tmp_path, "faker:\n  localee: fr_FR\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_project_yaml(tmp_path / "blazehammer.yaml")
    assert "localee" in str(excinfo.value.reason)


def test_non_mapping_root_rejected(tmp_path):
    _write_project(tmp_path, "- a\n- b\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_project_yaml(tmp_path / "blazehammer.yaml")
    assert "must be a mapping" in str(excinfo.value.reason)


def test_missing_explicit_config_path(tmp_path):
    with pytest.raises(ConfigurationError, match="not found"):
        build_config({}, environ={}, config_path=tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# Aliases and nested sections
# ---------------------------------------------------------------------------


def test_payload_headers_aliases_and_relative_resolution(tmp_path):
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    (tmp_path / "headers.json").write_text("{}", encoding="utf-8")
    data = load_project_yaml(
        _write_project(
            tmp_path,
            "payload: payload.json\nheaders: headers.json\n",
        )
    )
    assert data["payload_file"] == (tmp_path / "payload.json").resolve()
    assert data["headers_file"] == (tmp_path / "headers.json").resolve()


def test_relative_paths_resolve_against_config_dir_not_cwd(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "p.json").write_text("{}", encoding="utf-8")
    data = load_project_yaml(
        _write_project(project.parent, "payload: ../p.json\n", name="proj/blazehammer.yaml")
    )
    resolved = Path(data["payload_file"])
    assert resolved.is_absolute()
    # Points at the file next to the YAML's parent layout, not the cwd.
    assert resolved.name == "p.json"


def test_faker_section_maps_to_flat_fields(tmp_path):
    data = load_project_yaml(_write_project(tmp_path, "faker:\n  locale: bn_BD\n  seed: 99\n"))
    assert data["faker_locale"] == "bn_BD"
    assert data["seed"] == 99


def test_output_file_alias(tmp_path):
    data = load_project_yaml(
        _write_project(tmp_path, "output:\n  simple: true\n  file: results/out.json\n")
    )
    assert data["output"]["simple"] is True
    assert Path(data["output"]["export_path"]).name == "out.json"


def test_retries_int_shorthand(tmp_path):
    data = load_project_yaml(_write_project(tmp_path, "retries: 3\n"))
    assert data["retries"] == {"max_retries": 3}


def test_end_to_end_alias_merge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "payload.json").write_text("{}", encoding="utf-8")
    _write_project(
        tmp_path,
        'target: "https://t.test"\npayload: payload.json\nretries: 2\nfaker:\n  locale: de_DE\n',
    )
    cfg = _cfg(tmp_path)
    assert cfg.retries.max_retries == 2
    assert cfg.faker_locale == "de_DE"
    assert cfg.payload_file is not None and cfg.payload_file.name == "payload.json"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_is_cwd_only_no_parent_walk(tmp_path, monkeypatch):
    parent = tmp_path / "outer"
    parent.mkdir()
    _write_project(parent, 'target: "https://parent.test"\n')
    child = parent / "inner"
    child.mkdir()
    monkeypatch.chdir(child)
    assert find_project_config() is None
    assert find_project_config(parent) == parent / "blazehammer.yaml"


def test_missing_target_actionable_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError) as excinfo:
        build_config({}, environ={})
    assert "No Blaze Hammer project found" in str(excinfo.value)
    assert "blaze-hammer init" in str(excinfo.value.hint)


def test_profile_without_target_surfaces_schema_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "bad.json").write_text('{"concurrencyy": 5}', encoding="utf-8")
    with pytest.raises(ConfigurationError) as excinfo:
        build_config({}, profile="bad", environ={})
    assert "concurrencyy" in str(excinfo.value.reason)


def test_outside_run_via_config_path(tmp_path, monkeypatch):
    project = tmp_path / "my-api-test"
    project.mkdir()
    (project / "payload.json").write_text("{}", encoding="utf-8")
    _write_project(
        project.parent,
        'target: "https://outside.test"\npayload: payload.json\n',
        name="my-api-test/blazehammer.yaml",
    )
    caller = tmp_path / "elsewhere"
    caller.mkdir()
    monkeypatch.chdir(caller)
    cfg = _cfg(tmp_path, config_path=str(project / "blazehammer.yaml"))
    assert cfg.target == "https://outside.test"
    assert cfg.payload_file == (project / "payload.json").resolve()
