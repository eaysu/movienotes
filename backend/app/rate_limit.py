"""Small process-local rate limiter for expensive anonymous endpoints."""

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Combined long-window quota plus a short burst ceiling."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        burst: int,
        burst_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.burst = burst
        self.burst_seconds = burst_seconds
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._checks = 0

    def _retry_after(self, bucket: deque[float], now: float) -> float:
        """Seconds until this bucket has room again; 0 when it has room now."""
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self.limit:
            return bucket[0] + self.window_seconds - now
        burst_cutoff = now - self.burst_seconds
        recent = sum(timestamp > burst_cutoff for timestamp in bucket)
        if recent >= self.burst:
            return bucket[-self.burst] + self.burst_seconds - now
        return 0.0

    async def peek(self, key: str) -> tuple[bool, int]:
        """Report whether ``key`` has room, without spending any of it.

        A quota that only failures pay into still has to be read on every
        attempt; reading it must not itself be an attempt.
        """
        async with self._lock:
            retry_after = self._retry_after(
                self._buckets.setdefault(key, deque()), self._clock()
            )
        if retry_after > 0:
            return False, max(1, math.ceil(retry_after))
        return True, 0

    async def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` and consume allowed calls."""
        now = self._clock()
        async with self._lock:
            self._checks += 1
            bucket = self._buckets.setdefault(key, deque())
            retry_after = self._retry_after(bucket, now)

            if retry_after > 0:
                return False, max(1, math.ceil(retry_after))

            bucket.append(now)

            # Bound memory under distributed-IP abuse without a cleanup task.
            if self._checks % 1000 == 0:
                stale_before = now - self.window_seconds
                self._buckets = {
                    bucket_key: timestamps
                    for bucket_key, timestamps in self._buckets.items()
                    if timestamps and timestamps[-1] > stale_before
                }

            return True, 0
