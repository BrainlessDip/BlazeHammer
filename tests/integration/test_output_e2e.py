"""End-to-end output behavior over real HTTP: truncation, filters, saving."""

from __future__ import annotations

import asyncio
import csv
import json

from blaze_hammer.engine.client import build_client, read_body_snapshot
from tests.integration.test_http_engine import drive, make_runner


def test_response_bodies_captured_and_truncated(server_url):
    cfg, runner, _c, client = make_runner(
        server_url + "/big",
        requests=1,
        concurrency=1,
        output={"print_response": True, "max_response_size": 100},
    )
    outcomes: list = []
    runner._observers = (outcomes.append,)
    asyncio.run(drive(runner, client))
    assert len(outcomes) == 1
    body = outcomes[0].body
    assert body is not None
    assert len(body.text) <= 100
    assert body.truncated is True


def test_read_body_snapshot_unlimited(server_url):

    async def scenario():
        client = build_client(type("C", (), {"concurrency": 2, "timeout": 5.0})())
        try:
            response = await client.get(server_url + "/big")
            snap = await read_body_snapshot(response, None)
            return snap
        finally:
            await client.aclose()

    snap = asyncio.run(scenario())
    assert snap.truncated is False
    assert len(snap.text) > 1000


def test_status_filter_prints_only_matching(server_url, capsys):
    from click.testing import CliRunner

    from blaze_hammer.cli.main import cli

    result = CliRunner().invoke(
        cli,
        [
            "run",
            server_url + "/status/404",
            "-s",
            "-n",
            "3",
            "-c",
            "2",
            "-pr",
            "--status",
            "200",
        ],
        catch_exceptions=False,
    )
    _ = result
    out = capsys.readouterr().out
    # Nothing printed: 404 outcomes filtered out by --status 200.
    assert "Response" not in out


def test_failed_only_prints_error_lines(server_url):
    from click.testing import CliRunner

    from blaze_hammer.cli.main import cli

    result = CliRunner().invoke(
        cli,
        [
            "run",
            server_url + "/slow?ms=900",
            "-s",
            "-n",
            "1",
            "--failed-only",
            "-pr",
            "--timeout",
            "0.2",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    # Plain summary reports the classified failure...
    assert "Failed: 1" in result.output
    assert "timeout" in result.output
    # ...and the failed-only printer emitted the failing response block.
    assert "Response" in result.output


def test_save_responses_and_csv_export(server_url, tmp_path, capsys):
    from click.testing import CliRunner

    from blaze_hammer.cli.main import cli

    save_dir = tmp_path / "results"
    export = tmp_path / "summary.csv"
    payload = tmp_path / "p.json"
    payload.write_text('{"Authorization": "Bearer tok", "k": 1}')

    result = CliRunner().invoke(
        cli,
        [
            "run",
            server_url + "/echo",
            "-s",
            "-m",
            "POST",
            "-p",
            str(payload),
            "-n",
            "4",
            "-c",
            "2",
            "--save-responses",
            str(save_dir),
            "--output",
            str(export),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    lines = (save_dir / "responses.jsonl").read_text().strip().splitlines()
    assert len(lines) == 4
    record = json.loads(lines[0])
    assert record["payload"]["Authorization"] == "***REDACTED***"
    assert record["payload"]["k"] == 1
    assert (save_dir / "summary.json").is_file()

    with export.open(newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][0] == "target"
    assert len(rows) == 2
