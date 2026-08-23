"""Bounded in-process fixed-window rate limiting for a single Cloud Run instance."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, requests: int, window_seconds: int) -> None:
        self.requests = requests
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.requests:
                return False
            events.append(now)
            if len(self._events) > 10_000:
                for stale_key in tuple(self._events):
                    stale_events = self._events[stale_key]
                    while stale_events and stale_events[0] <= cutoff:
                        stale_events.popleft()
                    if not stale_events:
                        self._events.pop(stale_key, None)
            return True
