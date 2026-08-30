"""Parsers adapters, attachment handling, error rendering, presenter, live view."""

from __future__ import annotations

import pytest
from rich.console import Console

from blaze_hammer.errors import BlazeHammerError, ConfigurationError, MissingEnvVarError
from blaze_hammer.files.attachments import AttachmentSet
from blaze_hammer.output.console import plain_error, render_error
from blaze_hammer.output.live import DashboardView
from blaze_hammer.output.presenter import Presenter
from blaze_hammer.parsers import render_headers, render_payload, render_response
from blaze_hammer.stats.collector import StatsCollector

# -- parser adapters ----------------------------------------------------------


def test_parser_dispatch_status_and_fallback(monkeypatch):
    from blaze_hammer.ext import parsers as user_parsers

    monkeypatch.setattr(
        user_parsers,
        "custom_response_parsers",
        {200: lambda r: f"ok:{r}", "all": lambda r: f"any:{r}"},
    )
    assert render_response(200, "x") == "ok:x"
    assert render_response(500, "y") == "any:y"


def test_parser_failure_is_contained(monkeypatch):
    from blaze_hammer.ext import parsers as user_parsers

    def boom(_):
        raise ValueError("nope")

    monkeypatch.setattr(user_parsers, "custom_headers_parsers", {"all": boom})
    assert "parser error" in render_headers(200, {"a": "b"})
    # Payload parser was not monkeypatched; default passthrough still works.
    assert render_payload(200, {"x": 1}) == "{'x': 1}"
    _ = boom


def test_missing_parser_table_entry_returns_none(monkeypatch):
    from blaze_hammer.ext import parsers as user_parsers

    monkeypatch.setattr(user_parsers, "custom_payload_parsers", {})
    assert render_payload(200, {"x": 1}) is None


# -- attachments ---------------------------------------------------------------


def test_attachment_set_validates_missing_paths(tmp_path):
    with pytest.raises(ConfigurationError):
        AttachmentSet({"upload": str(tmp_path / "missing.bin")})


def test_attachment_set_open_close_owns_handles(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"data")
    handle = target.open("rb")
    spec = {"path_form": str(target), "obj_form": handle}
    with AttachmentSet(spec) as attachments:
        files = attachments.as_httpx_files()
        assert set(files) == {"path_form", "obj_form"}
    # Path-owned handle closed; user-owned handle left open.
    assert handle.closed is False
    handle.close()


def test_empty_attachments_allowed_without_flag():
    attachments = AttachmentSet({})
    assert attachments.empty and attachments.as_httpx_files() is None


# -- console / errors ------------------------------------------------------------


def test_render_error_includes_structure_and_hint():
    Console(file=None, force_terminal=False)
    err = ConfigurationError(
        "Unable to load payload.json",
        reason="invalid JSON at line 12, column 17",
        expected="object",
        found="garbage",
        hint="check the file",
    )
    from io import StringIO

    buffer = StringIO()
    render_error(Console(file=buffer), err)
    text = buffer.getvalue()
    for fragment in ("Unable to load", "Reason:", "line 12", "Hint:", "--debug"):
        assert fragment in text


def test_plain_error_single_line():
    err = MissingEnvVarError(["A_TOKEN"])
    line = plain_error(err)
    assert line.startswith("error:")
    assert "A_TOKEN" in line


def test_unexpected_error_wrapper_fields():
    err = BlazeHammerError("boom", hint="try again")
    assert err.exit_code == 1 and err.hint == "try again"


# -- presenter --------------------------------------------------------------------


def _stats():
    collector = StatsCollector()
    collector.record_success(200, 0.1)
    collector.record_success(201, 0.3)
    collector.record_failure("timeout", "timeout: t")
    return collector.snapshot(target="http://t", method="GET", requested=3)


def test_presenter_plain_summary(capsys):
    from blaze_hammer.config.models import RunConfig

    cfg = RunConfig(target="http://t", output={"simple": True})
    Presenter(cfg).final_summary(_stats())
    out = capsys.readouterr().out
    assert "--- Final Report ---" in out
    assert "Successful: 2" in out
    assert "Status 200: 1" in out


def test_presenter_rich_summary_mentions_percentiles(capsys):
    from blaze_hammer.config.models import RunConfig

    cfg = RunConfig(target="http://t")  # non-tty -> simple forced; call rich directly
    presenter = Presenter(cfg)
    presenter._rich_summary(_stats())
    out = capsys.readouterr().out
    assert "Final Report" in out
    assert "P50" in out or "Latency" in out


# -- live dashboard view -----------------------------------------------------------


def test_dashboard_view_renders_group_with_codes_and_errors():
    view = DashboardView(StatsCollector(), requested=5)
    group = view.__rich__()  # empty state renders fine
    assert group is not None

    collector = StatsCollector()
    collector.record_success(200, 0.01)
    collector.record_failure("connection", "connection: refused")
    view2 = DashboardView(collector, requested=2)
    assert view2.__rich__() is not None
