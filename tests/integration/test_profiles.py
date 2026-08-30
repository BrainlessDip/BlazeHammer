"""Profile loading and precedence through the full CLI path."""

from __future__ import annotations

import json

from click.testing import CliRunner

from blaze_hammer.cli.main import cli
from blaze_hammer.config.loader import build_config as loader_build


def _make_profile(tmp_path: json.Path, name: str, data: dict) -> None:  # type: ignore[name-defined]
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / f"{name}.json").write_text(json.dumps(data))


def test_profile_drives_run_via_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = tmp_path / "p.json"
    payload.write_text('{"n": "{int(min=3, max=3)}"}')
    _make_profile(
        tmp_path,
        "register",
        {
            "target": "https://example.test/register",
            "method": "POST",
            "payload_file": str(payload),
            "requests": 12,
            "retries": {"max_retries": 2},
        },
    )

    result = CliRunner().invoke(
        cli, ["run", "--profile", "register", "--dry-run"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "https://example.test/register" in result.output
    assert '"n": 3' in result.output  # native JSON int via {int(...)}


def test_cli_overrides_profile_values(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = tmp_path / "p.json"
    payload.write_text('{"a": 1}')
    _make_profile(
        tmp_path,
        "login",
        {
            "target": "https://example.test/login",
            "method": "POST",
            "requests": 5,
        },
    )

    result = CliRunner().invoke(
        cli,
        ["run", "--profile", "login", "-m", "GET", "--preview"],
        catch_exceptions=False,
    )
    # -m GET overrides profile POST; preview renders GET line.
    assert result.exit_code == 0
    assert "GET https://example.test/login" in result.output


def test_env_retries_mapping(monkeypatch):
    cfg = loader_build(
        {"target": "https://x.test"},
        environ={"BLAZE_RETRIES": "4", "BLAZE_RATE": "25.5"},
    )
    assert cfg.retries.max_retries == 4
    assert cfg.rate == 25.5
