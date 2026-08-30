"""Token-bucket rate limiter (requests per second)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class TokenBucket:
    """Lazy-refill token bucket.

    Consumed only by the scheduler task, so no locking is required.
    The injectable clock keeps tests deterministic.
    """

    def __init__(
        self,
        rate: float,
        *,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        # Default capacity 1 => smooth, strictly-paced sending. Callers can
        # opt into bursts by passing a larger capacity.
        self.capacity = capacity if capacity is not None else 1.0
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    async def acquire(self) -> None:
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            deficit = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(min(deficit, 1.0))
