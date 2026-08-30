"""Stats collector and percentile math."""

from __future__ import annotations

from blaze_hammer.stats.collector import StatsCollector, summarize
from blaze_hammer.stats.models import percentile


def test_percentile_known_inputs():
    values = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert percentile(values, 50) == 3.0
    assert percentile(values, 0) == 1.0
    assert percentile(values, 100) == 5.0
    assert percentile([], 95) is None


def test_collector_counts_and_snapshot():
    collector = StatsCollector()
    for i in range(10):
        collector.record_success(200, latency_s=i / 100)
    collector.record_success(201, 0.05)
    collector.record_failure("timeout", "timeout: read timed out")
    collector.record_failure("timeout", "timeout: read timed out")  # dedup sample
    collector.record_retry()

    stats = collector.snapshot(target="https://x", method="GET", requested=13)
    assert stats.completed == 13 and stats.success == 11 and stats.failed == 2
    assert stats.status_codes == {200: 10, 201: 1}
    assert stats.error_counts == {"timeout": 2}
    assert len(stats.error_samples["timeout"]) == 1
    assert stats.retries == 1
    lat = stats.latency
    # Latency samples track successful responses only.
    assert lat.count == 11 and lat.min_s == 0.0
    assert lat.p50_s is not None and lat.p99_s >= lat.p95_s >= lat.p90_s


def test_summary_empty():
    summary = summarize([])
    assert summary.count == 0 and summary.p99_s is None


def test_to_dict_export_shape():
    collector = StatsCollector()
    collector.record_success(200, 0.123456)
    stats = collector.snapshot(target="http://t", method="POST", requested=1)
    data = stats.to_dict()
    assert data["latency_ms"]["p50"] == round(123.456, 3)
    assert data["status_codes"] == {"200": 1}
    assert data["rps"] >= 0
