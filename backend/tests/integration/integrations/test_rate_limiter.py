"""Fixed-window rate limits (SPEC §2, §3, §5, §9, A.4).

Every test runs twice: against Redis in real time, and against the in-process fallback
with a clock that only moves when told to. Both must behave the same.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.integrations.redis.client import RedisClient
from app.integrations.redis.rate_limiter import (
    InProcessRateLimiter,
    RateLimit,
    RateLimiter,
    RedisRateLimiter,
)

pytestmark = pytest.mark.integration


class ManualClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@dataclass(frozen=True)
class LimiterUnderTest:
    limiter: RateLimiter
    let_pass: Callable[[float], Awaitable[None]]
    """Let that many seconds go by, in whatever time the limiter lives in."""


@pytest.fixture(params=["redis", "in-process"])
def under_test(request: pytest.FixtureRequest, redis_client: RedisClient) -> LimiterUnderTest:
    if request.param == "redis":
        return LimiterUnderTest(RedisRateLimiter(redis_client), asyncio.sleep)
    clock = ManualClock()

    async def advance(seconds: float) -> None:
        clock.advance(seconds)

    return LimiterUnderTest(InProcessRateLimiter(clock), advance)


def rule(limit: int, window_seconds: int) -> RateLimit:
    """A rule with a name of its own, so tests sharing one Redis cannot meet."""
    return RateLimit(name=f"test-{uuid4().hex}", limit=limit, window_seconds=window_seconds)


async def test_hit_after_the_limit_is_rejected_and_reports_a_retry_after(
    under_test: LimiterUnderTest,
) -> None:
    five_per_ten_minutes = rule(limit=5, window_seconds=600)

    within = [await under_test.limiter.hit(five_per_ten_minutes, "alice") for _ in range(5)]
    sixth = await under_test.limiter.hit(five_per_ten_minutes, "alice")

    assert [decision.allowed for decision in within] == [True] * 5
    assert [decision.remaining for decision in within] == [4, 3, 2, 1, 0]
    assert [decision.retry_after_seconds for decision in within] == [0] * 5
    assert not sixth.allowed
    assert sixth.remaining == 0
    assert 599 <= sixth.retry_after_seconds <= 600


async def test_new_window_allows_again(under_test: LimiterUnderTest) -> None:
    once_per_second = rule(limit=1, window_seconds=1)
    assert (await under_test.limiter.hit(once_per_second, "alice")).allowed
    assert not (await under_test.limiter.hit(once_per_second, "alice")).allowed

    await under_test.let_pass(1.1)

    again = await under_test.limiter.hit(once_per_second, "alice")
    assert again.allowed
    assert again.remaining == 0


async def test_rejected_hits_do_not_extend_the_window(under_test: LimiterUnderTest) -> None:
    once_per_two_seconds = rule(limit=1, window_seconds=2)
    await under_test.limiter.hit(once_per_two_seconds, "alice")

    await under_test.let_pass(1.2)
    rejected = await under_test.limiter.hit(once_per_two_seconds, "alice")
    await under_test.let_pass(1.0)
    after_the_window = await under_test.limiter.hit(once_per_two_seconds, "alice")

    assert not rejected.allowed
    assert rejected.retry_after_seconds == 1
    assert after_the_window.allowed


async def test_concurrent_hits_never_exceed_the_limit(under_test: LimiterUnderTest) -> None:
    ten_per_minute = rule(limit=10, window_seconds=60)

    decisions = await asyncio.gather(
        *(under_test.limiter.hit(ten_per_minute, "alice") for _ in range(50))
    )

    assert sum(decision.allowed for decision in decisions) == 10


async def test_subjects_and_rules_are_counted_separately(under_test: LimiterUnderTest) -> None:
    first, second = rule(limit=1, window_seconds=60), rule(limit=1, window_seconds=60)
    assert (await under_test.limiter.hit(first, "alice")).allowed

    assert (await under_test.limiter.hit(first, "bob")).allowed
    assert (await under_test.limiter.hit(second, "alice")).allowed
    assert not (await under_test.limiter.hit(first, "alice")).allowed
