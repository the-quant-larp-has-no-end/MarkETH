"""Fixed-window rate limits for code redemption and trades (SPEC §2, §5, §9).

A rule allows `limit` hits per `window_seconds` for one subject, a user's id. The
window starts with the subject's first hit and ends `window_seconds` later; hits beyond
the limit are counted but rejected.

Two implementations share those semantics. The services choose what an unreachable
Redis means: code redemption fails closed with 503, trades switch to the in-process
limiter (SPEC §9, "When a dependency fails").
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core.clock import Clock
from app.integrations.redis.client import RedisClient


@dataclass(frozen=True, slots=True)
class RateLimit:
    """At most `limit` hits per `window_seconds`. `name` keeps the rules' counters apart."""

    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    """Hits left in the current window after this one."""
    retry_after_seconds: int
    """Seconds until the window ends when the hit was rejected, otherwise 0."""


class RateLimiter(Protocol):
    async def hit(self, rule: RateLimit, subject: str) -> RateLimitDecision:
        """Count one attempt by `subject` against `rule` and say whether it is allowed."""
        ...


class RedisRateLimiter:
    """The limiter shared by every API process: one Redis counter per rule and subject.

    When Redis is unreachable: `hit` raises `RedisUnavailableError`. It never guesses a
    decision, so the caller decides between failing closed and falling back.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def hit(self, rule: RateLimit, subject: str) -> RateLimitDecision:
        count, remaining_ms = await self._redis.count_in_window(
            f"ratelimit:{rule.name}:{subject}", rule.window_seconds
        )
        return _decide(rule, count, remaining_ms / 1000)


@dataclass(slots=True)
class _Window:
    count: int
    ends_at: datetime


class InProcessRateLimiter:
    """The same rule kept in this process's memory: the trade fallback of SPEC A.4.

    It has no backend that can be unreachable. Its counters are per process, so with
    several API workers a user gets the limit once per worker; that is acceptable for
    the time Redis is down, and Postgres still decides every trade.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._windows: dict[tuple[str, str], _Window] = {}

    async def hit(self, rule: RateLimit, subject: str) -> RateLimitDecision:
        now = self._clock.now()
        key = (rule.name, subject)
        window = self._windows.get(key)
        if window is None or now >= window.ends_at:
            self._forget_ended_windows(now)
            window = _Window(count=0, ends_at=now + timedelta(seconds=rule.window_seconds))
            self._windows[key] = window
        window.count += 1
        return _decide(rule, window.count, (window.ends_at - now).total_seconds())

    def _forget_ended_windows(self, now: datetime) -> None:
        self._windows = {key: w for key, w in self._windows.items() if now < w.ends_at}


def _decide(rule: RateLimit, count: int, window_left_seconds: float) -> RateLimitDecision:
    allowed = count <= rule.limit
    return RateLimitDecision(
        allowed=allowed,
        remaining=max(rule.limit - count, 0),
        retry_after_seconds=0 if allowed else max(math.ceil(window_left_seconds), 1),
    )
