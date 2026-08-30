"""Interactive mode: prompts produce a real validated run."""

from __future__ import annotations

from click.testing import CliRunner

from blaze_hammer.cli.main import cli


def test_interactive_runs_configured_request(server_url, server, tmp_path):
    payload = tmp_path / "p.json"
    payload.write_text('{"k": "{int(min=1, max=1)}"}')
    headers = tmp_path / "h.json"
    headers.write_text("{}")

    answers = "\n".join(
        [
            server_url + "/echo",  # Target URL
            "GET",  # Method
            "3",  # Requests
            "2",  # Concurrency
            str(payload),  # Payload file
            str(headers),  # Headers file
            "",  # Seed
            "n",  # Preview? -> run immediately
        ]
    )
    result = CliRunner().invoke(cli, ["interactive"], input=answers, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "Final Report" in result.output
    assert server.state.requests >= 3


def test_interactive_preview_answer_skips_execution(server_url, server, tmp_path, monkeypatch):
    headers = tmp_path / "headers.json"
    headers.write_text("{}")
    monkeypatch.chdir(tmp_path)  # default 'headers.json' resolves here

    answers = "\n".join(
        [
            server_url + "/echo",
            "GET",
            "2",
            "2",
            "",  # no payload
            "",  # headers -> default headers.json (exists in cwd)
            "",  # seed
            "y",  # preview only
        ]
    )
    result = CliRunner().invoke(cli, ["interactive"], input=answers, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert server.state.requests == 0
