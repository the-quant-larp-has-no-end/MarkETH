"""Live updates of one market for open phones (SPEC §8 step 9, §9, Appendix A.4).

After a trade or a lifecycle change commits, the service that made it calls
`publish_quote` or `publish_status` here; the event travels over Redis pub/sub
(`QuoteBus`) to every API process, and each one hands it to the event streams it is
serving. This class is the only place that knows what an event looks like
(`app.schemas.live`).

Events are a convenience. One that is lost costs a few seconds of staleness: the
browser refetches from the API, which reads Postgres.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.integrations.redis.quote_bus import QuoteBus
from app.models.market import Market
from app.schemas.live import QuoteEvent, StatusEvent
from app.services import UowFactory
from app.services.market_view import MarketView


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Nothing happened for `SSE_HEARTBEAT_SECONDS`. The route sends a comment line, so
    proxies keep the stream open."""


type LiveEvent = QuoteEvent | StatusEvent | Heartbeat


class LiveQuoteService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        bus: QuoteBus,
    ) -> None:
        self._uow_factory = uow_factory
        self._heartbeat_seconds = settings.redis.sse_heartbeat_seconds
        self._clock = clock
        self._view = view
        self._bus = bus

    def subscribe(self, market_id: UUID) -> AbstractAsyncContextManager[AsyncIterator[LiveEvent]]:
        """The live events of one market, for `GET /markets/{id}/stream`.

        Implement as an `@asynccontextmanager`. Use as
        `async with live.subscribe(market_id) as events: async for event in events`.

        On entry, in this order:
        1. `QuoteBus.subscribe(market_id)`. `RedisUnavailableError` becomes
           `ServiceUnavailableError` (503): the browser falls back to polling.
        2. Only then read the market from Postgres, so no event between the read and
           the subscription can be missed. Unknown, PROPOSED or REJECTED:
           `NotFoundError` (404), leaving the subscription.

        Both errors are raised before the iterator exists, so the route can still
        answer them as JSON.

        The iterator yields:
        * first, one `QuoteEvent` built from the row just read (the current state);
        * then every event of the queue: a dict whose `type` is `"quote"` or
          `"status"` is validated into its model, anything else is skipped;
        * a `Heartbeat` whenever `SSE_HEARTBEAT_SECONDS` pass without an event
          (`asyncio.timeout` around `queue.get()`).

        It ends, without an error, when `queue.get()` raises `asyncio.QueueShutDown`:
        Redis dropped, the bus stopped, or this subscriber fell too far behind.
        Leaving the block unsubscribes.
        """
        raise NotImplementedError

    async def publish_quote(self, market: Market) -> None:
        """Tell open phones that a trade moved `market`. Call after the commit.

        Publishes `QuoteEvent(type="quote", market_id, version, p_yes, p_no, volume_cr,
        ts=clock.now())` from the committed row, as `event.model_dump(mode="json")`,
        through `QuoteBus.publish`. Best effort: never raises, also with Redis down.
        """
        raise NotImplementedError

    async def publish_status(self, market: Market) -> None:
        """Tell open phones that `market` changed status. Call after the commit.

        Publishes `StatusEvent(type="status", market_id, status=<effective status>,
        trading_open, ts=clock.now())` the same way. Best effort: never raises.
        """
        raise NotImplementedError
