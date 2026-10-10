"""The market list cache against a real Redis (SPEC §9, A.4)."""

import asyncio
from itertools import permutations

import pytest
from redis.asyncio import Redis

from app.core.config import RedisSettings
from app.core.enums import MarketStatus
from app.integrations.redis.client import RedisClient
from app.integrations.redis.market_cache import MarketCache

pytestmark = pytest.mark.integration

LISTS_KEY = "cache:markets:lists"
"""The one Redis key the cache writes: a hash with a field per list."""

OPEN_AND_HALTED = (MarketStatus.OPEN, MarketStatus.HALTED)
SETTLED = (MarketStatus.RESOLVED, MarketStatus.VOID)
EVERY_PUBLIC_STATUS = (
    MarketStatus.OPEN,
    MarketStatus.HALTED,
    MarketStatus.CLOSED,
    MarketStatus.RESOLVED,
    MarketStatus.VOID,
)


@pytest.fixture
async def markets(
    redis_client: RedisClient, redis_settings: RedisSettings, raw_redis: Redis
) -> MarketCache:
    """The cache on an empty Redis key: an earlier test's lists are gone."""
    await raw_redis.delete(LISTS_KEY)
    return MarketCache(redis_client, redis_settings)


def test_one_set_of_statuses_has_one_key_however_a_request_names_it() -> None:
    """`?status=OPEN&status=HALTED` and `?status=HALTED&status=OPEN` are one list."""
    key = MarketCache.list_key(OPEN_AND_HALTED)

    for order in permutations(OPEN_AND_HALTED):
        assert MarketCache.list_key(order) == key
    assert MarketCache.list_key([MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.OPEN]) == key
    assert MarketCache.list_key(frozenset(OPEN_AND_HALTED)) == key


def test_different_sets_of_statuses_have_different_keys() -> None:
    sets = [
        (MarketStatus.OPEN,),
        (MarketStatus.HALTED,),
        OPEN_AND_HALTED,
        (MarketStatus.CLOSED,),
        SETTLED,
        EVERY_PUBLIC_STATUS,
    ]

    assert len({MarketCache.list_key(statuses) for statuses in sets}) == len(sets)


async def test_market_cache_returns_what_was_set_and_none_on_a_miss(markets: MarketCache) -> None:
    open_and_halted = MarketCache.list_key(OPEN_AND_HALTED)
    settled = MarketCache.list_key(SETTLED)
    assert await markets.get(open_and_halted) is None
    assert await markets.get(settled) is None

    await markets.set(open_and_halted, '{"items":[{"p_yes":0.5}]}')

    assert await markets.get(open_and_halted) == '{"items":[{"p_yes":0.5}]}'
    assert await markets.get(settled) is None, "one list says nothing about another"


async def test_market_cache_entries_expire_after_the_configured_ttl(
    markets: MarketCache, raw_redis: Redis, redis_settings: RedisSettings
) -> None:
    ttl_ms = redis_settings.market_cache_ttl_seconds * 1000

    await markets.set(MarketCache.list_key(OPEN_AND_HALTED), '{"items":[]}')
    lists_ttl_ms = await raw_redis.pttl(LISTS_KEY)
    await asyncio.sleep(0.2)
    await markets.set(MarketCache.list_key(EVERY_PUBLIC_STATUS), '{"items":[]}')

    assert 0 < lists_ttl_ms <= ttl_ms
    assert await raw_redis.pttl(LISTS_KEY) < lists_ttl_ms, (
        "a later list must not keep an earlier one alive beyond the TTL"
    )


async def test_invalidating_the_lists_drops_every_one_of_them(
    markets: MarketCache, raw_redis: Redis
) -> None:
    """A trade or a lifecycle change can alter any list, so all of them go at once."""
    keys = [
        MarketCache.list_key(statuses)
        for statuses in (EVERY_PUBLIC_STATUS, OPEN_AND_HALTED, SETTLED)
    ]
    for number, key in enumerate(keys):
        await markets.set(key, f'{{"items":[{number}]}}')
    await raw_redis.set("cache:something-else", "kept", ex=60)

    await markets.invalidate_lists()

    for key in keys:
        assert await markets.get(key) is None
    assert await raw_redis.exists(LISTS_KEY) == 0
    assert await raw_redis.get("cache:something-else") == "kept", "only the lists are dropped"
    await raw_redis.delete("cache:something-else")


async def test_the_cache_keeps_nothing_but_the_lists(
    markets: MarketCache, raw_redis: Redis
) -> None:
    """One market's detail carries its viewer's side and is never cached: everything
    the cache writes is a field of the one hash."""
    before = set(await raw_redis.keys("cache:*"))

    await markets.set(MarketCache.list_key(SETTLED), '{"items":[]}')

    assert set(await raw_redis.keys("cache:*")) - before == {LISTS_KEY}
    assert await raw_redis.hkeys(LISTS_KEY) == [MarketCache.list_key(SETTLED)]
