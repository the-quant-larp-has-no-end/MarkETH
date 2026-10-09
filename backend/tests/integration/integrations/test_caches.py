"""The market cache and the session cache against a real Redis (SPEC §3, §9, A.3, A.4)."""

import asyncio
from uuid import uuid4, uuid7

import pytest
from redis.asyncio import Redis

from app.core.config import RedisSettings
from app.integrations.redis.client import RedisClient
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.session_cache import SessionCache

pytestmark = pytest.mark.integration


@pytest.fixture
def markets(redis_client: RedisClient, redis_settings: RedisSettings) -> MarketCache:
    return MarketCache(redis_client, redis_settings)


@pytest.fixture
def sessions(redis_client: RedisClient) -> SessionCache:
    return SessionCache(redis_client)


async def test_market_cache_returns_what_was_set_and_none_on_a_miss(markets: MarketCache) -> None:
    summary_key = MarketCache.market_key(uuid7())
    list_key = MarketCache.list_key(f"status-{uuid4().hex}")
    assert await markets.get(summary_key) is None
    assert await markets.get(list_key) is None

    await markets.set(summary_key, '{"p_yes":0.5}')
    await markets.set(list_key, '[{"p_yes":0.5}]')

    assert await markets.get(summary_key) == '{"p_yes":0.5}'
    assert await markets.get(list_key) == '[{"p_yes":0.5}]'


async def test_market_cache_entries_expire_after_the_configured_ttl(
    markets: MarketCache, raw_redis: Redis, redis_settings: RedisSettings
) -> None:
    ttl_ms = redis_settings.market_cache_ttl_seconds * 1000
    summary_key = MarketCache.market_key(uuid7())
    await markets.invalidate_market(uuid7())

    await markets.set(summary_key, "{}")
    await markets.set(MarketCache.list_key("OPEN"), "[]")
    lists_ttl_ms = await raw_redis.pttl("cache:markets:lists")
    await asyncio.sleep(0.2)
    await markets.set(MarketCache.list_key(None), "[]")

    assert 0 < await raw_redis.pttl(summary_key) <= ttl_ms
    assert 0 < lists_ttl_ms <= ttl_ms
    assert await raw_redis.pttl("cache:markets:lists") < lists_ttl_ms, (
        "a later list must not keep an earlier one alive beyond the TTL"
    )


async def test_invalidating_a_market_drops_its_summary_and_every_list(
    markets: MarketCache,
) -> None:
    traded, untouched = uuid7(), uuid7()
    await markets.set(MarketCache.market_key(traded), '{"version":1}')
    await markets.set(MarketCache.market_key(untouched), '{"version":7}')
    await markets.set(MarketCache.list_key(None), "[1]")
    await markets.set(MarketCache.list_key("OPEN"), "[2]")
    await markets.set(MarketCache.list_key("RESOLVED"), "[3]")

    await markets.invalidate_market(traded)

    assert await markets.get(MarketCache.market_key(traded)) is None
    assert await markets.get(MarketCache.list_key(None)) is None
    assert await markets.get(MarketCache.list_key("OPEN")) is None
    assert await markets.get(MarketCache.list_key("RESOLVED")) is None
    assert await markets.get(MarketCache.market_key(untouched)) == '{"version":7}'


async def test_session_cache_round_trip_and_delete(
    sessions: SessionCache, raw_redis: Redis
) -> None:
    session_id = uuid4().hex
    payload = {"user_id": str(uuid7()), "role": "USER", "expires_at": "2026-11-08T12:00:00+00:00"}
    assert await sessions.get(session_id) is None

    await sessions.set(session_id, payload, ttl_seconds=60)

    assert await sessions.get(session_id) == payload
    assert 0 < await raw_redis.ttl(f"session:{session_id}") <= 60

    await sessions.delete(session_id)

    assert await sessions.get(session_id) is None


async def test_session_cache_keeps_nothing_for_a_ttl_that_has_run_out(
    sessions: SessionCache,
) -> None:
    session_id = uuid4().hex

    await sessions.set(session_id, {"user_id": "u"}, ttl_seconds=0)

    assert await sessions.get(session_id) is None


async def test_session_cache_treats_an_unreadable_entry_as_a_miss(
    sessions: SessionCache, raw_redis: Redis
) -> None:
    session_id = uuid4().hex
    await raw_redis.set(f"session:{session_id}", "not json", ex=60)

    assert await sessions.get(session_id) is None
