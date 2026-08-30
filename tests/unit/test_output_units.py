"""Redaction, response filtering, truncation, JSON diff, exporters."""

from __future__ import annotations

import csv
import json

from blaze_hammer.engine.runner import RequestOutcome
from blaze_hammer.output.exporters import ResponseRecorder, export_results, write_summary
from blaze_hammer.output.filters import ResponseFilter, truncate_text
from blaze_hammer.output.json_diff import diff_tree
from blaze_hammer.security.redaction import (
    is_sensitive,
    redact_mapping,
    sensitive_leaf_paths,
)


def _outcome(ok=True, status=200, **kwargs):
    return RequestOutcome(
        index=0,
        url="https://t.test",
        method="GET",
        ok=ok,
        status_code=status if ok else None,
        latency_s=0.01,
        attempts=1,
        error_category=None if ok else "timeout",
        error_message=None if ok else "timeout: boom",
        **kwargs,
    )


# -- redaction ---------------------------------------------------------------


def test_redaction_by_name_case_insensitive_and_nested():
    data = {
        "Authorization": "Bearer x",
        "x-api-key": "secret",
        "nested": {"password": "h", "keep": 1, "list": [{"token": "t"}]},
    }
    out = redact_mapping(data)
    assert out["Authorization"] == "***REDACTED***"
    assert out["x-api-key"] == "***REDACTED***"
    assert out["nested"]["password"] == "***REDACTED***"
    assert out["nested"]["keep"] == 1
    assert out["nested"]["list"][0]["token"] == "***REDACTED***"


def test_is_sensitive_extra_names():
    assert is_sensitive("Session-Token")
    assert not is_sensitive("X-Request-Id")
    assert is_sensitive("X-My-Field", extra=("my-field",))


def test_sensitive_leaf_paths():
    paths = sensitive_leaf_paths({"auth": {"authorization": "x"}, "ok": 1})
    assert paths == ("auth.authorization",)


# -- filters / truncation ------------------------------------------------------


def test_response_filter_modes():
    f = ResponseFilter(statuses=frozenset({500}))
    assert f.matches(_outcome(status=500))
    assert not f.matches(_outcome(status=200))
    failed = ResponseFilter(failed_only=True)
    assert failed.matches(_outcome(ok=False))
    assert not failed.matches(_outcome())
    success = ResponseFilter(success_only=True)
    assert success.matches(_outcome())
    assert not success.matches(_outcome(ok=False))


def test_truncate_text():
    shown, omitted = truncate_text("abcdef", 4)
    assert (shown, omitted) == ("abcd", 2)
    assert truncate_text(None, 10) == (None, 0)
    whole, zero = truncate_text("abc", 10)
    assert (whole, zero) == ("abc", 0)


# -- json diff ------------------------------------------------------------------


def test_diff_tree_nested_and_lists():
    before = {"a": 1, "b": {"c": 2, "d": [1, 2]}, "drop": True}
    after = {"a": 1, "b": {"c": 3, "d": [1, 2, 3], "new": "x"}, "add": 9}
    entries = diff_tree(before, after)
    ops = {(e.op, e.path) for e in entries}
    assert ("~", "$.b.c") in ops
    assert ("+", "$.b.d[2]") in ops
    assert ("-", "$.drop") in ops
    assert ("+", "$.add") in ops
    assert ("+", "$.b.new") in ops


# -- exporters --------------------------------------------------------------------


def test_recorder_writes_jsonl_with_redaction(tmp_path):
    recorder = ResponseRecorder(tmp_path, sensitive_names=("custom-secret",), max_body_chars=50)
    recorder(
        _outcome(
            resolved_headers={"Authorization": "Bearer z", "Accept": "json"},
            resolved_payload={"custom-secret": "v", "plain": 1},
        )
    )
    recorder(_outcome(ok=False))
    recorder.close()

    lines = (tmp_path / "responses.jsonl").read_text().strip().splitlines()
    errors = (tmp_path / "errors.jsonl").read_text().strip().splitlines()
    record = json.loads(lines[0])
    assert record["headers"]["Authorization"] == "***REDACTED***"
    assert record["payload"]["custom-secret"] == "***REDACTED***"
    assert len(errors) == 1
    assert json.loads(errors[0])["error_category"] == "timeout"


def test_summary_and_export_formats(tmp_path):
    from blaze_hammer.stats.collector import StatsCollector

    collector = StatsCollector()
    collector.record_success(200, 0.05)
    stats = collector.snapshot(target="http://t", method="GET", requested=1)

    summary_path = write_summary(tmp_path, stats)
    loaded = json.loads(summary_path.read_text())
    assert loaded["target"] == "http://t"

    json_out = tmp_path / "out.json"
    export_results(json_out, stats)
    assert json.loads(json_out.read_text())["success"] == 1

    csv_out = tmp_path / "out.csv"
    export_results(csv_out, stats)
    row = next(csv.reader(csv_out.open()))
    header = next(csv.reader(csv_out.open()))
    _ = row
    assert "p99_ms" in header
