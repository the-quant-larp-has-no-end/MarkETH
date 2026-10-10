"""What every Redis adapter does when Redis is down (SPEC §9, "When a dependency fails").

Redis never decides anything, so its absence must be cheap and survivable: the limiter
says so and lets the caller choose, the caches miss, publishing is dropped, streams are
refused, and a dead Redis costs one timeout per cool-down instead of one per call.
"""

import asyncio
import time
from collections.abc import Awaitable
from uuid import uuid4, uuid7

import pytest
from redis.asyncio import Redis

from app.core.config import RedisSettings, Settings
from app.core.enums import MarketStatus
from app.integrations.redis.client import COOLDOWN_SECONDS, RedisClient, RedisUnavailableError
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.quote_bus import QuoteBus
from app.integrations.redis.rate_limiter import RateLimit, RedisRateLimiter
from tests.support.containers import LOOPBACK
from tests.support.network import SilentServer

pytestmark = pytest.mark.integration

ALMOST_IMMEDIATELY_SECONDS = 0.05


async def seconds_until_unavailable(call: Awaitable[object]) -> float:
    started = time.perf_counter()
    with pytest.raises(RedisUnavailableError):
        await call
    return time.perf_counter() - started


async def test_rate_limiter_raises_quickly_so_the_caller_can_decide(
    unreachable_redis: RedisClient, base_settings: Settings
) -> None:
    limiter = RedisRateLimiter(unreachable_redis)
    rule = RateLimit(name="code-redeem-user", limit=5, window_seconds=600)

    elapsed = await seconds_until_unavailable(limiter.hit(rule, str(uuid7())))

    assert elapsed < base_settings.redis.timeout_seconds + 0.25


async def test_the_market_cache_reports_a_miss_and_never_raises(
    unreachable_redis: RedisClient, base_settings: Settings
) -> None:
    markets = MarketCache(unreachable_redis, base_settings.redis)
    key = MarketCache.list_key([MarketStatus.OPEN, MarketStatus.HALTED])

    await markets.set(key, '{"items":[]}')
    await markets.invalidate_lists()

    assert await markets.get(key) is None


async def test_publish_is_dropped_and_subscribe_is_refused(
    unreachable_redis: RedisClient,
) -> None:
    bus = QuoteBus(unreachable_redis)
    market_id = uuid7()
    await bus.start()
    try:
        await bus.publish(market_id, {"type": "quote", "version": 1})

        assert not bus.is_connected
        with pytest.raises(RedisUnavailableError):
            async with bus.subscribe(market_id):
                pytest.fail("a subscription opened without Redis")
    finally:
        await bus.stop()


async def test_ping_reports_false_instead_of_raising(unreachable_redis: RedisClient) -> None:
    assert await unreachable_redis.ping() is False


async def test_calls_during_the_cooldown_return_almost_immediately(
    unreachable_redis: RedisClient,
) -> None:
    await seconds_until_unavailable(unreachable_redis.hash_get("key", "field"))

    for _ in range(20):
        elapsed = await seconds_until_unavailable(unreachable_redis.hash_get("key", "field"))
        assert elapsed < ALMOST_IMMEDIATELY_SECONDS


async def test_hanging_redis_costs_one_timeout_per_cooldown(
    silent_server: SilentServer, base_settings: Settings
) -> None:
    settings = base_settings.redis.model_copy(update={"host": LOOPBACK, "port": silent_server.port})
    client = RedisClient(settings)
    try:
        first = await seconds_until_unavailable(client.hash_get("key", "field"))
        assert settings.timeout_seconds * 0.8 <= first < settings.timeout_seconds + 0.25
        assert silent_server.connections == 1

        for _ in range(20):
            waited = await seconds_until_unavailable(client.hash_get("key", "field"))
            assert waited < ALMOST_IMMEDIATELY_SECONDS
        assert silent_server.connections == 1, "a call during the cool-down reached the network"

        await asyncio.sleep(COOLDOWN_SECONDS)
        probes = await asyncio.gather(
            *(client.hash_get("key", "field") for _ in range(5)), return_exceptions=True
        )
        assert all(isinstance(outcome, RedisUnavailableError) for outcome in probes)
        assert silent_server.connections == 2, "exactly one call may probe after the cool-down"
    finally:
        await client.close()


async def test_client_works_again_once_redis_answers_after_the_cooldown(
    redis_client: RedisClient, raw_redis: Redis, redis_settings: RedisSettings
) -> None:
    key = f"test:{uuid4().hex}"
    await redis_client.hash_set(key, "field", "kept", ttl_seconds=60)

    await raw_redis.client_pause(redis_settings.timeout_ms * 2)
    await seconds_until_unavailable(redis_client.hash_get(key, "field"))
    assert await redis_client.ping() is False, "the cool-down must not wait for Redis"

    await asyncio.sleep(COOLDOWN_SECONDS + 0.05)
    assert await redis_client.hash_get(key, "field") == "kept"
    assert await redis_client.ping() is True


async def test_command_redis_answers_with_an_error_raises_but_starts_no_cooldown(
    redis_client: RedisClient, raw_redis: Redis
) -> None:
    key = f"test:{uuid4().hex}"
    await raw_redis.set(key, "not a counter", ex=60)

    with pytest.raises(RedisUnavailableError, match="refused"):
        await redis_client.count_in_window(key, window_seconds=60)

    assert await redis_client.ping() is True, "an error reply proves Redis is reachable"
