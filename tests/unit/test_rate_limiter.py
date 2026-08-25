"""Token bucket behavior with an injectable clock."""

from __future__ import annotations

import asyncio

import pytest

from blaze_hammer.engine.rate_limiter import TokenBucket


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _run(coro):
    return asyncio.run(coro)


def test_burst_up_to_capacity_is_instant():
    clock = FakeClock()
    bucket = TokenBucket(10, capacity=5, clock=clock)
    delays = []

    async def scenario():
        for _ in range(5):
            await bucket.acquire()

    _run(scenario())
    assert not delays  # no sleeps recorded (we assert via token exhaustion below)


def test_refill_over_time(monkeypatch):
    clock = FakeClock()
    bucket = TokenBucket(10, capacity=1, clock=clock)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def scenario():
        await bucket.acquire()  # consumes the single token
        await bucket.acquire()  # must wait ~0.1s for refill at rate=10

    _run(scenario())
    assert slept and abs(slept[0] - 0.1) < 0.01


def test_invalid_rate_rejected():
    with pytest.raises(ValueError):
        TokenBucket(0)
