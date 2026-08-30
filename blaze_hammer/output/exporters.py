"""Response saving and result export (summary / jsonl / json / csv)."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from blaze_hammer.errors import ExportError
from blaze_hammer.output.filters import truncate_text
from blaze_hammer.security.redaction import redact_mapping

if TYPE_CHECKING:
    from blaze_hammer.engine.runner import RequestOutcome
    from blaze_hammer.stats.models import RunStats

FLUSH_EVERY = 128


class ResponseRecorder:
    """Observer that appends outcomes to responses.jsonl / errors.jsonl.

    Buffered writes flushed every ``FLUSH_EVERY`` records and on close;
    payloads/headers/bodies are redacted and size-capped.
    """

    def __init__(
        self,
        directory: Path,
        *,
        sensitive_names: tuple[str, ...] = (),
        max_body_chars: int = 2000,
    ) -> None:
        self._dir = directory
        self._sensitive = sensitive_names
        self._max_body = max_body_chars
        self._responses_file: IO[str] | None = None
        self._errors_file: IO[str] | None = None
        self._since_flush = 0
        self.count = 0

    def _ensure_open(self) -> None:
        if self._responses_file is not None:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._responses_file = (self._dir / "responses.jsonl").open("a", encoding="utf-8")
            self._errors_file = (self._dir / "errors.jsonl").open("a", encoding="utf-8")
        except OSError as exc:
            raise ExportError(
                f"Cannot open response files in {self._dir}", reason=str(exc)
            ) from exc

    def __call__(self, outcome: RequestOutcome) -> None:
        self._ensure_open()
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "index": outcome.index,
            "method": outcome.method,
            "url": outcome.url,
            "ok": outcome.ok,
            "status_code": outcome.status_code,
            "latency_ms": round(outcome.latency_s * 1000, 2),
            "attempts": outcome.attempts,
        }
        if outcome.resolved_headers is not None:
            record["headers"] = redact_mapping(outcome.resolved_headers, self._sensitive)
        if outcome.resolved_payload is not None:
            record["payload"] = redact_mapping(outcome.resolved_payload, self._sensitive)
        if outcome.body is not None and outcome.body.text is not None:
            body, _omitted = truncate_text(outcome.body.text, self._max_body)
            record["response_body"] = body
            record["response_truncated"] = outcome.body.truncated
        handle = self._responses_file if outcome.ok else self._errors_file
        assert handle is not None
        if not outcome.ok:
            record["error_category"] = outcome.error_category
            record["error"] = outcome.error_message
        try:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            raise ExportError("Failed writing response record", reason=str(exc)) from exc
        self.count += 1
        self._since_flush += 1
        if self._since_flush >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        for handle in (self._responses_file, self._errors_file):
            if handle is not None:
                handle.flush()
        self._since_flush = 0

    def close(self) -> None:
        self.flush()
        for handle in (self._responses_file, self._errors_file):
            if handle is not None:
                handle.close()
        self._responses_file = None
        self._errors_file = None


def write_summary(directory: Path, stats: RunStats) -> Path:
    """Persist summary.json next to saved responses."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "summary.json"
        path.write_text(
            json.dumps(stats.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path
    except OSError as exc:
        raise ExportError(f"Cannot write summary to {directory}", reason=str(exc)) from exc


_CSV_COLUMNS = (
    "target",
    "method",
    "requested",
    "completed",
    "success",
    "failed",
    "retries",
    "duration_s",
    "rps",
    "interrupted",
    "p50_ms",
    "p90_ms",
    "p95_ms",
    "p99_ms",
)


def export_results(path: Path, stats: RunStats) -> Path:
    """Export the run summary as JSON or CSV (chosen by file suffix)."""
    data = stats.to_dict()
    latency = data.get("latency_ms", {})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".csv":
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(_CSV_COLUMNS)
                writer.writerow(
                    [
                        data["target"],
                        data["method"],
                        data["requested"],
                        data["completed"],
                        data["success"],
                        data["failed"],
                        data["retries"],
                        data["duration_s"],
                        data["rps"],
                        data["interrupted"],
                        latency.get("p50"),
                        latency.get("p90"),
                        latency.get("p95"),
                        latency.get("p99"),
                    ]
                )
        else:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError as exc:
        raise ExportError(f"Cannot export results to {path}", reason=str(exc)) from exc
