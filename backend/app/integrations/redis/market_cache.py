"""Short-lived cache of the market list and market summaries (SPEC §9, A.4).

Entries are the JSON text of a response, kept for `MARKET_CACHE_TTL_SECONDS` and dropped
right after every committed trade or lifecycle change. Postgres stays the source of
every number: a cached entry is only ever a copy that is at most one TTL old.
"""

from typing import Final
from uuid import UUID

from app.core.config import RedisSettings
from app.integrations.redis.client import RedisClient, RedisUnavailableError

_LIST_KEY_PREFIX: Final[str] = "cache:markets:list:"

_LISTS_HASH: Final[str] = "cache:markets:lists"
"""Every cached list is a field of this one hash, so a single `DEL` drops them all."""


class MarketCache:
    """Read-through cache for `GET /markets` and the public summary of one market.

    When Redis is unreachable: `get` reports a miss and `set` and `invalidate_market`
    do nothing, so the caller simply reads Postgres. No method raises. An invalidation
    that was skipped leaves a stale entry for at most the TTL.
    """

    def __init__(self, redis: RedisClient, settings: RedisSettings) -> None:
        self._redis = redis
        self._ttl_seconds = settings.market_cache_ttl_seconds

    @staticmethod
    def market_key(market_id: UUID) -> str:
        """Key of one market's summary."""
        return f"cache:market:{market_id}"

    @staticmethod
    def list_key(status_filter: str | None) -> str:
        """Key of the market list for one `?status=` filter; None is the unfiltered list."""
        return f"{_LIST_KEY_PREFIX}{status_filter or 'all'}"

    async def get(self, key: str) -> str | None:
        """The cached JSON text, or None on a miss and whenever Redis is unreachable."""
        try:
            if key.startswith(_LIST_KEY_PREFIX):
                return await self._redis.hash_get(_LISTS_HASH, key)
            return await self._redis.get(key)
        except RedisUnavailableError:
            return None

    async def set(self, key: str, json_text: str) -> None:
        """Keep `json_text` under `key` for the configured TTL."""
        try:
            if key.startswith(_LIST_KEY_PREFIX):
                await self._redis.hash_set(_LISTS_HASH, key, json_text, self._ttl_seconds)
            else:
                await self._redis.set(key, json_text, self._ttl_seconds)
        except RedisUnavailableError:
            return

    async def invalidate_market(self, market_id: UUID) -> None:
        """Drop that market's summary and every cached list, atomically."""
        try:
            await self._redis.delete(self.market_key(market_id), _LISTS_HASH)
        except RedisUnavailableError:
            return
