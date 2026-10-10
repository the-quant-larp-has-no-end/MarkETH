"""The Redis connection shared by every adapter in this package (SPEC §9).

Redis makes MarkETH faster and enforces its rate limits. It never decides a balance, a
price or whether a trade is allowed, so every caller must be able to carry on without
it. `RedisClient` reduces that to one contract: an operation either answers within
`REDIS_TIMEOUT_MS` or raises `RedisUnavailableError`, and the adapter built on top
decides what that means: a cache miss, a fallback limiter or a 503.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from typing import Final

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import RedisSettings

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS: Final[float] = 1.0
"""How long every operation fails fast after Redis could not be reached.

Short, because code redemption fails closed for as long as it lasts.
"""

# OSError includes the builtin TimeoutError that the per-operation deadline raises.
_UNREACHABLE: Final = (RedisConnectionError, RedisTimeoutError, OSError)


class RedisUnavailableError(Exception):
    """Redis did not do what was asked: unreachable, too slow, cooling down, or it refused."""


class RedisClient:
    """The few Redis operations MarkETH needs, each bounded by `REDIS_TIMEOUT_MS`.

    When Redis is unreachable: the operation that finds out raises
    `RedisUnavailableError` after at most one timeout. For the next `COOLDOWN_SECONDS`
    every operation raises it at once without touching the network; after that a single
    operation is let through as a probe while the others keep failing fast. A dead Redis
    therefore costs one timeout per cool-down instead of one per call. A command that
    Redis answers with an error (out of memory, still loading) raises the same exception
    but starts no cool-down, because that answer came quickly.

    Requires Redis 7 or newer (`EXPIRE ... NX`).
    """

    def __init__(self, settings: RedisSettings) -> None:
        self._timeout_seconds = settings.timeout_seconds
        self._redis = Redis(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            decode_responses=True,
            socket_timeout=settings.timeout_seconds,
            socket_connect_timeout=settings.timeout_seconds,
            # The pub/sub connection waits without a timeout; keepalive is what ends
            # that wait when the peer vanishes without closing the connection.
            socket_keepalive=True,
            # redis-py retries a failed command with backoff by default, which would
            # multiply the timeout. The cool-down below is the only retry policy.
            retry=Retry(NoBackoff(), 0),
        )
        self._down = False
        self._fail_fast_until = 0.0

    async def ping(self) -> bool:
        """Whether Redis answers right now. Never raises; used by `/health`."""
        try:
            await self._run(lambda: self._redis.ping())
        except RedisUnavailableError:
            return False
        return True

    async def delete(self, *keys: str) -> None:
        """`DEL`: all keys in one command, so they disappear together."""
        await self._run(lambda: self._redis.delete(*keys))

    async def hash_get(self, key: str, field: str) -> str | None:
        """`HGET`: the field's value, or None if the hash or the field does not exist."""
        value = await self._run(lambda: self._redis.hget(key, field))
        return value if isinstance(value, str) else None

    async def hash_set(self, key: str, field: str, value: str, ttl_seconds: int) -> None:
        """`HSET` one field. The whole hash expires `ttl_seconds` after its first write.

        Later writes do not push the expiry back, so no field outlives the TTL.
        """

        async def operation() -> None:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.hset(key, field, value)
                pipeline.expire(key, ttl_seconds, nx=True)
                await pipeline.execute()

        await self._run(operation)

    async def count_in_window(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Count one hit in a fixed window: `INCR` and `EXPIRE` as one transaction.

        The first hit starts the window; later hits leave its expiry alone. Returns the
        number of hits including this one and the milliseconds until the window ends.
        """

        async def operation() -> tuple[int, int]:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window_seconds, nx=True)
                pipeline.pttl(key)
                count, _, remaining_ms = await pipeline.execute()
            return int(count), int(remaining_ms)

        return await self._run(operation)

    async def publish(self, channel: str, message: str) -> None:
        """`PUBLISH` a message to whoever is subscribed right now."""
        await self._run(lambda: self._redis.publish(channel, message))

    @asynccontextmanager
    async def listen(self, pattern: str) -> AsyncIterator[AsyncIterator[tuple[str, str]]]:
        """Subscribe one dedicated connection to a channel pattern (`PSUBSCRIBE`).

        Entering returns once Redis has confirmed the subscription and yields an
        iterator of `(channel, message)` pairs. The iterator raises
        `RedisUnavailableError` when the connection drops; leaving the block closes it.

        This connection lives outside the cool-down: it neither starts one nor waits
        for one, because a lost subscription says nothing about the next command.
        """
        async with self._redis.pubsub() as pubsub:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    await pubsub.psubscribe(pattern)
                    await pubsub.get_message(timeout=None)
            except (RedisError, OSError) as exc:
                raise RedisUnavailableError(
                    f"cannot subscribe to {pattern}: {_reason(exc)}"
                ) from exc
            async with aclosing(_messages(pubsub)) as messages:
                yield messages

    async def close(self) -> None:
        """Close every pooled connection. Call once, on shutdown."""
        await self._redis.aclose()

    async def _run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        now = time.monotonic()
        if now < self._fail_fast_until:
            raise RedisUnavailableError("Redis is unavailable; cooling down after a failure")
        if self._down:
            # This call is the probe. Everyone else keeps failing fast until it returns.
            self._fail_fast_until = now + COOLDOWN_SECONDS
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await operation()
        except _UNREACHABLE as exc:
            self._mark_down(exc)
            raise RedisUnavailableError(f"Redis is unreachable: {_reason(exc)}") from exc
        except RedisError as exc:
            # An error reply is still a reply: Redis is reachable.
            self._mark_up()
            logger.warning("Redis refused a command: %s", exc)
            raise RedisUnavailableError(f"Redis refused a command: {exc}") from exc
        self._mark_up()
        return result

    def _mark_down(self, cause: BaseException) -> None:
        if not self._down:
            logger.warning("Redis is unreachable, degrading until it answers: %s", _reason(cause))
        self._down = True
        self._fail_fast_until = time.monotonic() + COOLDOWN_SECONDS

    def _mark_up(self) -> None:
        if self._down:
            logger.info("Redis answers again")
        self._down = False
        self._fail_fast_until = 0.0


async def _messages(pubsub: PubSub) -> AsyncGenerator[tuple[str, str]]:
    while True:
        try:
            frame = await pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
        except (RedisError, OSError) as exc:
            raise RedisUnavailableError(f"the pub/sub connection dropped: {_reason(exc)}") from exc
        if frame is not None and frame["type"] == "pmessage":
            yield frame["channel"], frame["data"]


def _reason(error: BaseException) -> str:
    """Name and message. redis-py's own repr drops the message; a bare timeout has none."""
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
