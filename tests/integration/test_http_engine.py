"""Engine integration tests against a local threaded HTTP server."""

from __future__ import annotations

import asyncio
import random
import time

from blaze_hammer.config.models import RunConfig
from blaze_hammer.engine.client import build_client
from blaze_hammer.engine.errors import ErrorCategory
from blaze_hammer.engine.planner import RequestPlanner, RequestTemplates
from blaze_hammer.engine.runner import LoadTestRunner
from blaze_hammer.stats.collector import StatsCollector
from blaze_hammer.templating import (
    FakerFactory,
    ResolveContext,
    TemplateResolver,
    build_default_registry,
)


def make_runner(url: str, *, templates: dict | None = None, **cfg_kwargs):
    cfg = RunConfig(target=url, **cfg_kwargs)
    registry = build_default_registry()
    rng = random.Random(cfg.seed)
    ctx = ResolveContext(rng=rng, faker=FakerFactory(rng))
    resolver = TemplateResolver(registry, ctx)
    tpl = RequestTemplates(
        headers=(templates or {}).get("headers"),
        payload=(templates or {}).get("payload"),
    )
    planner = RequestPlanner(cfg, resolver, tpl)
    client = build_client(cfg)
    collector = StatsCollector()
    from blaze_hammer.engine.rate_limiter import TokenBucket

    bucket = TokenBucket(cfg.rate) if cfg.rate else None
    runner = LoadTestRunner(cfg, planner, client, collector, bucket=bucket)
    return cfg, runner, collector, client


async def drive(runner, client):
    try:
        return await runner.run()
    finally:
        await client.aclose()


def test_basic_get_run_completes(server_url):
    cfg, runner, collector, client = make_runner(server_url + "/echo", requests=25, concurrency=5)
    stats = asyncio.run(drive(runner, client))
    assert stats.completed == 25 and stats.success == 25
    assert stats.status_codes == {200: 25}
    assert stats.interrupted is False
    assert stats.latency.p99_s is not None


def test_concurrency_is_bounded(server_url, server):
    cfg, runner, _c, client = make_runner(server_url + "/slow?ms=80", requests=24, concurrency=4)
    started = time.perf_counter()
    stats = asyncio.run(drive(runner, client))
    elapsed = time.perf_counter() - started
    assert stats.success == 24
    # The SERVER observed at most ~concurrency simultaneous handlers.
    assert server.state.max_concurrent <= 5
    # Pacing proof: 24 requests at <=4 in flight * 80ms each takes >= ~0.4s.
    assert elapsed >= 0.35


def test_post_payload_resolved_each_request(server_url, server):
    template = {
        "headers": None,
        "payload": {
            "username": "{faker.user_name}",
            "n": "{int(min=7, max=7)}",
        },
    }
    cfg, runner, _c, client = make_runner(
        server_url + "/echo",
        method="POST",
        requests=6,
        concurrency=2,
        templates=template,
    )
    stats = asyncio.run(drive(runner, client))
    assert stats.success == 6
    sent = [body for body in server.state.bodies if "username" in body]
    assert len(sent) == 6
    assert all(str(body["n"]) == "7" for body in sent)
    usernames = {body["username"] for body in sent}
    assert len(usernames) == 6  # every request generated fresh data


def test_get_headers_placeholders_resolved(server_url, server):
    """Regression: GET used to send headers verbatim."""
    template = {"headers": {"X-Trace": "{uuid}"}, "payload": None}
    cfg, runner, _c, client = make_runner(
        server_url + "/echo", requests=2, concurrency=2, templates=template
    )
    asyncio.run(drive(runner, client))
    traces = [headers.get("X-Trace") for headers in server.state.seen_headers[-2:]]
    assert all(traces), traces
    assert traces[0] != traces[1]


def test_retries_recover_from_flaky(server_url):
    flaky_url = server_url + "/flaky"
    cfg, runner, collector, client = make_runner(
        flaky_url, requests=1, concurrency=1, retries={"max_retries": 5}
    )
    # Make the endpoint succeed on 3rd global attempt.
    stats = asyncio.run(drive(runner, client))
    assert stats.success == 1
    assert stats.retries == 2
    assert stats.status_codes.get(200) == 1


def test_timeout_classified(server_url):
    cfg, runner, collector, client = make_runner(
        server_url + "/slow?ms=2000", requests=1, concurrency=1, timeout=0.3
    )
    stats = asyncio.run(drive(runner, client))
    assert stats.failed == 1
    assert stats.error_counts.get(ErrorCategory.TIMEOUT.value) == 1


def test_rate_limit_paces_requests(server_url):
    cfg, runner, _c, client = make_runner(
        server_url + "/echo", requests=10, concurrency=10, rate=20.0
    )
    started = time.perf_counter()
    stats = asyncio.run(drive(runner, client))
    elapsed = time.perf_counter() - started
    assert stats.success == 10
    # 10 requests at 20/s => at least ~0.45s of pacing (9 gaps).
    assert elapsed >= 0.35


def test_graceful_stop_returns_partial_stats(server_url):
    cfg, runner, collector, client = make_runner(
        server_url + "/slow?ms=50", requests=500, concurrency=5
    )

    async def scenario():
        async def stop_soon():
            await asyncio.sleep(0.2)
            runner.request_stop()

        stopper = asyncio.create_task(stop_soon())
        try:
            return await drive(runner, client)
        finally:
            stopper.cancel()

    stats = asyncio.run(scenario())
    assert stats.interrupted is True
    assert stats.completed < 500
    assert stats.completed > 0


def test_seed_determinism_across_runs(server_url, server):
    template = {
        "headers": None,
        "payload": {
            "username": "{faker.user_name}",
            "u": "{uuid}",
            "e": "{email(prefix=x_, length=6)}",
        },
    }

    def collect(seed):
        cfg, runner, _c, client = make_runner(
            server_url + "/echo",
            method="POST",
            requests=5,
            concurrency=1,
            seed=seed,
            templates=dict(template),
        )
        stats = asyncio.run(drive(runner, client))
        assert stats.success == 5
        return [(b["username"], b["u"], b["e"]) for b in server.state.bodies[-5:]]

    first = collect(1234)
    second = collect(1234)
    third = collect(4321)
    assert first == second
    assert first != third


def test_generation_error_recorded_not_fatal(server_url, monkeypatch):
    cfg, runner, collector, client = make_runner(server_url + "/echo", requests=3, concurrency=1)
    calls = {"n": 0}
    original = runner._planner.next_plan

    def flaky_plan(index):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated generation crash")
        return original(index)

    monkeypatch.setattr(runner._planner, "next_plan", flaky_plan)
    stats = asyncio.run(drive(runner, client))
    assert stats.success == 2 and stats.failed == 1
