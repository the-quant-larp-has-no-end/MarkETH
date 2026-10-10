"""Live updates of one market, as the reader of a stream gets them (SPEC §8 step 9, §9,
Appendix A.4).

The two publishers are called after a commit and must not fail the request that made
it. A stream starts with the market as it stands, carries what was published since,
sends a heartbeat when nothing was, and ends when its subscription does. Redis is the
real one: every event a test receives here went through pub/sub.

The route is driven in two ways. Through the test client where it answers at once: the
two errors a stream can start with. Through ASGI by hand where it streams, because the
test client hands a response over only once it has ended.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from fastapi import FastAPI
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from starlette.datastructures import Headers
from starlette.types import Message, Scope

from app.container import Container
from app.core.enums import MarketStatus, Outcome
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.core.units import MICRO, to_cr
from app.db import database
from app.integrations.redis.client import RedisClient
from app.integrations.redis.quote_bus import QuoteBus
from app.models import Market
from app.schemas.common import AMOUNT_INTEGER_DIGITS
from app.schemas.live import QuoteEvent, StatusEvent
from app.services.live_quotes import Heartbeat, LiveEvent, LiveQuoteService
from tests.support.books import assert_books_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

LIVE_LOGGER: Final[str] = "app.services.live_quotes"

RECEIVE_TIMEOUT_SECONDS: Final[float] = 5.0
"""How long a test waits for what should arrive at once, before it fails instead of
hanging."""

SHARES_MICRO: Final[int] = 100 * MICRO
"""What the trader of these tests buys: enough to move a price visibly. A share costs
less than one credit, so a balance of as many credits always pays for them."""

QUICK_HEARTBEAT_SECONDS: Final[int] = 1
"""`SSE_HEARTBEAT_SECONDS` of the heartbeat test: the shortest the setting can be."""

TIMER_SLACK_SECONDS: Final[float] = 0.05
"""What a timer of the event loop may fire early by."""

BIGINT_MAX: Final[int] = 2**63 - 1

type Sent = tuple[dict[str, Any], QuoteEvent | StatusEvent | None]
"""A message on a market's channel, and the event a stream makes of it: None for a
message that is not an event."""

type Publisher = Callable[[LiveQuoteService, Market], Awaitable[None]]

both_publishers = pytest.mark.parametrize(
    "publish",
    [LiveQuoteService.publish_quote, LiveQuoteService.publish_status],
    ids=["publish_quote", "publish_status"],
)


# --- What a test reads and expects -------------------------------------------------


async def next_event(events: AsyncIterator[LiveEvent]) -> LiveEvent:
    """The next event of a stream; a failure if none comes."""
    return await asyncio.wait_for(anext(events), timeout=RECEIVE_TIMEOUT_SECONDS)


async def rest_of(events: AsyncIterator[LiveEvent]) -> list[LiveEvent]:
    """Everything a stream still hands out before it ends by itself."""
    async with asyncio.timeout(RECEIVE_TIMEOUT_SECONDS):
        return [event async for event in events]


def quote_of(container: Container, market: Market) -> QuoteEvent:
    """The quote event of `market` as it stands now, in the numbers `MarketView` reports."""
    view = container.market_view
    p_yes, p_no = view.probabilities(view.p_yes_micro(market))
    return QuoteEvent(
        type="quote",
        market_id=market.id,
        version=market.version,
        p_yes=p_yes,
        p_no=p_no,
        volume_cr=to_cr(market.volume_micro),
        ts=container.clock.now(),
    )


def status_of(container: Container, market: Market) -> StatusEvent:
    """The status event of `market` as it stands now, in the status `MarketView` reports."""
    view = container.market_view
    now = container.clock.now()
    return StatusEvent(
        type="status",
        market_id=market.id,
        status=view.effective_status(market, now),
        trading_open=view.is_trading_open(market, now),
        ts=now,
    )


def wire(event: QuoteEvent | StatusEvent) -> dict[str, Any]:
    """An event as it travels: the JSON a publisher sends and the route writes."""
    return event.model_dump(mode="json")


def errors_logged(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == LIVE_LOGGER and record.levelno >= logging.ERROR
    ]


# --- Stand-ins and fixtures --------------------------------------------------------


class WatchedBus(QuoteBus):
    """A `QuoteBus` that shows what its subscriber does with it: how many subscriptions
    were opened and how many are open, and, through `once_subscribed`, the moment one
    has just opened and its subscriber has not carried on yet."""

    def __init__(self, redis: RedisClient) -> None:
        super().__init__(redis)
        self.opened = 0
        self.open = 0
        self.once_subscribed: Callable[[], Awaitable[None]] | None = None

    @asynccontextmanager
    async def subscribe(self, market_id: UUID) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        async with super().subscribe(market_id) as queue:
            self.opened += 1
            self.open += 1
            try:
                if self.once_subscribed is not None:
                    await self.once_subscribed()
                yield queue
            finally:
                self.open -= 1


class BrokenBus(QuoteBus):
    """A `QuoteBus` whose `publish` fails, which the real one promises never to do."""

    async def publish(self, market_id: UUID, event: dict[str, Any]) -> None:
        raise RuntimeError("the bus failed in a way nobody planned for")


def live_service(
    container: Container, *, bus: QuoteBus | None = None, heartbeat_seconds: int | None = None
) -> LiveQuoteService:
    """A `LiveQuoteService` wired like the container's, on another bus or with another
    `SSE_HEARTBEAT_SECONDS`."""
    chosen = container.settings
    if heartbeat_seconds is not None:
        redis = chosen.redis.model_copy(update={"sse_heartbeat_seconds": heartbeat_seconds})
        chosen = chosen.model_copy(update={"redis": redis})
    return LiveQuoteService(
        container.uow_factory,
        chosen,
        container.clock,
        container.market_view,
        bus or container.quote_bus,
    )


@pytest.fixture
async def watched_bus(container: Container) -> AsyncIterator[WatchedBus]:
    bus = WatchedBus(container.redis)
    await bus.start()
    assert bus.is_connected
    yield bus
    await bus.stop()


@pytest.fixture
async def market(factory: Factory) -> Market:
    """An OPEN market nobody has traded."""
    return await factory.market()


class StreamReader:
    """One request to the stream route, made through ASGI by hand. What the server
    sends can be read while it is still sending, and `leave` is the reader going away,
    told to the application the way uvicorn tells it."""

    def __init__(self, app: FastAPI, market_id: UUID) -> None:
        path = f"/api/markets/{market_id}/stream"
        scope: Scope = {
            "type": "http",
            # What uvicorn says of itself: a reader that left is reported through
            # `receive`, which is how Starlette then ends the response.
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        self._sent: asyncio.Queue[Message] = asyncio.Queue()
        self._gone = asyncio.Event()
        self._serving = asyncio.create_task(app(scope, self._receive, self._sent.put))

    async def _receive(self) -> Message:
        await self._gone.wait()
        return {"type": "http.disconnect"}

    async def next_message(self) -> Message:
        """The next ASGI message the application sent."""
        return await asyncio.wait_for(self._sent.get(), timeout=RECEIVE_TIMEOUT_SECONDS)

    async def next_frame(self) -> tuple[str, dict[str, Any]]:
        """The next event of the stream: its `event:` name and the JSON of its `data:`."""
        message = await self.next_message()
        assert message["type"] == "http.response.body", message
        name, data = message["body"].decode().strip("\n").split("\n")
        return name.removeprefix("event: "), json.loads(data.removeprefix("data: "))

    async def finished(self) -> None:
        """Wait until the application has finished with the request. An error it ran
        into is raised here."""
        await asyncio.wait_for(self._serving, timeout=RECEIVE_TIMEOUT_SECONDS)

    async def leave(self) -> None:
        """Go away, and wait until the application has finished with the request."""
        self._gone.set()
        await self.finished()


@asynccontextmanager
async def reading_stream(app: FastAPI, market_id: UUID) -> AsyncIterator[StreamReader]:
    reader = StreamReader(app, market_id)
    try:
        yield reader
    finally:
        await reader.leave()


# --- What a stream carries ---------------------------------------------------------


async def test_a_stream_starts_with_the_market_as_it_stands(
    container: Container, factory: Factory, market: Market, clock: FrozenClock
) -> None:
    trader = await factory.user(balance_micro=SHARES_MICRO)
    await factory.position(trader, market, Outcome.YES, shares_micro=SHARES_MICRO)
    clock.advance(timedelta(minutes=3))

    async with container.live_quotes.subscribe(market.id) as events:
        first = await next_event(events)

    current = quote_of(container, market)
    assert first == current
    assert current.version == 1
    assert current.p_yes > current.p_no, "the trade moved the price, so 50% would be wrong"
    await assert_books_balance(container.uow_factory)


async def test_a_published_quote_reaches_a_subscriber_as_a_quote_event(
    container: Container, factory: Factory, market: Market, clock: FrozenClock
) -> None:
    live = container.live_quotes
    trader = await factory.user(balance_micro=SHARES_MICRO)
    untraded = quote_of(container, market)

    async with live.subscribe(market.id) as events:
        assert await next_event(events) == untraded
        await factory.position(trader, market, Outcome.NO, shares_micro=SHARES_MICRO)
        clock.advance(timedelta(seconds=30))
        await live.publish_quote(market)
        published = await next_event(events)

    traded = quote_of(container, market)
    assert isinstance(published, QuoteEvent)
    assert published == traded
    assert (traded.version, traded.ts) == (untraded.version + 1, clock.now())
    assert traded.p_no > untraded.p_no
    assert traded.volume_cr > untraded.volume_cr
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("stored", "trading_open"),
    [
        (MarketStatus.OPEN, True),
        (MarketStatus.HALTED, False),
        (MarketStatus.CLOSED, False),
        (MarketStatus.RESOLVED, False),
        (MarketStatus.VOID, False),
    ],
    ids=["open", "halted", "closed", "resolved", "void"],
)
async def test_a_published_status_reaches_a_subscriber_as_a_status_event(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    stored: MarketStatus,
    trading_open: bool,
) -> None:
    live = container.live_quotes
    changed = await factory.market(status=stored)

    async with live.subscribe(changed.id) as events:
        await next_event(events)
        clock.advance(timedelta(seconds=30))
        await live.publish_status(changed)
        published = await next_event(events)

    assert isinstance(published, StatusEvent)
    assert published == status_of(container, changed)
    assert (published.status, published.trading_open) == (stored, trading_open)
    assert published.ts == clock.now()


async def test_a_status_event_reports_an_open_market_past_its_deadline_as_closed(
    container: Container, market: Market, clock: FrozenClock
) -> None:
    """No job flips the column at `close_at` (A.4): the event says what the API says."""
    live = container.live_quotes

    async with live.subscribe(market.id) as events:
        await next_event(events)
        clock.set(market.close_at)
        await live.publish_status(market)
        published = await next_event(events)

    assert market.status is MarketStatus.OPEN
    assert published == status_of(container, market)
    assert isinstance(published, StatusEvent)
    assert (published.status, published.trading_open) == (MarketStatus.CLOSED, False)


async def test_a_stream_carries_the_events_of_its_own_market_only(
    container: Container, factory: Factory, market: Market
) -> None:
    live = container.live_quotes
    other = await factory.market(status=MarketStatus.HALTED)

    async with live.subscribe(market.id) as events:
        await next_event(events)
        await live.publish_status(other)
        await live.publish_quote(other)
        await live.publish_status(market)
        published = await next_event(events)

    assert published == status_of(container, market)


# What a message on a market's channel can be. Every field of an event is required, so
# a message with fewer fields than the smaller event has is never one by accident.
EVENT_FIELDS: Final[list[str]] = sorted({*QuoteEvent.model_fields, *StatusEvent.model_fields})
FEWEST_FIELDS: Final[int] = min(len(QuoteEvent.model_fields), len(StatusEvent.model_fields))
A_DECADE: Final[timedelta] = timedelta(days=3650)
AFTER_EVERY_DRAWN_TIME: Final[datetime] = TEST_NOW + 2 * A_DECADE

event_times = st.datetimes(
    min_value=(TEST_NOW - A_DECADE).replace(tzinfo=None),
    max_value=(TEST_NOW + A_DECADE).replace(tzinfo=None),
    timezones=st.just(UTC),
)
probabilities = st.floats(min_value=0, max_value=1)
quote_events = st.builds(
    QuoteEvent,
    type=st.just("quote"),
    market_id=st.uuids(),
    version=st.integers(min_value=0, max_value=BIGINT_MAX),
    p_yes=probabilities,
    p_no=probabilities,
    volume_cr=st.integers(min_value=0, max_value=10**AMOUNT_INTEGER_DIGITS * MICRO - 1).map(to_cr),
    ts=event_times,
)
status_events = st.builds(
    StatusEvent,
    type=st.just("status"),
    market_id=st.uuids(),
    status=st.sampled_from(MarketStatus),
    trading_open=st.booleans(),
    ts=event_times,
)
events_published = quote_events | status_events

json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=10), inner, max_size=3)
    ),
    max_leaves=10,
)


@st.composite
def events_short_of_a_field(draw: st.DrawFn) -> dict[str, Any]:
    message = wire(draw(events_published))
    del message[draw(st.sampled_from(sorted(message)))]
    return message


not_events = st.one_of(
    st.dictionaries(
        st.sampled_from(EVENT_FIELDS) | st.text(max_size=10),
        json_values,
        max_size=FEWEST_FIELDS - 1,
    ),
    events_short_of_a_field(),
    quote_events.map(lambda event: wire(event) | {"type": "status"}),
    status_events.map(lambda event: wire(event) | {"type": "quote"}),
)


def an_event(event: QuoteEvent | StatusEvent) -> Sent:
    return wire(event), event


def no_event(message: dict[str, Any]) -> Sent:
    return message, None


messages: st.SearchStrategy[Sent] = st.one_of(
    events_published.map(an_event), not_events.map(no_event)
)


# The container and the market are made once and serve every example: an example
# opens a stream of its own and leaves nothing behind.
@settings(
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(sent=st.lists(messages, max_size=6))
@example(sent=[no_event({}), no_event({"type": None}), no_event({"type": ["quote"]})])
@example(
    sent=[no_event({"type": "quote"}), no_event({"type": "status"}), no_event({"type": "trade"})]
)
async def test_a_stream_hands_out_the_events_among_what_was_published_and_skips_the_rest(
    container: Container, market: Market, sent: list[Sent]
) -> None:
    """Whatever arrives on the channel: each event comes out as its model, in the order
    it was published, and a message that is not an event neither shows nor ends the
    stream."""
    bus = container.quote_bus
    last = StatusEvent(
        type="status",
        market_id=market.id,
        status=MarketStatus.CLOSED,
        trading_open=False,
        ts=AFTER_EVERY_DRAWN_TIME,
    )

    async with container.live_quotes.subscribe(market.id) as events:
        await next_event(events)
        for message, _ in sent:
            await bus.publish(market.id, message)
        await bus.publish(market.id, wire(last))
        received = []
        while (event := await next_event(events)) != last:
            received.append(event)

    assert received == [event for _, event in sent if event is not None]


async def test_a_quiet_stream_sends_a_heartbeat_every_sse_heartbeat_seconds(
    container: Container, market: Market
) -> None:
    live = live_service(container, heartbeat_seconds=QUICK_HEARTBEAT_SECONDS)
    loop = asyncio.get_running_loop()

    async with live.subscribe(market.id) as events:
        await next_event(events)
        quiet_since = loop.time()
        assert await next_event(events) == Heartbeat()
        assert loop.time() - quiet_since >= QUICK_HEARTBEAT_SECONDS - TIMER_SLACK_SECONDS

        # A heartbeat ends nothing: what is published next arrives, after one more
        # heartbeat should Redis take that long, and so does the heartbeat after it.
        await live.publish_status(market)
        published = await next_event(events)
        while published == Heartbeat():
            published = await next_event(events)
        assert published == status_of(container, market)
        quiet_since = loop.time()
        assert await next_event(events) == Heartbeat()
        assert loop.time() - quiet_since >= QUICK_HEARTBEAT_SECONDS - TIMER_SLACK_SECONDS


async def test_a_stream_ends_without_an_error_when_its_subscription_is_ended(
    container: Container, market: Market
) -> None:
    """What a lost Redis connection does to every open stream: the bus shuts its queue.
    What was already delivered is still handed out."""
    live = container.live_quotes
    bus = container.quote_bus
    elsewhere = uuid7()

    async with live.subscribe(market.id) as events, bus.subscribe(elsewhere) as marker:
        await next_event(events)
        await live.publish_status(market)
        # One connection delivers in order: once this arrives, the status has arrived.
        await bus.publish(elsewhere, {"delivered": True})
        await asyncio.wait_for(marker.get(), timeout=RECEIVE_TIMEOUT_SECONDS)

        await bus.stop()
        remaining = await rest_of(events)

    assert remaining == [status_of(container, market)]


# --- Opening and leaving a stream --------------------------------------------------


async def test_the_market_is_read_after_the_subscription_has_opened(
    container: Container, factory: Factory, market: Market, watched_bus: WatchedBus
) -> None:
    """A trade that commits while a stream is being opened is in the state the stream
    starts with or in an event after it, never in neither: the subscription comes
    first. Here one commits right after the subscription opened."""
    live = live_service(container, bus=watched_bus)
    trader = await factory.user(balance_micro=SHARES_MICRO)

    async def trade() -> None:
        await factory.position(trader, market, Outcome.YES, shares_micro=SHARES_MICRO)

    watched_bus.once_subscribed = trade

    async with live.subscribe(market.id) as events:
        first = await next_event(events)

    assert market.version == 1
    assert first == quote_of(container, market)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "hidden",
    [None, MarketStatus.PROPOSED, MarketStatus.REJECTED],
    ids=["unknown", "proposed", "rejected"],
)
async def test_a_market_that_is_unknown_proposed_or_rejected_has_no_stream(
    container: Container,
    factory: Factory,
    watched_bus: WatchedBus,
    hidden: MarketStatus | None,
) -> None:
    live = live_service(container, bus=watched_bus)
    market_id = uuid7() if hidden is None else (await factory.market(status=hidden)).id

    with pytest.raises(NotFoundError):
        async with live.subscribe(market_id):
            pytest.fail("a stream opened for a market nobody may watch")

    assert watched_bus.opened == 1
    assert watched_bus.open == 0, "the refused stream gave its subscription back"


async def test_leaving_a_stream_gives_its_subscription_back_and_ends_it(
    container: Container, market: Market, watched_bus: WatchedBus
) -> None:
    live = live_service(container, bus=watched_bus)

    async with live.subscribe(market.id) as events:
        await next_event(events)
        assert watched_bus.open == 1

    assert watched_bus.open == 0
    with pytest.raises(StopAsyncIteration):
        await next_event(events)


# --- When something fails ----------------------------------------------------------


@pytest.mark.redis_down
async def test_without_redis_a_stream_is_refused_as_unavailable(
    container: Container, market: Market
) -> None:
    with pytest.raises(ServiceUnavailableError):
        async with container.live_quotes.subscribe(market.id):
            pytest.fail("a stream opened without Redis")


@pytest.mark.redis_down
async def test_without_redis_an_unknown_market_is_unavailable_too(container: Container) -> None:
    """The subscription is asked for before the market is: nobody is told a market does
    not exist by a service that could not have streamed it anyway."""
    with pytest.raises(ServiceUnavailableError):
        async with container.live_quotes.subscribe(uuid7()):
            pytest.fail("a stream opened without Redis")


@pytest.mark.postgres_down
async def test_without_postgres_a_stream_fails_as_an_outage_and_keeps_no_subscription(
    container: Container, watched_bus: WatchedBus
) -> None:
    """A lost database is nobody's to translate (SPEC §9): the error leaves the service
    as it is, the API answers it with 503, and the subscription that was opened before
    the market was asked for is given back."""
    live = live_service(container, bus=watched_bus)

    with pytest.raises(database.ERROR_FAMILIES) as raised:
        async with live.subscribe(uuid7()):
            pytest.fail("a stream opened without Postgres")

    assert database.is_unavailable(raised.value)
    assert (watched_bus.opened, watched_bus.open) == (1, 0)


@pytest.mark.redis_down
@both_publishers
async def test_without_redis_a_publisher_returns_normally(
    container: Container, market: Market, caplog: pytest.LogCaptureFixture, publish: Publisher
) -> None:
    with caplog.at_level(logging.ERROR, logger=LIVE_LOGGER):
        await publish(container.live_quotes, market)

    assert errors_logged(caplog) == [], "an outage the bus absorbs is not an error here"


@both_publishers
async def test_a_publisher_logs_and_swallows_a_failure_of_the_bus(
    container: Container, market: Market, caplog: pytest.LogCaptureFixture, publish: Publisher
) -> None:
    """The trade or the admin action it reports is committed: nothing here may turn
    that request into a failure."""
    live = live_service(container, bus=BrokenBus(container.redis))

    with caplog.at_level(logging.ERROR, logger=LIVE_LOGGER):
        await publish(live, market)

    (record,) = errors_logged(caplog)
    assert str(market.id) in record.getMessage()
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)


@both_publishers
async def test_a_publisher_logs_and_swallows_a_row_it_cannot_make_an_event_of(
    container: Container, caplog: pytest.LogCaptureFixture, publish: Publisher
) -> None:
    with caplog.at_level(logging.ERROR, logger=LIVE_LOGGER):
        await publish(container.live_quotes, Market())

    (record,) = errors_logged(caplog)
    assert record.exc_info is not None


# --- The route on the real service -------------------------------------------------


async def test_the_stream_of_an_unknown_market_is_answered_as_json_before_it_starts(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.user())

    response = await client.get(f"/api/markets/{uuid7()}/stream")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["code"] == "NOT_FOUND"


@pytest.mark.redis_down
async def test_without_redis_the_stream_is_answered_503_as_json_before_it_starts(
    client: httpx.AsyncClient, factory: Factory, market: Market, as_user: ActAs
) -> None:
    """503 is what sends the browser to polling (SPEC §9)."""
    as_user(await factory.user())

    response = await client.get(f"/api/markets/{market.id}/stream")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert (body["code"], body["details"]) == ("SERVICE_UNAVAILABLE", {})


async def test_the_stream_route_sends_events_as_they_are_published_until_the_reader_leaves(
    app: FastAPI,
    container: Container,
    factory: Factory,
    market: Market,
    as_user: ActAs,
    watched_bus: WatchedBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subscription is held by a dependency of the route: open for as long as the
    response streams, and given back when the reader has gone."""
    live = live_service(container, bus=watched_bus)
    monkeypatch.setattr(container, "live_quotes", live)
    as_user(await factory.user())

    async with reading_stream(app, market.id) as reader:
        start = await reader.next_message()
        assert (start["type"], start["status"]) == ("http.response.start", 200)
        assert Headers(raw=start["headers"])["content-type"].startswith("text/event-stream")
        assert await reader.next_frame() == ("quote", wire(quote_of(container, market)))
        assert watched_bus.open == 1

        await live.publish_status(market)
        assert await reader.next_frame() == ("status", wire(status_of(container, market)))
        assert watched_bus.open == 1

    assert watched_bus.open == 0


async def test_the_stream_route_ends_the_response_when_the_subscription_is_ended(
    app: FastAPI,
    container: Container,
    factory: Factory,
    market: Market,
    as_user: ActAs,
    watched_bus: WatchedBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that ends is what makes the browser open another stream or poll."""
    monkeypatch.setattr(container, "live_quotes", live_service(container, bus=watched_bus))
    as_user(await factory.user())

    async with reading_stream(app, market.id) as reader:
        await reader.next_message()
        await reader.next_frame()

        await watched_bus.stop()
        end = await reader.next_message()
        assert (end["type"], end["body"], end["more_body"]) == ("http.response.body", b"", False)
        await reader.finished()

    assert watched_bus.open == 0
