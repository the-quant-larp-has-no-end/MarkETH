"""From a commit to an open stream, on the real Postgres and Redis (SPEC §9).

A service announces inside its transaction. The unit of work hands the events to the
`Announcer` once the commit has returned; the announcer drops the cached market lists
where a price or a status is among them, and the hub hands each event to the streams it
is for. These tests stand where a service stands: they write and announce in a unit of
work that was given the announcer, and read what a stream of the hub brings.

An event exists exactly if its transaction committed. A hand-over that fails is logged
and swallowed: the transaction stands, and so does the answer of its request.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode
from uuid import uuid7

import httpx
import pytest
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.types import Message, Scope

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import MarketStatus, MarketTier, Outcome
from app.core.identity import AuthenticatedUser
from app.core.live import Topic, TopicKind
from app.core.units import MICRO, format_cr
from app.db.database import Database
from app.integrations.redis.client import RedisClient
from app.integrations.redis.market_cache import MarketCache
from app.models import Market, User
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.announcer import Announcer
from app.services.live_events import LiveEvents, me_changed
from app.services.live_hub import (
    STREAM_UNAVAILABLE_MESSAGE,
    LiveFrame,
    LiveHub,
    too_many_topics_message,
)
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

MARKETS: Final[Topic] = Topic(TopicKind.MARKETS)
PROGRAMME: Final[str] = CATALOGUE[0].programmes[0]
OTHER_PROGRAMME: Final[str] = CATALOGUE[0].programmes[1]
OPEN_LISTS: Final[str] = MarketCache.list_key([MarketStatus.OPEN])


def of(market: Market) -> Topic:
    return Topic(TopicKind.MARKET, market.id)


class Reading:
    """What one stream brought, read in a task of its own as a route reads it."""

    def __init__(self, at_each: Callable[[], Awaitable[object]] | None) -> None:
        self._at_each = at_each
        self.bodies: list[dict[str, Any]] = []
        self.found: list[object] = []
        self.ended = False

    async def take(self, frames: AsyncIterator[LiveFrame]) -> None:
        async for frame in frames:
            if self._at_each is not None:
                self.found.append(await self._at_each())
            self.bodies.append(json.loads(frame.data))
        self.ended = True

    @property
    def names(self) -> list[str]:
        return [body["type"] for body in self.bodies]


async def settled(reading: Reading | None = None, *, names: int = 0) -> None:
    """Let the readers take what their streams were offered. With `reading`, wait until
    it holds `names` events: a reader that asks a store at each event needs real time."""
    for _ in range(5):
        await asyncio.sleep(0)
    if reading is not None:
        for _ in range(1000):
            if len(reading.bodies) >= names:
                return
            await asyncio.sleep(0.005)
        pytest.fail(f"the stream brought {len(reading.bodies)} events, not {names}")


@asynccontextmanager
async def reading(
    hub: LiveHub,
    who: AuthenticatedUser,
    topics: Collection[Topic] = (),
    *,
    tab: str = "tab-0001",
    at_each: Callable[[], Awaitable[object]] | None = None,
) -> AsyncIterator[Reading]:
    async with hub.open(who, tab, topics) as frames:
        taken = Reading(at_each)
        reader = asyncio.create_task(taken.take(frames))
        try:
            await settled()
            yield taken
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


@pytest.fixture
def hub(settings: Settings, clock: FrozenClock) -> LiveHub:
    return LiveHub(settings.live, clock)


@pytest.fixture
def events(container: Container) -> LiveEvents:
    return LiveEvents(container.market_view)


@pytest.fixture
def announcing(database: Database, hub: LiveHub, container: Container) -> UowFactory:
    """Units of work whose events go to `hub`, past the cache of the market lists."""
    announcer = Announcer(hub, container.market_cache)
    return lambda: UnitOfWork(database.session_factory, announcer)


async def private_market(factory: Factory, container: Container, proposer: User) -> Market:
    """An OPEN market for the members of `PROGRAMME`, as an approval leaves it."""
    now = container.clock.now()
    pricing = container.settings.pricing
    b_micro = pricing.tier_b_micro(MarketTier.DEFAULT)
    market = Market(
        question="Will the exam of Analysis I be held in the HPH?",
        yes_def="It is held there.",
        no_def="It is held somewhere else.",
        resolution_criteria="The exam plan decides.",
        resolution_source="ethz.ch/exams",
        status=MarketStatus.OPEN,
        tier=MarketTier.DEFAULT,
        b_micro=b_micro,
        budget_micro=lmsr.max_loss_micro(b_micro, precision=pricing.decimal_precision),
        close_at=now + timedelta(days=7),
        resolve_by=now + timedelta(days=14),
        created_by=proposer.id,
        approved_by=proposer.id,
        audience_programme=PROGRAMME,
        created_at=now,
        opened_at=now,
    )
    async with container.uow_factory() as uow:
        await uow.markets.add(market)
        await uow.accounts.create_market_account(market.id)
    return market


# --- An event exists exactly if its transaction committed -------------------------------


async def test_what_a_transaction_announces_reaches_a_stream_once_it_has_committed(
    container: Container,
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    announcing: UowFactory,
    clock: FrozenClock,
) -> None:
    trader = await factory.user(balance_micro=100 * MICRO)
    market = await factory.market()
    await factory.position(trader, market, Outcome.YES, shares_micro=40 * MICRO)
    view = container.market_view

    async with (
        reading(hub, factory.identity(trader), [MARKETS]) as on_the_lists,
        reading(hub, factory.identity(trader), [of(market)], tab="tab-0002") as on_the_market,
        reading(hub, factory.identity(await factory.user()), [of(await factory.market())]) as other,
    ):
        async with announcing() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            now = clock.now()
            uow.announce(me_changed([trader.id], now))
            uow.announce(events.market_quote(row, now))
            await settled()
            # Nothing is told while the transaction is open.
            assert on_the_lists.names == on_the_market.names == other.names == ["ready"]
        await settled()

        async with container.uow_factory() as uow:
            committed = await uow.markets.get(market.id)
        assert committed is not None
        p_yes, p_no = view.probabilities(view.p_yes_micro(committed))
        quote = {
            "type": "market.quote",
            "market_id": str(market.id),
            "version": committed.version,
            "p_yes": p_yes,
            "p_no": p_no,
            "volume_cr": format_cr(committed.volume_micro),
            "ts": "2026-10-09T12:00:00Z",
        }
        own = {"type": "me.changed", "ts": "2026-10-09T12:00:00Z"}
        assert on_the_lists.bodies[1:] == [own, quote]
        assert on_the_market.bodies[1:] == [own, quote]
        assert (committed.version, p_yes > 0.5) == (1, True)
        # Another member on another market hears neither.
        assert other.names == ["ready"]

    await assert_books_balance(container.uow_factory)


async def test_a_transaction_that_rolls_back_tells_no_stream_anything(
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    announcing: UowFactory,
    clock: FrozenClock,
) -> None:
    member = await factory.user()
    market = await factory.market()

    async def give_up() -> None:
        async with announcing() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            uow.announce(events.market_status(row, clock.now()))
            uow.announce(me_changed([member.id], clock.now(), ends_streams_of=member.id))
            raise RuntimeError("the service gave up")

    async with reading(hub, factory.identity(member), [MARKETS, of(market)]) as heard:
        with pytest.raises(RuntimeError, match="gave up"):
            await give_up()
        await settled()

        assert heard.names == ["ready"]
        # Nor does it end the streams its event would have ended.
        assert (heard.ended, hub.open_streams) == (False, 1)


async def test_an_event_that_ends_its_users_streams_does_so_after_the_commit_and_after_it_went_out(
    factory: Factory, hub: LiveHub, announcing: UowFactory, clock: FrozenClock
) -> None:
    suspended, other = await factory.user(), await factory.user()

    async with (
        reading(hub, factory.identity(suspended), [MARKETS]) as theirs,
        reading(hub, factory.identity(other), [MARKETS]) as others,
    ):
        async with announcing() as uow:
            uow.announce(me_changed([suspended.id], clock.now(), ends_streams_of=suspended.id))
            await settled()
            assert not theirs.ended
        await settled()

        assert (theirs.names, theirs.ended) == (["ready", "me.changed"], True)
        assert (others.names, others.ended) == (["ready"], False)
        assert hub.open_streams == 1


# --- The cached market lists ------------------------------------------------------------


async def test_the_cached_market_lists_are_gone_when_a_market_event_reaches_a_stream(
    container: Container,
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    announcing: UowFactory,
    clock: FrozenClock,
) -> None:
    """A tab that reads a list because of an event never reads the copy from before the
    event: at the moment a stream is handed a price or a status, the cache is empty."""
    cache = container.market_cache
    member = await factory.user()
    market = await factory.market()
    await cache.set(OPEN_LISTS, '{"items":[]}')

    async def cached() -> str | None:
        return await cache.get(OPEN_LISTS)

    async with reading(hub, factory.identity(member), [MARKETS], at_each=cached) as heard:
        await settled(heard, names=1)
        async with announcing() as uow:
            uow.announce(me_changed([member.id], clock.now()))
        await settled(heard, names=2)
        # What changes no list leaves the cached lists alone.
        assert heard.found == ['{"items":[]}'] * 2

        async with announcing() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            row.status = MarketStatus.HALTED
            uow.announce(events.market_status(row, clock.now()))
        await settled(heard, names=3)

        assert heard.names == ["ready", "me.changed", "market.status"]
        assert heard.bodies[2]["status"] == "HALTED"
        assert heard.found[2] is None

        await cache.set(OPEN_LISTS, '{"items":[]}')
        async with announcing() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            uow.announce(events.market_quote(row, clock.now()))
        await settled(heard, names=4)

        assert heard.names[3] == "market.quote"
        assert heard.found[3] is None


async def test_with_redis_down_a_commit_still_reaches_the_streams(
    database: Database,
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    unreachable_redis: RedisClient,
    settings: Settings,
    clock: FrozenClock,
) -> None:
    """No event passes Redis. The one Redis call of the hand-over fails and is not
    waited for beyond its timeout."""
    announcer = Announcer(hub, MarketCache(unreachable_redis, settings.redis))
    member = await factory.user()
    market = await factory.market()

    async with reading(hub, factory.identity(member), [of(market)]) as heard:
        async with UnitOfWork(database.session_factory, announcer) as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            uow.announce(events.market_status(row, clock.now()))
            uow.announce(me_changed([member.id], clock.now()))
        await settled()

        assert heard.names == ["ready", "market.status", "me.changed"]


# --- A hand-over that fails -------------------------------------------------------------


class BrokenCache(MarketCache):
    """A cache of the market lists that fails in a way no Redis outage explains."""

    def __init__(self) -> None:
        pass

    async def invalidate_lists(self) -> None:
        raise RuntimeError("the cache is broken")


async def test_a_hand_over_that_fails_is_logged_and_swallowed_and_the_transaction_stands(
    database: Database,
    container: Container,
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    announcing: UowFactory,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken = Announcer(hub, BrokenCache())
    member = await factory.user()
    market = await factory.market()

    async with reading(hub, factory.identity(member), [MARKETS]) as heard:
        with caplog.at_level(logging.ERROR, logger="app.repositories.unit_of_work"):
            async with UnitOfWork(database.session_factory, broken) as uow:
                row = await uow.markets.get_for_update(market.id)
                assert row is not None
                row.status = MarketStatus.HALTED
                uow.announce(events.market_status(row, clock.now()))
        await settled()

        # The block ended without an error, and what it wrote is there.
        async with container.uow_factory() as uow:
            committed = await uow.markets.get(market.id)
        assert committed is not None
        assert committed.status is MarketStatus.HALTED
        (record,) = caplog.records
        assert record.getMessage() == (
            "1 live event(s) of a committed transaction were not handed over"
        )
        assert record.exc_info is not None
        assert "the cache is broken" in str(record.exc_info[1])
        assert heard.names == ["ready"]

        # The hub goes on: the next commit is heard.
        async with announcing() as uow:
            uow.announce(me_changed([member.id], clock.now()))
        await settled()
        assert heard.names == ["ready", "me.changed"]


# --- Who is inside a market -------------------------------------------------------------


async def test_the_events_of_a_private_market_reach_who_is_inside_it_and_nobody_else(
    container: Container,
    factory: Factory,
    hub: LiveHub,
    events: LiveEvents,
    announcing: UowFactory,
    clock: FrozenClock,
) -> None:
    """Naming a topic grants nothing: every stream here names the market and the lists.
    A holder outside the audience keeps the page of the market and is pushed nothing."""
    proposer = await factory.user(degree_programme=OTHER_PROGRAMME)
    market = await private_market(factory, container, proposer)
    holder = await factory.user(degree_programme=OTHER_PROGRAMME, balance_micro=100 * MICRO)
    await factory.position(holder, market, Outcome.NO, shares_micro=10 * MICRO)
    callers = {
        "member": await factory.user(degree_programme=PROGRAMME),
        "admin": await factory.admin(),
        "proposer": proposer,
        "outsider": await factory.user(degree_programme=OTHER_PROGRAMME),
        "holder": holder,
    }

    async with AsyncExitStack() as stack:
        heard = {
            name: await stack.enter_async_context(
                reading(hub, factory.identity(who), [MARKETS, of(market)])
            )
            for name, who in callers.items()
        }
        async with announcing() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            uow.announce(events.market_quote(row, clock.now()))
            uow.announce(events.market_status(row, clock.now()))
        await settled()

        assert {name: taken.names[1:] for name, taken in heard.items()} == {
            "member": ["market.quote", "market.status"],
            "admin": ["market.quote", "market.status"],
            "proposer": ["market.quote", "market.status"],
            "outsider": [],
            "holder": [],
        }
        # Every one of them opened the same way.
        assert all(taken.names[0] == "ready" for taken in heard.values())
        assert not any(taken.ended for taken in heard.values())

    await assert_books_balance(container.uow_factory)


# --- What a stream costs ----------------------------------------------------------------


async def test_open_streams_hold_no_connection_of_the_pool(
    database: Database, factory: Factory, hub: LiveHub
) -> None:
    member = factory.identity(await factory.user())

    async with AsyncExitStack() as stack:
        for number in range(50):
            await stack.enter_async_context(hub.open(member, f"tab-{number:04d}", [MARKETS]))

        assert hub.open_streams == 50
        assert database.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    assert hub.open_streams == 0


# --- Through the route, on the application as it is wired -------------------------------

TAB: Final[str] = "tab-0123"
ANOTHER_TAB: Final[str] = "tab-4567"
STREAM: Final[str] = "/api/stream"

PATIENCE_SECONDS: Final[float] = 5.0
"""How long a test waits for what the application sends next."""


class Tab:
    """One request to `GET /api/stream`, made through ASGI by hand. What the application
    sends can be read while it is still sending, and `leave` is the browser going away,
    told to the application the way uvicorn tells it."""

    def __init__(self, app: FastAPI, tab: str, topics: Collection[Topic]) -> None:
        query = urlencode([("tab", tab), *(("topic", str(topic)) for topic in topics)])
        scope: Scope = {
            "type": "http",
            # What uvicorn says of itself: a reader that left is reported through
            # `receive`, which is how Starlette then ends the answer.
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": STREAM,
            "raw_path": STREAM.encode(),
            "query_string": query.encode(),
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

    async def _next(self) -> Message:
        return await asyncio.wait_for(self._sent.get(), timeout=PATIENCE_SECONDS)

    async def starts(self) -> Headers:
        """The start of the answer: 200, and its headers."""
        message = await self._next()
        assert (message["type"], message["status"]) == ("http.response.start", 200), message
        return Headers(raw=message["headers"])

    async def written(self) -> bytes:
        """The next piece of the answer, as it travels."""
        message = await self._next()
        assert message["type"] == "http.response.body", message
        assert message.get("more_body"), "the answer ended"
        body: bytes = message["body"]
        return body

    async def hears(self) -> tuple[str, dict[str, Any]]:
        """The next event: its `event:` name and the JSON of its `data:`."""
        piece = (await self.written()).decode()
        assert piece.endswith("\n\n"), "an event is written whole"
        lines = dict(line.split(": ", 1) for line in piece.removesuffix("\n\n").split("\n"))
        return lines["event"], json.loads(lines["data"])

    async def opens(self) -> None:
        """The answer starts and says `ready`."""
        await self.starts()
        name, _ = await self.hears()
        assert name == "ready"

    async def ends(self) -> None:
        """The application ends the answer by itself, without an error."""
        message = await self._next()
        assert message["type"] == "http.response.body", message
        assert (message["body"], message.get("more_body", False)) == (b"", False)
        await asyncio.wait_for(self._serving, timeout=PATIENCE_SECONDS)

    async def leave(self) -> None:
        """Go away, and wait until the application has finished with the request. An
        error it ran into is raised here."""
        self._gone.set()
        await asyncio.wait_for(self._serving, timeout=PATIENCE_SECONDS)


@asynccontextmanager
async def tab_on(
    app: FastAPI, topics: Collection[Topic] = (), *, tab: str = TAB
) -> AsyncIterator[Tab]:
    opened = Tab(app, tab, topics)
    try:
        yield opened
    finally:
        await opened.leave()


def refused(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """A refusal is the whole answer, as JSON in the error shape: no stream started."""
    assert response.status_code == status_code, response.text
    assert response.headers["content-type"] == "application/json"
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    return body


async def test_the_route_answers_an_event_stream_that_says_ready_first_and_then_beats(
    app: FastAPI,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    clock: FrozenClock,
    settings: Settings,
) -> None:
    as_user(await factory.user())
    live = settings.live

    async with tab_on(app) as tab:
        headers = await tab.starts()
        assert headers["content-type"] == "text/event-stream; charset=utf-8"
        assert headers["cache-control"] == "no-cache"
        assert headers["x-accel-buffering"] == "no"

        # `ready` is written as the hub encoded it: its `retry:` line, name and body.
        retry, name, data, nothing, end = (await tab.written()).split(b"\n")
        assert (name, nothing, end) == (b"event: ready", b"", b"")
        assert live.retry_ms <= int(retry.removeprefix(b"retry: ")) <= 2 * live.retry_ms
        ready = json.loads(data.removeprefix(b"data: "))
        assert (ready["type"], datetime.fromisoformat(ready["ts"])) == ("ready", clock.now())
        assert container.live.open_streams == 1

        clock.advance(timedelta(seconds=live.heartbeat_seconds))
        container.live.tick()
        heard, beat = await tab.hears()
        assert (heard, beat["type"]) == ("beat", "beat")
        assert datetime.fromisoformat(beat["ts"]) == clock.now()

    # The tab went away: its place is free again.
    assert container.live.open_streams == 0


async def test_what_a_transaction_announces_reaches_the_tab_that_follows_its_topic_and_no_other(
    app: FastAPI, container: Container, factory: Factory, as_user: ActAs, clock: FrozenClock
) -> None:
    """The container's own units of work carry the announcer: a commit of the application
    is heard on the application's streams."""
    as_user(await factory.user())
    market, another = await factory.market(), await factory.market()

    async with (
        tab_on(app, [of(market)]) as on_the_market,
        tab_on(app, [of(another)], tab=ANOTHER_TAB) as elsewhere,
    ):
        await on_the_market.opens()
        await elsewhere.opens()

        async with container.uow_factory() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            uow.announce(container.live_events.market_quote(row, clock.now()))

        name, quote = await on_the_market.hears()
        assert (name, quote["type"]) == ("market.quote", "market.quote")
        assert (quote["market_id"], quote["version"]) == (str(market.id), row.version)

        # A beat is the next thing either tab hears: the quote went to one of them.
        container.live.tick()
        assert (await elsewhere.hears())[0] == "beat"
        assert (await on_the_market.hears())[0] == "beat"

    assert container.live.open_streams == 0


