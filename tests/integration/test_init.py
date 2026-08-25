"""Integration tests: `init` command, project workflow and URL shorthand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from blaze_hammer.cli.main import _route_legacy, cli


def _invoke(args):
    """Invoke like the installed console script would (routing included)."""
    return CliRunner().invoke(cli, _route_legacy(list(args)), catch_exceptions=False)


def _invoke_raw(args):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# init: creation
# ---------------------------------------------------------------------------


def test_init_creates_project_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(["init", "my-api-test", "--non-interactive"])
    assert result.exit_code == 0, result.output
    root = tmp_path / "my-api-test"
    assert (root / "blazehammer.yaml").is_file()
    assert (root / "payload.json").is_file()
    assert (root / "headers.json").is_file()
    assert (root / "profiles").is_dir()
    assert "cd my-api-test" in result.output
    # No files leaked into the caller's directory.
    assert not (tmp_path / "blazehammer.yaml").exists()


def test_init_generated_project_validates_and_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "proj", "--non-interactive"])
    monkeypatch.chdir(tmp_path / "proj")
    validated = _invoke(["validate"])
    assert validated.exit_code == 0, validated.output
    dried = _invoke(["run", "--dry-run"])
    assert dried.exit_code == 0 and "DRY RUN" in dried.output


def test_init_wizard_prompts_with_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["init"],
        input="my-wiz\nhttps://api.test/reg\n\n\n\n\n",
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    yaml_text = (tmp_path / "my-wiz" / "blazehammer.yaml").read_text(encoding="utf-8")
    assert "https://api.test/reg" in yaml_text
    assert "method: POST" in yaml_text


# ---------------------------------------------------------------------------
# init: safety
# ---------------------------------------------------------------------------


def test_init_refuses_existing_project_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = _invoke(["init", "dup", "--non-interactive"])
    assert first.exit_code == 0
    yaml_path = tmp_path / "dup" / "blazehammer.yaml"
    yaml_path.write_text("# marker\n", encoding="utf-8")

    second = _invoke(["init", "dup", "--non-interactive"])
    assert second.exit_code != 0
    assert "already exists" in second.output.lower()
    # Untouched without --force.
    assert yaml_path.read_text(encoding="utf-8") == "# marker\n"


def test_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "forced", "--non-interactive"])
    yaml_path = tmp_path / "forced" / "blazehammer.yaml"
    yaml_path.write_text("# old\n", encoding="utf-8")
    result = _invoke(["init", "forced", "--non-interactive", "--force"])
    assert result.exit_code == 0, result.output
    assert "# old" not in yaml_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad_name", ["..", ".", "../evil", "a/b", "a\\b", "  "])
def test_init_rejects_unsafe_names(tmp_path, monkeypatch, bad_name):
    monkeypatch.chdir(tmp_path)
    result = _invoke(["init", "--name", bad_name, "--non-interactive"])
    assert result.exit_code != 0
    assert "Invalid project name" in result.output
    if "/" in bad_name or "\\" in bad_name:
        assert not (tmp_path / "evil").exists()


def test_init_rejects_empty_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli, ["init", "--non-interactive"], input="\n", catch_exceptions=False
    )
    # Empty prompt answer falls through to the required-name error path.
    assert result.exit_code != 0


def test_init_positional_and_name_flag_conflict(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["init", "aaa", "--name", "bbb", "--non-interactive"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "Conflicting project names" in result.output


def test_init_non_interactive_requires_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["init", "--non-interactive"], catch_exceptions=False)
    assert result.exit_code != 0
    assert "Project name required" in result.output


def test_init_target_option_lands_in_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(
        [
            "init",
            "tgt",
            "--non-interactive",
            "--target",
            "https://opt.test/x",
            "-m",
            "GET",
            "-n",
            "7",
            "-c",
            "3",
        ]
    )
    assert result.exit_code == 0, result.output
    text = (tmp_path / "tgt" / "blazehammer.yaml").read_text(encoding="utf-8")
    assert "https://opt.test/x" in text
    assert "method: GET" in text
    assert "requests: 7" in text
    assert "concurrency: 3" in text


# ---------------------------------------------------------------------------
# Zero-config UX and URL shorthand
# ---------------------------------------------------------------------------


def test_bare_invocation_without_project_is_actionable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke([])
    assert result.exit_code != 0
    assert "No Blaze Hammer project found" in result.output
    assert "blaze-hammer init" in result.output


def test_bare_invocation_uses_yaml_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "p2", "--non-interactive", "--target", "https://bare.test"])
    monkeypatch.chdir(tmp_path / "p2")
    result = _invoke(["run", "--dry-run"])
    assert result.exit_code == 0
    assert "Target: https://bare.test" in result.output


def test_positional_url_overrides_yaml_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "p3", "--non-interactive", "--target", "https://yaml.test"])
    monkeypatch.chdir(tmp_path / "p3")
    result = _invoke(["https://override.test/x", "--dry-run"])
    assert result.exit_code == 0
    assert "Target: https://override.test/x" in result.output


def test_url_conflict_between_positional_and_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "p4", "--non-interactive"])
    monkeypatch.chdir(tmp_path / "p4")
    result = CliRunner().invoke(
        cli,
        ["run", "https://a.test", "--url", "https://b.test"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "Conflicting target URLs" in result.output


def test_outside_run_with_relative_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "outside-proj", "--non-interactive", "--target", "https://o.test"])
    caller = tmp_path / "elsewhere"
    caller.mkdir()
    monkeypatch.chdir(caller)
    cfg_rel = Path("..") / "outside-proj" / "blazehammer.yaml"
    result = _invoke(["run", "--config", str(cfg_rel), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Target: https://o.test" in result.output


# ---------------------------------------------------------------------------
# profile subcommands and aliases
# ---------------------------------------------------------------------------


def test_profile_create_list_show(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    created = _invoke(["profile", "create", "register"])
    assert created.exit_code == 0, created.output
    profile_file = tmp_path / "profiles" / "register.json"
    data = json.loads(profile_file.read_text(encoding="utf-8"))
    assert data["method"] == "POST"

    listed = _invoke(["profile", "list"])
    assert listed.exit_code == 0 and "register" in listed.output

    shown = _invoke(["profile", "show", "register"])
    assert shown.exit_code == 0 and "register" in shown.output


def test_profiles_legacy_alias_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _invoke(["profiles", "list"])
    assert result.exit_code == 0


def test_profile_resolved_relative_to_config_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _invoke(["init", "pp", "--non-interactive"])
    (tmp_path / "pp" / "profiles" / "heavy.json").write_text(
        json.dumps({"requests": 55}), encoding="utf-8"
    )
    caller = tmp_path / "out"
    caller.mkdir()
    monkeypatch.chdir(caller)
    config = Path("..") / "pp" / "blazehammer.yaml"
    result = _invoke(["run", "--config", str(config), "--profile", "heavy", "--dry-run"])
    assert result.exit_code == 0, result.output
