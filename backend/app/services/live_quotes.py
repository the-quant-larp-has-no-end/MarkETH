"""Live updates of one market for open phones (SPEC §8 step 9, §9, Appendix A.4).

After a trade or a lifecycle change commits, the service that made it calls
`publish_quote` or `publish_status` here; the event travels over Redis pub/sub
(`QuoteBus`) to every API process, and each one hands it to the event streams it is
serving. This class is the only place that knows what an event looks like
(`app.schemas.live`).

Events are a convenience. One that is lost costs a few seconds of staleness: the
browser refetches from the API, which reads Postgres.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from pydantic import ValidationError

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.core.units import to_cr
from app.integrations.redis.client import RedisUnavailableError
from app.integrations.redis.quote_bus import QuoteBus
from app.models.market import Market
from app.schemas.live import QuoteEvent, StatusEvent
from app.services import UowFactory
from app.services.market_view import HIDDEN_STATUSES, MarketView

logger = logging.getLogger(__name__)

STREAM_UNAVAILABLE_MESSAGE: Final[str] = "Live updates are unavailable."
"""Message of the `ServiceUnavailableError` a stream is refused with while Redis is
down."""


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

    @asynccontextmanager
    async def subscribe(self, market_id: UUID) -> AsyncIterator[AsyncIterator[LiveEvent]]:
        """The live events of one market, for `GET /markets/{id}/stream`.

        Use as `async with live.subscribe(market_id) as events: async for event in
        events`.

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
          `"status"` is validated into its model; anything else, and one that does
          not fit its model, is skipped;
        * a `Heartbeat` whenever `SSE_HEARTBEAT_SECONDS` pass without an event
          (`asyncio.timeout` around `queue.get()`).

        It ends, without an error, when `queue.get()` raises `asyncio.QueueShutDown`:
        Redis dropped, the bus stopped, or this subscriber fell too far behind.
        Leaving the block unsubscribes and closes the iterator.
        """
        async with AsyncExitStack() as stack:
            try:
                queue = await stack.enter_async_context(self._bus.subscribe(market_id))
            except RedisUnavailableError as exc:
                raise ServiceUnavailableError(STREAM_UNAVAILABLE_MESSAGE) from exc
            async with self._uow_factory() as uow:
                market = await uow.markets.get(market_id)
            if market is None or market.status in HIDDEN_STATUSES:
                raise NotFoundError
            current = self._quote_event(market)
            events = await stack.enter_async_context(aclosing(self._events(current, queue)))
            yield events

    async def publish_quote(self, market: Market) -> None:
        """Tell open phones that a trade moved `market`. Call after the commit.

        Publishes `QuoteEvent(type="quote", market_id, version, p_yes, p_no, volume_cr,
        ts=clock.now())` from the committed row, as `event.model_dump(mode="json")`,
        through `QuoteBus.publish`. Best effort: never raises, also with Redis down.
        The trade it reports is committed, so whatever goes wrong here is logged and
        swallowed (`except Exception`), not handed to a request that succeeded.
        """
        await self._publish(market, self._quote_event)

    async def publish_status(self, market: Market) -> None:
        """Tell open phones that `market` changed status. Call after the commit.

        Publishes `StatusEvent(type="status", market_id, status=<effective status>,
        trading_open, ts=clock.now())` the same way. Best effort: never raises, for
        the same reason.
        """
        await self._publish(market, self._status_event)

    async def _publish(
        self, market: Market, build: Callable[[Market], QuoteEvent | StatusEvent]
    ) -> None:
        """Send the event `build` makes of `market` on the market's channel.

        Never raises. A request that is cancelled stays cancelled: that is no failure
        of this call, and it is not caught.
        """
        market_id: UUID | None = None
        try:
            market_id = market.id
            event = build(market)
            await self._bus.publish(market_id, event.model_dump(mode="json"))
        except Exception:
            logger.exception("Dropped a live event of market %s", market_id)

    def _quote_event(self, market: Market) -> QuoteEvent:
        p_yes, p_no = self._view.probabilities(self._view.p_yes_micro(market))
        return QuoteEvent(
            type="quote",
            market_id=market.id,
            version=market.version,
            p_yes=p_yes,
            p_no=p_no,
            volume_cr=to_cr(market.volume_micro),
            ts=self._clock.now(),
        )

    def _status_event(self, market: Market) -> StatusEvent:
        now = self._clock.now()
        return StatusEvent(
            type="status",
            market_id=market.id,
            status=self._view.effective_status(market, now),
            trading_open=self._view.is_trading_open(market, now),
            ts=now,
        )

    async def _events(
        self, current: QuoteEvent, queue: asyncio.Queue[dict[str, Any]]
    ) -> AsyncGenerator[LiveEvent]:
        """One subscriber's stream: the state at connect, then what `queue` brings."""
        yield current
        while True:
            event: LiveEvent | None
            try:
                async with asyncio.timeout(self._heartbeat_seconds):
                    message = await queue.get()
            except TimeoutError:
                event = Heartbeat()
            except asyncio.QueueShutDown:
                return
            else:
                event = _as_event(message)
            if event is not None:
                yield event


def _as_event(message: Mapping[str, Any]) -> QuoteEvent | StatusEvent | None:
    """What the bus delivered, as the event it is; None for anything that is not one."""
    model: type[QuoteEvent] | type[StatusEvent]
    match message.get("type"):
        case "quote":
            model = QuoteEvent
        case "status":
            model = StatusEvent
        case _:
            return None
    try:
        return model.model_validate(message)
    except ValidationError:
        return None
