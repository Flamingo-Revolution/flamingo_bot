"""Durable request quotas that bound spend across every serving instance.

The in-process :class:`~flamingo_bot.rate_limit.RateLimiter` sheds bursts cheaply
but resets whenever an instance recycles and counts separately on each one, so it
cannot express "one hundred requests per day" for the service as a whole. These
quotas are the durable ceiling: a per-visitor allowance and a service-wide daily
budget, both consumed before any paid model call.

Windows are fixed and epoch-aligned. ``window_start = now // window_seconds``
puts an hourly window on the UTC hour and a daily window on UTC midnight, which
keeps a window addressable by a single integer and therefore by a single stored
counter. The tradeoff is the usual fixed-window one: a visitor may spend the tail
of one window and the head of the next back to back. The burst limiter covers
that spike, and the daily budget still bounds the day.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

QuotaScope = Literal["client", "daily"]


@dataclass(frozen=True)
class QuotaWindow:
    """One fixed-window allowance."""

    limit: int
    window_seconds: int

    def bucket(self, now: float) -> int:
        return int(now // self.window_seconds)

    def seconds_until_reset(self, now: float) -> int:
        elapsed = now - self.bucket(now) * self.window_seconds
        return max(1, int(self.window_seconds - elapsed) + 1)


@dataclass(frozen=True)
class QuotaDecision:
    """The outcome of trying to consume one request against the quotas."""

    allowed: bool
    #: Which allowance ran out. ``None`` when the request was allowed.
    scope: QuotaScope | None = None
    #: Seconds until the exhausted window rolls over. Zero when allowed.
    retry_after_seconds: int = 0


ALLOWED = QuotaDecision(allowed=True)


class InMemoryRequestQuota:
    """Single-process quota used in development, tests, and dry runs.

    Semantically identical to the Firestore implementation, but the counters live
    in this process, so it must not be used where more than one instance serves
    traffic.
    """

    def __init__(
        self,
        client_window: QuotaWindow,
        daily_window: QuotaWindow,
        *,
        time_source: Callable[[], float] = time.time,
    ) -> None:
        self.client_window = client_window
        self.daily_window = daily_window
        self._time_source = time_source
        self._client_counts: dict[tuple[str, int], int] = {}
        self._daily_counts: dict[int, int] = {}
        self._lock = asyncio.Lock()

    async def consume(self, client_key: str) -> QuotaDecision:
        now = self._time_source()
        client_bucket = self.client_window.bucket(now)
        daily_bucket = self.daily_window.bucket(now)
        async with self._lock:
            client_count = self._client_counts.get((client_key, client_bucket), 0)
            if client_count >= self.client_window.limit:
                return QuotaDecision(
                    allowed=False,
                    scope="client",
                    retry_after_seconds=self.client_window.seconds_until_reset(now),
                )
            daily_count = self._daily_counts.get(daily_bucket, 0)
            if daily_count >= self.daily_window.limit:
                return QuotaDecision(
                    allowed=False,
                    scope="daily",
                    retry_after_seconds=self.daily_window.seconds_until_reset(now),
                )
            self._client_counts[(client_key, client_bucket)] = client_count + 1
            self._daily_counts[daily_bucket] = daily_count + 1
            self._prune(client_bucket, daily_bucket)
            return ALLOWED

    def _prune(self, client_bucket: int, daily_bucket: int) -> None:
        """Drop closed windows so a long-lived instance does not grow without bound."""
        if len(self._client_counts) > 10_000:
            for key in tuple(self._client_counts):
                if key[1] != client_bucket:
                    self._client_counts.pop(key, None)
        for bucket in tuple(self._daily_counts):
            if bucket != daily_bucket:
                self._daily_counts.pop(bucket, None)


class UnlimitedRequestQuota:
    """Explicit opt-out used where a durable quota is not wanted, such as a dry run."""

    async def consume(self, client_key: str) -> QuotaDecision:
        del client_key
        return ALLOWED
