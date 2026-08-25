"""CLI end-to-end tests via CliRunner (no real network)."""

from __future__ import annotations

import json

from click.testing import CliRunner

from blaze_hammer.cli.main import _route_legacy, cli


def _invoke(args):
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def test_version_commands():
    from blaze_hammer import __version__

    result = _invoke(["--version"])
    assert result.exit_code == 0 and f"version {__version__}" in result.output
    result = _invoke(["version"])
    assert result.exit_code == 0 and __version__ in result.output


def test_help_lists_all_commands():
    result = _invoke(["--help"])
    for command in ("run", "inspect", "validate", "placeholders", "profiles", "interactive"):
        assert command in result.output


def test_dry_run_never_sends(tmp_path):
    payload = tmp_path / "p.json"
    payload.write_text('{"u": "{uuid}"}')
    result = _invoke(
        ["run", "https://example.test/api", "-m", "POST", "-p", str(payload), "--dry-run"]
    )
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert '"u"' in result.output


def test_run_post_without_payload_fails_early():
    result = _invoke(["run", "https://example.test/api", "-m", "POST"])
    assert result.exit_code == 1
    assert "payload" in result.output.lower()


def test_validate_reports_bad_placeholder_with_suggestion(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"a": "{emal(prefix=x_)}"}')
    result = _invoke(["validate", "-p", str(bad), "https://example.test"])
    assert result.exit_code == 1
    assert "Unknown placeholder" in result.output or "email" in result.output


def test_inspect_shows_sample_request(tmp_path):
    payload = tmp_path / "p.json"
    payload.write_text('{"name": "{faker.name}"}')
    headers = tmp_path / "h.json"
    headers.write_text('{"Accept": "application/json"}')
    result = _invoke(
        [
            "inspect",
            "-p",
            str(payload),
            "--headers",
            str(headers),
            "https://example.test/api",
        ]
    )
    assert result.exit_code == 0
    assert "Generated request" in result.output
    assert "Authorization" not in result.output  # nothing to redact here anyway


def test_inspect_redacts_sensitive_headers(tmp_path):
    headers = tmp_path / "h.json"
    headers.write_text(json.dumps({"Authorization": "Bearer topsecret"}))
    result = _invoke(["inspect", "--headers", str(headers), "https://example.test"])
    assert result.exit_code == 0
    assert "topsecret" not in result.output
    assert "***REDACTED***" in result.output


def test_placeholders_listing():
    result = _invoke(["placeholders"])
    assert result.exit_code == 0
    assert "{uuid}" in result.output and "{choice(" in result.output


def test_placeholders_faker_query():
    result = _invoke(["placeholders", "faker.user_na"])
    assert result.exit_code == 0
    assert "user_name" in result.output

    builtin = _invoke(["placeholders", "email"])
    assert builtin.exit_code == 0
    assert "e-mail" in builtin.output.lower()


def test_placeholders_unknown_query_fails():
    result = _invoke(["placeholders", "definitely_not_real"])
    assert result.exit_code == 1


def test_json_diff_legacy_exit_code(tmp_path):
    target = tmp_path / "tpl.json"
    target.write_text('{"n": "{int(min=1, max=1)}", "name": "{faker.name}"}')
    result = _invoke(["run", "--json-diff", str(target)])
    # Documented legacy quirk: json-diff always exits 1.
    assert result.exit_code == 1
    assert "Payload differences" in result.output


def test_legacy_routing_bare_url_and_host():
    assert _route_legacy(["https://x.test/api", "-n", "5"])[0] == "run"
    assert _route_legacy(["--json-diff", "f.json"])[0] == "run"
    assert _route_legacy(["run", "x"])[0] == "run"
    assert _route_legacy(["-n", "5"])[0] == "run"
    assert _route_legacy(["--help"]) == ["--help"]
    assert _route_legacy(["--version"]) == ["--version"]
    assert _route_legacy([]) == ["run"]


def test_profiles_list_and_show(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "register.json").write_text(
        json.dumps({"method": "POST", "requests": 42})
    )
    listed = _invoke(["profiles", "list"])
    assert listed.exit_code == 0 and "register" in listed.output

    shown = _invoke(["profiles", "show", "register"])
    assert shown.exit_code == 0 and '"requests": 42' in shown.output.replace(" ", " ")


def test_env_var_override_applies(monkeypatch):
    monkeypatch.setenv("BLAZE_CONCURRENCY", "33")
    from blaze_hammer.config.loader import build_config as loader_build

    cfg = loader_build({"target": "https://x.test"})
    assert cfg.concurrency == 33


def test_unexpected_error_concise_without_debug_verbose_with(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("blaze_hammer.services.build_run_config", boom)
    runner = CliRunner()

    plain = runner.invoke(cli, ["run", "https://x.test"], catch_exceptions=True)
    assert plain.exit_code == 1
    assert "Unexpected error" in plain.output
    assert "Traceback" not in plain.output
    assert "--debug" in plain.output

    verbose = runner.invoke(cli, ["run", "https://x.test", "--debug"], catch_exceptions=True)
    assert isinstance(verbose.exception, RuntimeError)
    assert "kaboom" in str(verbose.exception)


def test_completion_script_emitted():
    result = _invoke(["completion", "bash"])
    assert result.exit_code == 0
    assert "_BLAZE_HAMMER_COMPLETE" in result.output
