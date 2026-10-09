"""Read-through cache in front of the Postgres `sessions` table (SPEC §3, A.3).

The key is `sessions.id`, the SHA-256 of the cookie's token, so Redis never holds a
value that would open a session. The payload is whatever the session service needs to
answer a request without a query; it must be JSON.
"""

import json
import logging
from typing import Any

from app.integrations.redis.client import RedisClient, RedisUnavailableError

logger = logging.getLogger(__name__)


class SessionCache:
    """Cached sessions, keyed by session id.

    When Redis is unreachable: `get` reports a miss and `set` and `delete` do nothing,
    so authentication reads Postgres directly (SPEC A.3). No method raises. A `delete`
    that could not reach Redis leaves the entry until its TTL runs out, which makes the
    TTL the longest a signed-out session can stay usable.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """The cached payload, or None on a miss and whenever Redis is unreachable."""
        try:
            text = await self._redis.get(_key(session_id))
        except RedisUnavailableError:
            return None
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def set(self, session_id: str, payload: dict[str, Any], ttl_seconds: int) -> None:
        """Cache `payload` for `ttl_seconds`. A TTL that has already run out caches nothing."""
        if ttl_seconds <= 0:
            return
        try:
            text = json.dumps(payload, separators=(",", ":"))
        except TypeError, ValueError:
            logger.exception("Session payload is not JSON; the session stays uncached")
            return
        try:
            await self._redis.set(_key(session_id), text, ttl_seconds)
        except RedisUnavailableError:
            return

    async def delete(self, session_id: str) -> None:
        """Forget a session, for example at sign-out."""
        try:
            await self._redis.delete(_key(session_id))
        except RedisUnavailableError:
            return


def _key(session_id: str) -> str:
    return f"session:{session_id}"
