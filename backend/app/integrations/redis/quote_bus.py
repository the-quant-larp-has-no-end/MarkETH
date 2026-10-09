"""Live quotes over Redis pub/sub (SPEC §8 step 9, §9, A.4).

After a trade commits, the API publishes the new quote on channel `market:{id}`;
lifecycle changes publish a status event on the same channel. Each API process keeps one
Redis connection subscribed to `market:*` and fans the events out to the server-sent
event streams it is serving.

Events are a convenience for open phones. A missed one costs a few seconds of
staleness, never correctness: the browser refetches from the API, which reads Postgres.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any, Final
from uuid import UUID

from app.integrations.redis.client import RedisClient, RedisUnavailableError

logger = logging.getLogger(__name__)

_CHANNEL_PATTERN: Final[str] = "market:*"

_RECONNECT_DELAY_MIN_SECONDS: Final[float] = 0.5
_RECONNECT_DELAY_MAX_SECONDS: Final[float] = 10.0

_SUBSCRIBER_QUEUE_SIZE: Final[int] = 64
"""Events a subscriber may fall behind before its subscription is ended.

A stream whose phone stopped reading would otherwise grow without bound. Ending it
makes the browser reconnect or poll, and either way it refetches the current state.
"""


class QuoteBus:
    """Publishes market events and fans them out to this process's subscribers.

    When Redis is unreachable: `publish` drops the event and never raises. `subscribe`
    raises `RedisUnavailableError`, so the stream endpoint can answer 503. When the
    connection drops while subscriptions are open, each of their queues is shut down:
    `queue.get()` hands out what is already queued and then raises
    `asyncio.QueueShutDown`. The stream ends there and the browser falls back to
    polling (SPEC §9). The listener reconnects by itself with backoff; subscriptions
    are not restored, the client opens a new stream.

    A connection that dies without being closed is only noticed through TCP keepalive,
    after about 45 seconds with redis-py's defaults.
    """

    def __init__(self, redis: RedisClient) -> None:
        self._redis = redis
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._listener: asyncio.Task[None] | None = None
        self._connected = False

    @staticmethod
    def channel(market_id: UUID) -> str:
        return f"market:{market_id}"

    @property
    def is_connected(self) -> bool:
        """Whether the listener holds a confirmed subscription right now."""
        return self._connected

    async def publish(self, market_id: UUID, event: dict[str, Any]) -> None:
        """Best effort: send `event` (JSON-serialisable) to every process's subscribers."""
        try:
            message = json.dumps(event, separators=(",", ":"))
        except TypeError, ValueError:
            logger.exception("Dropped a market event that is not JSON")
            return
        with suppress(RedisUnavailableError):
            await self._redis.publish(self.channel(market_id), message)

    async def start(self) -> None:
        """Launch the listener and wait for its first connection attempt.

        That attempt takes at most one Redis timeout. If it fails, the bus starts
        disconnected and keeps retrying in the background.
        """
        if self._listener is not None:
            return
        first_attempt_done = asyncio.Event()
        self._listener = asyncio.create_task(
            self._listen(first_attempt_done), name="quote-bus-listener"
        )
        await first_attempt_done.wait()

    async def stop(self) -> None:
        """End the listener and every open subscription."""
        listener, self._listener = self._listener, None
        if listener is None:
            return
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)

    @asynccontextmanager
    async def subscribe(self, market_id: UUID) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """A queue of the events published for one market while the block is open.

        Raises `RedisUnavailableError` when the listener is not connected. The queue is
        shut down when no more events will come: the connection dropped, the bus was
        stopped, or this subscriber fell too far behind. Events are shared between
        subscribers; read them, do not change them.
        """
        if not self._connected:
            raise RedisUnavailableError("live quotes are not connected to Redis")
        channel = self.channel(market_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.setdefault(channel, set()).add(queue)
        try:
            yield queue
        finally:
            self._forget(channel, queue)

    async def _listen(self, first_attempt_done: asyncio.Event) -> None:
        delay = _RECONNECT_DELAY_MIN_SECONDS
        outage_logged = False
        while True:
            try:
                async with self._redis.listen(_CHANNEL_PATTERN) as messages:
                    self._connected = True
                    first_attempt_done.set()
                    delay = _RECONNECT_DELAY_MIN_SECONDS
                    outage_logged = False
                    logger.info("Live quotes are connected")
                    async for channel, message in messages:
                        self._deliver(channel, message)
            except RedisUnavailableError as exc:
                if not outage_logged:
                    logger.warning("Live quotes are off until Redis is back: %s", exc)
                    outage_logged = True
            finally:
                self._connected = False
                self._end_subscriptions()
                first_attempt_done.set()
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_DELAY_MAX_SECONDS)

    def _deliver(self, channel: str, message: str) -> None:
        queues = self._subscribers.get(channel)
        if not queues:
            return
        try:
            event = json.loads(message)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        for queue in tuple(queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                queue.shutdown()
                self._forget(channel, queue)

    def _end_subscriptions(self) -> None:
        for queues in self._subscribers.values():
            for queue in queues:
                queue.shutdown()
        self._subscribers.clear()

    def _forget(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._subscribers.get(channel)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            del self._subscribers[channel]
