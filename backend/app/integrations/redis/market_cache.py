"""Short-lived cache of the market lists (SPEC §9, A.4).

Entries are the JSON text of a `GET /markets` response, kept for
`MARKET_CACHE_TTL_SECONDS` and dropped right after every committed trade or lifecycle
change. Postgres stays the source of every number: a cached entry is only ever a copy
that is at most one TTL old.

Only the lists are cached, because only they are the same for everybody. One market's
detail carries its viewer's side and is always read from Postgres.
"""

from collections.abc import Collection
from typing import Final

from app.core.config import RedisSettings
from app.core.enums import MarketStatus
from app.integrations.redis.client import RedisClient, RedisUnavailableError

_LISTS_HASH: Final[str] = "cache:markets:lists"
"""Every cached list is a field of this one hash, so a single `DEL` drops them all."""


class MarketCache:
    """Read-through cache for `GET /markets`.

    When Redis is unreachable: `get` reports a miss and `set` and `invalidate_lists`
    do nothing, so the caller simply reads Postgres. No method raises. An invalidation
    that was skipped leaves a stale entry for at most the TTL.
    """

    def __init__(self, redis: RedisClient, settings: RedisSettings) -> None:
        self._redis = redis
        self._ttl_seconds = settings.market_cache_ttl_seconds

    @staticmethod
    def list_key(statuses: Collection[MarketStatus]) -> str:
        """Key of the list of the markets in these effective statuses.

        One set of statuses has one key, in whatever order and however often a request
        named them: `?status=OPEN&status=HALTED` and `?status=HALTED&status=OPEN` share
        an entry.
        """
        return ",".join(sorted({status.value for status in statuses}))

    async def get(self, key: str) -> str | None:
        """The cached JSON text, or None on a miss and whenever Redis is unreachable."""
        try:
            return await self._redis.hash_get(_LISTS_HASH, key)
        except RedisUnavailableError:
            return None

    async def set(self, key: str, json_text: str) -> None:
        """Keep `json_text` under `key`. The lists expire together, one TTL after the
        first of them was stored, so none is ever served for longer than that."""
        try:
            await self._redis.hash_set(_LISTS_HASH, key, json_text, self._ttl_seconds)
        except RedisUnavailableError:
            return

    async def invalidate_lists(self) -> None:
        """Drop every cached list at once: any trade or lifecycle change can alter
        any of them."""
        try:
            await self._redis.delete(_LISTS_HASH)
        except RedisUnavailableError:
            return