async def test_a_stream_that_opens_under_a_tab_ends_the_one_that_tab_held(
    app: FastAPI, container: Container, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.user())

    async with tab_on(app) as left_behind:
        await left_behind.opens()

        async with tab_on(app) as its_successor:
            await its_successor.opens()
            await left_behind.ends()
            assert container.live.open_streams == 1

            # Another tab of the same person is another stream.
            async with tab_on(app, tab=ANOTHER_TAB) as beside_it:
                await beside_it.opens()
                assert container.live.open_streams == 2

    assert container.live.open_streams == 0


async def test_beyond_live_max_streams_a_stream_is_refused_as_json_and_takes_no_place(
    client: httpx.AsyncClient,
    app: FastAPI,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    settings: Settings,
) -> None:
    member = await factory.user()
    as_user(member)
    limit = settings.live.max_streams

    async with AsyncExitStack() as held:
        for number in range(limit):
            stream = container.live.open(factory.identity(member), f"held-{number:04d}", ())
            await held.enter_async_context(stream)

        body = refused(await client.get(STREAM, params={"tab": TAB}), 503, "SERVICE_UNAVAILABLE")
        assert body["message"] == STREAM_UNAVAILABLE_MESSAGE
        assert container.live.open_streams == limit

    # With the places given back, the same request is a stream.
    async with tab_on(app) as tab:
        await tab.opens()


async def test_more_topics_than_a_stream_may_follow_are_refused_as_json_before_a_stream_opens(
    client: httpx.AsyncClient,
    app: FastAPI,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    settings: Settings,
) -> None:
    as_user(await factory.user())
    limit = settings.live.topics_per_stream
    topics = [Topic(TopicKind.MARKET, uuid7()) for _ in range(limit + 1)]

    def asking_for(named: Collection[Topic]) -> httpx.QueryParams:
        return httpx.QueryParams([("tab", TAB), *(("topic", str(topic)) for topic in named)])

    body = refused(await client.get(STREAM, params=asking_for(topics)), 422, "VALIDATION_ERROR")
    assert (body["message"], body["details"]["field"]) == (too_many_topics_message(limit), "topic")

    # What the query model refuses is JSON too: no tab, and a topic that is none.
    refused(await client.get(STREAM), 422, "VALIDATION_ERROR")
    refused(await client.get(STREAM, params={"tab": TAB, "topic": "me"}), 422, "VALIDATION_ERROR")
    assert container.live.open_streams == 0

    # As many topics as the setting allows are a stream.
    async with tab_on(app, topics[:limit]) as tab:
        await tab.opens()
