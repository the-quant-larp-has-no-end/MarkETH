"""The price chart of a market: `HistoryService.history` (SPEC §9, §10, §12, A.4).

SPEC §12, "Failures": with ScyllaDB stopped trades succeed and the chart catches up
after its return. The default suite runs with ScyllaDB as a closed port, so every chart
here that is read through `container.history` is the "stopped" half: complete, and
built from the `trades` table.

A ScyllaDB that answers is `MemoryHistory` (tests/support/history.py) in the default
suite and the real node in the tests marked `scylla`, at the end of the file.

The trades are the factory's, each with the outbox row a trade queues, so nothing here
waits for the trading service. A point the tests put into the store by hand carries
`STORE_P_YES_MICRO`, a price no trade leaves: the chart then shows which of its points
the store served and which Postgres.

A chart is bounded (`HISTORY_READ_LIMIT`, `HISTORY_MAX_POINTS`). Where a test needs more
points than a factory can trade, the record is written in one statement (`record`) and
the store is filled by hand; where it needs the bounds within reach of a few trades, the
service is built on smaller ones (`history_on`).
"""

import asyncio
import itertools
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, assume, example, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from app.container import Container
from app.core.config import ScyllaSettings
from app.core.enums import (
    STORED_MARKET_STATUSES,
    MarketStatus,
    Outcome,
    UserRole,
    UserStatus,
)
from app.core.errors import NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO
from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from app.models import Market, User
from app.relay.outbox_relay import OutboxRelay
from app.repositories.trading import TradeRepository
from app.schemas.admin.markets import (
    CloseMarketRequest,
    HaltMarketRequest,
    ResolveMarketRequest,
    VoidMarketRequest,
)
from app.schemas.live import HistoryPoint, HistoryResponse, HistorySource
from app.services.history import HistoryService, thinned
from app.services.market_view import EVEN_ODDS_MICRO, HIDDEN_STATUSES, MarketView
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.history import (
    MemoryHistory,
    RecordedPoint,
    buy_and_queue,
    opening_point,
    queue_point,
    ready,
    recorded_chart,
    to_milliseconds,
    unpublished_ids,
)
from tests.support.network import SwitchableProxy
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

READ_LIMIT: Final[int] = load_base_settings().scylla.history_read_limit
"""HISTORY_READ_LIMIT: the most points either store is asked for."""

MAX_POINTS: Final[int] = load_base_settings().scylla.history_max_points
"""HISTORY_MAX_POINTS: the most points an answer holds."""

TRADES: Final[int] = 6
"""Trades of `traded_market`, so its chart has the versions 0 to 6."""

STORE_P_YES_MICRO: Final[int] = 987_654
"""The YES price of a point a test puts into the store by hand. No trade here leaves it."""

BUYER_BALANCE_MICRO: Final[int] = 1_000 * MICRO

SCYLLA_RECOVERY_SECONDS: Final[float] = 30.0
"""The driver retries a dead node every 5 seconds; this leaves it several attempts."""

BETWEEN_TRADES: Final[timedelta] = timedelta(seconds=6)
"""What lies between two points of a record written by `record`: the pace one person
can keep under MAX_ORDERS_PER_MINUTE, which makes 14,400 points a day."""

HOUR: Final[timedelta] = timedelta(hours=1)
DAY: Final[timedelta] = timedelta(days=1)

VISIBLE_STATUSES: Final[list[MarketStatus]] = sorted(set(STORED_MARKET_STATUSES) - HIDDEN_STATUSES)


@pytest.fixture
async def store() -> MemoryHistory:
    """A ScyllaDB that answers: its schema is in place and it holds nothing."""
    memory = MemoryHistory()
    await memory.ensure_schema()
    return memory


def history_on(
    container: Container,
    store: QuoteHistoryStore,
    *,
    read_limit: int | None = None,
    max_points: int | None = None,
) -> HistoryService:
    """The application's history service, reading `store` instead of the suite's
    unreachable ScyllaDB. `read_limit` and `max_points` stand in for
    HISTORY_READ_LIMIT and HISTORY_MAX_POINTS where a test names them."""
    scylla = container.settings.scylla
    bounds = scylla.model_copy(
        update={
            "history_read_limit": read_limit or scylla.history_read_limit,
            "history_max_points": max_points or scylla.history_max_points,
        }
    )
    assert 2 <= bounds.history_max_points <= bounds.history_read_limit, "as startup demands"
    settings = container.settings.model_copy(update={"scylla": bounds})
    return HistoryService(
        container.uow_factory, settings, container.clock, container.market_view, store
    )


@pytest.fixture
def chart(container: Container, store: MemoryHistory) -> HistoryService:
    return history_on(container, store)


@pytest.fixture
def relay(container: Container, store: MemoryHistory) -> OutboxRelay:
    return OutboxRelay(container.uow_factory, store, container.settings.scylla, container.clock)


@pytest.fixture
def catch_ups(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """How each read of a market's recorded prices was asked for, in order. The history
    service reads them for one reason only: to catch a chart up from Postgres."""
    seen: list[dict[str, Any]] = []
    read = TradeRepository.list_price_points

    async def recorded(
        self: TradeRepository, market_id: UUID, **how: Any
    ) -> Sequence[tuple[int, datetime, int]]:
        seen.append(how)
        return await read(self, market_id, **how)

    monkeypatch.setattr(TradeRepository, "list_price_points", recorded)
    return seen


def caught_up_after(version: int, limit: int = READ_LIMIT) -> dict[str, Any]:
    """How the one catch-up of a chart is asked for when ScyllaDB got as far as
    `version`: the trades after it, and no more of them than the read limit."""
    return {"after_version": version, "limit": limit}


async def traded_market(
    container: Container, factory: Factory, clock: FrozenClock, *, trades: int = TRADES
) -> Market:
    """An open market whose opening row is queued, with `trades` buys a minute apart,
    on both outcomes, each with its outbox row."""
    market = await factory.market()
    await queue_point(container.uow_factory, opening_point(market))
    buyer = await factory.user(balance_micro=BUYER_BALANCE_MICRO)
    for number in range(1, trades + 1):
        clock.advance(timedelta(minutes=1))
        outcome = Outcome.NO if number % 3 == 0 else Outcome.YES
        await buy_and_queue(
            factory,
            container.uow_factory,
            buyer,
            market,
            outcome,
            shares_micro=(number + 1) * 10 * MICRO,
        )
    return market


def shown(view: MarketView, recorded: Iterable[RecordedPoint]) -> list[HistoryPoint]:
    """The chart the API shows for recorded points: each price rounded for display."""
    return [
        HistoryPoint(t=at, p_yes=view.probabilities(p_yes_micro)[0], version=version)
        for version, at, p_yes_micro in recorded
    ]


async def hold(
    store: QuoteHistoryStore,
    market: Market,
    recorded: Sequence[RecordedPoint],
    versions: Iterable[int],
) -> None:
    """Put the points of `versions` into `store`, each at its recorded instant and at
    `STORE_P_YES_MICRO`."""
    instants = {version: at for version, at, _ in recorded}
    for version in versions:
        await store.write(
            QuotePoint(
                market_id=market.id,
                ts=instants[version],
                version=version,
                p_yes_micro=STORE_P_YES_MICRO,
                q_yes_micro=0,
                q_no_micro=0,
                trade_id=None,
            )
        )


def from_store_up_to(
    view: MarketView, recorded: Sequence[RecordedPoint], reached: int
) -> list[HistoryPoint]:
    """The chart when the store served the versions 0 to `reached` and Postgres the rest."""
    return shown(
        view,
        [
            (version, at, STORE_P_YES_MICRO if version <= reached else p_yes_micro)
            for version, at, p_yes_micro in recorded
        ],
    )


# --- ScyllaDB stopped: the suite's closed port ----------------------------------------


async def test_with_scylla_stopped_the_chart_is_complete_from_postgres_and_says_so(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """SPEC §12, "Failures": one point per version, in order, each at the instant and
    the price of its trade."""
    market = await traded_market(container, factory, clock)

    response = await container.history.history(market.id)

    recorded = await recorded_chart(container.uow_factory, market)
    assert response.source is HistorySource.POSTGRES
    assert response.points == shown(container.market_view, recorded)
    assert [point.version for point in response.points] == list(range(TRADES + 1))
    instants = [point.t for point in response.points]
    assert instants == sorted(set(instants)), "a minute apart, oldest first"
    # The chart ends at the price the market's card shows.
    card = container.market_view.summary(market, clock.now(), None)
    assert response.points[-1].p_yes == card.p_yes
    moves = [after.p_yes - before.p_yes for before, after in itertools.pairwise(response.points)]
    assert all(moves), "every trade moved the price that is shown"
    assert {move > 0 for move in moves} == {True, False}, "buys of YES and of NO"
    # Nothing was delivered: the relay still owes the opening and every trade.
    assert len(await unpublished_ids(container.uow_factory)) == TRADES + 1
    await assert_books_balance(container.uow_factory)


async def test_the_series_starts_with_the_opening_point(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Version 0: even odds at the instant the market opened, whether or not anybody
    traded since (A.4)."""
    market = await factory.market()
    opened_at = opening_point(market).ts
    opening = HistoryPoint(
        t=opened_at, p_yes=container.market_view.probabilities(EVEN_ODDS_MICRO)[0], version=0
    )
    clock.advance(timedelta(hours=3))

    untraded = await container.history.history(market.id)

    assert untraded.points == [opening]
    assert (opening.p_yes, opening.t) == (0.5, clock.now() - timedelta(hours=3))

    alice = await factory.user(balance_micro=BUYER_BALANCE_MICRO)
    await factory.position(alice, market, Outcome.YES, shares_micro=10 * MICRO)
    traded = await container.history.history(market.id)

    assert traded.points[0] == opening
    assert [point.version for point in traded.points] == [0, 1]
    assert traded.points[1].t == clock.now()
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("status", sorted(HIDDEN_STATUSES))
async def test_a_proposal_and_a_rejected_market_have_no_chart(
    container: Container, factory: Factory, status: MarketStatus
) -> None:
    market = await factory.market(status=status)

    with pytest.raises(NotFoundError):
        await container.history.history(market.id)


async def test_an_expired_proposal_has_no_chart(container: Container, factory: Factory) -> None:
    """It was never opened: there is nothing to draw, and nobody is told that it exists."""
    expired = await factory.market(status=MarketStatus.PROPOSED, close_in=-timedelta(days=1))

    with pytest.raises(NotFoundError):
        await container.history.history(expired.id)


async def test_an_unknown_market_has_no_chart(container: Container) -> None:
    with pytest.raises(NotFoundError):
        await container.history.history(uuid7())


@pytest.mark.parametrize("status", VISIBLE_STATUSES)
async def test_a_market_in_every_other_status_has_a_chart(
    container: Container, factory: Factory, status: MarketStatus
) -> None:
    """Halted, closed and settled markets keep the chart of the time they traded."""
    market = await factory.market(status=status)

    response = await container.history.history(market.id)

    assert [point.version for point in response.points] == [0]
    assert response.source is HistorySource.POSTGRES


async def test_the_route_answers_a_member_with_the_chart(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    market = await traded_market(container, factory, clock, trades=2)
    as_user(await factory.user())

    response = await client.get(f"/api/markets/{market.id}/history")

    assert response.status_code == 200
    body = response.json()
    assert HistoryResponse.model_validate(body) == await container.history.history(market.id)
    assert body["source"] == "postgres"
    first, *_, last = body["points"]
    assert set(first) == {"t", "p_yes", "version"}
    assert (first["p_yes"], first["version"], last["version"]) == (0.5, 0, 2)
    await assert_books_balance(container.uow_factory)


async def test_the_route_answers_404_for_a_market_without_a_chart(
    client: httpx.AsyncClient, as_user: ActAs, factory: Factory
) -> None:
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    as_user(await factory.user())

    for market_id in (proposal.id, uuid7()):
        response = await client.get(f"/api/markets/{market_id}/history")

        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


async def test_a_market_that_was_opened_without_an_instant_is_a_broken_row_not_a_chart(
    container: Container, factory: Factory, owner_engine: AsyncEngine
) -> None:
    """Approval dates every market it opens. The column allows NULL for the proposals."""
    market = await factory.market()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE markets SET opened_at = NULL WHERE id = :id"), {"id": market.id}
        )

    with pytest.raises(RuntimeError, match="has no opened_at"):
        await container.history.history(market.id)


@pytest.mark.postgres_down
async def test_with_postgres_unreachable_there_is_no_chart_whatever_scylla_holds(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    store: MemoryHistory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC §9: with Postgres down everything answers 503. ScyllaDB holds the points,
    and whether the market may be shown at all is Postgres' to say."""
    market_id = uuid7()
    await store.write(
        QuotePoint(
            market_id=market_id,
            ts=container.clock.now(),
            version=0,
            p_yes_micro=EVEN_ODDS_MICRO,
            q_yes_micro=0,
            q_no_micro=0,
            trade_id=None,
        )
    )
    monkeypatch.setattr(container, "history", history_on(container, store))
    as_user(
        AuthenticatedUser(
            id=uuid7(),
            email="member@ethz.ch",
            name="Member",
            username="member",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
    )

    response = await client.get(f"/api/markets/{market_id}/history")

    assert (response.status_code, response.json()["code"]) == (503, "SERVICE_UNAVAILABLE")
    assert store.reads == [], "ScyllaDB was not even asked"


# --- ScyllaDB answers: the stand-in store ---------------------------------------------


async def test_an_up_to_date_scylla_serves_the_whole_chart_and_no_trade_is_read(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    chart: HistoryService,
    relay: OutboxRelay,
    catch_ups: list[dict[str, Any]],
) -> None:
    market = await traded_market(container, factory, clock)
    assert await relay.drain_once() == TRADES + 1

    response = await chart.history(market.id)

    recorded = await recorded_chart(container.uow_factory, market)
    assert response.source is HistorySource.SCYLLA
    assert response.points == shown(container.market_view, recorded)
    assert catch_ups == []
    await assert_books_balance(container.uow_factory)


async def test_a_chart_that_scylla_holds_in_full_shows_scyllas_points_not_those_of_postgres(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
) -> None:
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    await hold(store, market, recorded, range(TRADES + 1))

    response = await chart.history(market.id)

    assert response.points == from_store_up_to(container.market_view, recorded, TRADES)
    assert {point.p_yes for point in response.points} == {
        container.market_view.probabilities(STORE_P_YES_MICRO)[0]
    }
    await assert_books_balance(container.uow_factory)


async def test_a_scylla_that_is_behind_is_completed_from_postgres(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    catch_ups: list[dict[str, Any]],
) -> None:
    """The relay delivered the opening and three trades; the other three are in the
    outbox still. The chart shows all six."""
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    await hold(store, market, recorded, range(4))

    response = await chart.history(market.id)

    assert response.points == from_store_up_to(container.market_view, recorded, 3)
    assert [point.version for point in response.points] == list(range(TRADES + 1))
    assert catch_ups == [caught_up_after(3)]
    # ScyllaDB returned points, and that is all `source` says.
    assert response.source is HistorySource.SCYLLA
    await assert_books_balance(container.uow_factory)


async def test_a_version_missing_below_scyllas_latest_point_is_noticed_and_filled(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    catch_ups: list[dict[str, Any]],
) -> None:
    """ScyllaDB holds the latest version and lacks version 3. Its latest point alone
    would pass for a complete chart. From the hole on Postgres is the record, also for
    the versions ScyllaDB does hold."""
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    await hold(store, market, recorded, [0, 1, 2, 4, 5, 6])

    response = await chart.history(market.id)

    assert response.points == from_store_up_to(container.market_view, recorded, 2)
    assert [point.version for point in response.points] == list(range(TRADES + 1))
    assert catch_ups == [caught_up_after(2)]
    await assert_books_balance(container.uow_factory)


async def test_without_scyllas_opening_point_the_chart_starts_at_the_one_of_postgres(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    catch_ups: list[dict[str, Any]],
) -> None:
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    await hold(store, market, recorded, range(1, TRADES + 1))

    response = await chart.history(market.id)

    assert response.points == shown(container.market_view, recorded)
    assert response.points[0] == HistoryPoint(t=opening_point(market).ts, p_yes=0.5, version=0)
    assert catch_ups == [caught_up_after(0)]
    await assert_books_balance(container.uow_factory)


async def test_a_scylla_that_holds_nothing_of_the_market_gives_the_postgres_chart_and_says_so(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
) -> None:
    """ScyllaDB is up and the relay has delivered nothing of this market yet."""
    market = await traded_market(container, factory, clock)
    elsewhere = await factory.market()
    await store.write(opening_point(elsewhere))

    response = await chart.history(market.id)

    recorded = await recorded_chart(container.uow_factory, market)
    assert response.source is HistorySource.POSTGRES
    assert response.points == shown(container.market_view, recorded)
    assert (await chart.history(elsewhere.id)).source is HistorySource.SCYLLA
    await assert_books_balance(container.uow_factory)


async def test_a_point_scylla_holds_beyond_the_version_just_read_is_shown(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    catch_ups: list[dict[str, Any]],
) -> None:
    """A trade that commits and is relayed between the read of the market row and the
    read of ScyllaDB: the chart is ahead of the row, never cut back to it."""
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    ahead = (TRADES + 1, clock.now(), STORE_P_YES_MICRO)
    await hold(store, market, [*recorded, ahead], range(TRADES + 2))

    response = await chart.history(market.id)

    assert [point.version for point in response.points] == list(range(TRADES + 2))
    assert catch_ups == []
    await assert_books_balance(container.uow_factory)


@pytest.fixture
async def recorded_market(
    container: Container, factory: Factory, clock: FrozenClock
) -> tuple[Market, list[RecordedPoint]]:
    """A traded market and its chart as Postgres records it."""
    market = await traded_market(container, factory, clock)
    return market, await recorded_chart(container.uow_factory, market)


@hypothesis_settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(held=st.sets(st.integers(min_value=0, max_value=TRADES)))
@example(held=set())
@example(held=set(range(TRADES + 1)))
@example(held={0, 1, 2, 4, 5, 6})
@example(held={TRADES})
async def test_whatever_scylla_holds_the_chart_is_complete_and_its_gap_free_start_is_scyllas(
    container: Container,
    recorded_market: tuple[Market, list[RecordedPoint]],
    held: set[int],
) -> None:
    """For every set of versions ScyllaDB may hold: the chart has each version once, in
    order; the versions from 0 up to the first one ScyllaDB lacks are ScyllaDB's, the
    rest Postgres'; and `source` is `scylla` exactly when ScyllaDB returned a point.

    The market is one for all examples (a fixture is set up once per test); each
    example has a store of its own.
    """
    market, recorded = recorded_market
    store = MemoryHistory()
    await store.ensure_schema()
    await hold(store, market, recorded, held)

    response = await history_on(container, store).history(market.id)

    reached = next(version for version in itertools.count() if version not in held) - 1
    assert response.points == from_store_up_to(container.market_view, recorded, reached)
    assert response.source is (HistorySource.SCYLLA if held else HistorySource.POSTGRES)
    assert store.calls.count("read") == 1
    await assert_books_balance(container.uow_factory)


async def test_scylla_is_asked_for_the_time_the_market_traded_and_no_further(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    owner_engine: AsyncEngine,
) -> None:
    """From `opened_at` to now, and after the deadline to `close_at`: the store sends
    one query per day of the range, so a chart never costs more than a market's
    lifetime, however long its proposal waited and however long ago it closed."""
    market = await factory.market(close_in=timedelta(days=2))
    opened_at = opening_point(market).ts
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE markets SET created_at = created_at - interval '3 days' WHERE id = :id"),
            {"id": market.id},
        )
    clock.advance(timedelta(hours=5))
    await chart.history(market.id)
    clock.advance(timedelta(days=400))
    await chart.history(market.id)

    assert store.reads == [
        (market.id, opened_at, opened_at + timedelta(hours=5), READ_LIMIT),
        (market.id, opened_at, market.close_at, READ_LIMIT),
    ]
    assert market.close_at == opened_at + timedelta(days=2)


async def test_no_postgres_connection_is_held_while_scylla_is_read(
    container: Container,
    factory: Factory,
    store: MemoryHistory,
    chart: HistoryService,
) -> None:
    """A slow ScyllaDB must not keep connections out of the pool that trades need."""
    market = await factory.market()
    pool = container.database.engine.pool
    assert isinstance(pool, QueuePool)
    checked_out: list[int] = []

    async def count_connections() -> None:
        checked_out.append(pool.checkedout())

    store.on_read = count_connections
    async with container.uow_factory() as uow:
        assert await uow.markets.get(market.id) is not None
        assert pool.checkedout() == 1, "a unit of work in use holds one"

    await chart.history(market.id)

    assert checked_out == [0]


async def test_a_point_from_scylla_carries_its_instant_cut_to_the_millisecond(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    chart: HistoryService,
    relay: OutboxRelay,
) -> None:
    """The one way in which "the same chart" differs by where it was read: a
    `timestamp` of ScyllaDB has milliseconds, a `timestamptz` of Postgres microseconds.
    A range that starts at `opened_at` still finds the opening point."""
    clock.advance(timedelta(microseconds=123_456))
    market = await traded_market(container, factory, clock, trades=2)
    recorded = await recorded_chart(container.uow_factory, market)
    from_postgres = await container.history.history(market.id)
    assert await relay.drain_once() == 3

    from_scylla = await chart.history(market.id)

    assert from_scylla.source is HistorySource.SCYLLA
    assert from_postgres.points == shown(container.market_view, recorded)
    assert from_scylla.points == shown(
        container.market_view, [(version, to_milliseconds(at), p) for version, at, p in recorded]
    )
    assert from_scylla.points[0].t == from_postgres.points[0].t - timedelta(microseconds=456)
    await assert_books_balance(container.uow_factory)


async def test_after_scylla_returns_and_the_relay_has_drained_the_same_chart_comes_from_scylla(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    relay: OutboxRelay,
    catch_ups: list[dict[str, Any]],
) -> None:
    """SPEC §12, "Failures", the whole story on the stand-in: ScyllaDB stops, trades go
    on, the chart is complete from Postgres; ScyllaDB returns, the relay drains what
    backed up, and the chart is ScyllaDB's again."""
    market = await factory.market()
    await queue_point(container.uow_factory, opening_point(market))
    alice = await factory.user(balance_micro=BUYER_BALANCE_MICRO)
    await buy_and_queue(
        factory, container.uow_factory, alice, market, Outcome.YES, shares_micro=20 * MICRO
    )
    assert await relay.drain_once() == 2
    assert (await chart.history(market.id)).source is HistorySource.SCYLLA

    store.down = True
    for outcome in (Outcome.NO, Outcome.YES, Outcome.YES):
        clock.advance(timedelta(minutes=1))
        await buy_and_queue(
            factory, container.uow_factory, alice, market, outcome, shares_micro=30 * MICRO
        )
    recorded = await recorded_chart(container.uow_factory, market)

    while_down = await chart.history(market.id)

    assert while_down.source is HistorySource.POSTGRES
    assert while_down.points == shown(container.market_view, recorded)
    assert len(while_down.points) == 5
    with pytest.raises(ScyllaUnavailableError):
        await relay.drain_once()
    assert len(await unpublished_ids(container.uow_factory)) == 3

    store.down = False
    behind = await chart.history(market.id)
    assert behind.points == while_down.points, "complete before the relay has caught up"
    assert await relay.drain_once() == 3
    catch_ups.clear()

    caught_up = await chart.history(market.id)

    assert caught_up.source is HistorySource.SCYLLA
    assert caught_up.points == while_down.points
    assert catch_ups == []
    assert await unpublished_ids(container.uow_factory) == []
    await assert_books_balance(container.uow_factory)


# --- A chart is bounded -------------------------------------------------------------------


def paced(market: Market, points: int, p_yes_micro: int) -> list[RecordedPoint]:
    """`points` points of `market`, versions 0 to `points - 1`, each `BETWEEN_TRADES`
    after the one before it and the first at the opening, all at `p_yes_micro`."""
    opened_at = opening_point(market).ts
    return [
        (version, opened_at + version * BETWEEN_TRADES, p_yes_micro) for version in range(points)
    ]


async def set_version(owner_engine: AsyncEngine, market: Market, version: int) -> None:
    """Let the row of `market` say that it has seen `version` trades."""
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE markets SET version = :version WHERE id = :id"),
            {"version": version, "id": market.id},
        )


async def record(
    container: Container, owner_engine: AsyncEngine, market: Market, trader: User, trades: int
) -> list[RecordedPoint]:
    """Write `trades` trades of `market` into the record with one statement,
    `BETWEEN_TRADES` apart from its opening, and let its row say so. Returns the chart
    Postgres then records, the opening first.

    A factory buys one trade at a time, which takes minutes for the thousands a chart
    has to cope with. Of these rows only what a chart reads means anything: the
    version, the instant, and the YES price a trade left, which rises by a millionth
    with every trade. No credit moves and nobody holds a share, so the books stay as
    they were.
    """
    statement = text(
        "INSERT INTO trades (id, market_id, user_id, side, outcome, shares_micro,"
        " amount_micro, p_yes_before_micro, p_yes_after_micro, market_version, created_at)"
        " SELECT gen_random_uuid(), :market, :trader, 'BUY', 'YES', 1000000, 1000000,"
        " 100000 + n - 1, 100000 + n, n,"
        " CAST(:opened_at AS timestamptz) + n * CAST(:between AS interval)"
        " FROM generate_series(1, :trades) AS n"
    )
    async with owner_engine.begin() as connection:
        await connection.execute(
            statement,
            {
                "market": market.id,
                "trader": trader.id,
                "opened_at": opening_point(market).ts,
                "between": BETWEEN_TRADES,
                "trades": trades,
            },
        )
    await set_version(owner_engine, market, trades)
    return await recorded_chart(container.uow_factory, market)


def versions_of(response: HistoryResponse) -> list[int]:
    return [point.version for point in response.points]


def rising(versions: Sequence[int]) -> bool:
    """Whether each version is above the one before it."""
    return all(before < after for before, after in itertools.pairwise(versions))


async def test_a_chart_of_43200_points_is_read_within_the_limit_and_answered_within_the_maximum(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    owner_engine: AsyncEngine,
    catch_ups: list[dict[str, Any]],
) -> None:
    """Three days of one person buying and selling as fast as MAX_ORDERS_PER_MINUTE
    lets them, which costs them a millionth of a token a pair. The store is asked
    once, for the newest HISTORY_READ_LIMIT points and no more; the answer holds
    HISTORY_MAX_POINTS of them, the opening and the latest price among them; and a
    day more of the same makes it no larger."""
    points = 43_200
    market = await factory.market()
    opened_at = opening_point(market).ts
    held = paced(market, points, STORE_P_YES_MICRO)
    await hold(store, market, held, range(points))
    await set_version(owner_engine, market, points - 1)
    clock.advance(points * BETWEEN_TRADES)
    assert clock.now() == opened_at + 3 * DAY

    response = await chart.history(market.id)

    versions = versions_of(response)
    assert len(response.points) == MAX_POINTS
    assert rising(versions)
    assert response.points[0] == HistoryPoint(t=opened_at, p_yes=0.5, version=0)
    _, latest_at, _ = held[-1]
    assert response.points[-1] == HistoryPoint(
        t=latest_at,
        p_yes=container.market_view.probabilities(STORE_P_YES_MICRO)[0],
        version=points - 1,
    )
    # Between the opening and the latest price: what was read, evenly thinned.
    assert min(versions[1:]) >= points - READ_LIMIT
    steps = {after - before for before, after in itertools.pairwise(versions[1:])}
    assert max(steps) - min(steps) <= 1, steps
    assert store.reads == [(market.id, opened_at, clock.now(), READ_LIMIT)]
    assert catch_ups == [], "ScyllaDB holds the latest version: no trade is read"
    assert (response.source, response.until) == (HistorySource.SCYLLA, clock.now())
    size = len(response.model_dump_json())
    assert size < 64 * 1024

    more = paced(market, points + 14_400, STORE_P_YES_MICRO)
    await hold(store, market, more, range(points, len(more)))
    await set_version(owner_engine, market, len(more) - 1)
    clock.advance(DAY)

    a_day_later = await chart.history(market.id)

    assert len(a_day_later.points) == MAX_POINTS
    assert versions_of(a_day_later)[-1] == len(more) - 1
    assert store.reads[1:] == [(market.id, opened_at, clock.now(), READ_LIMIT)]
    assert len(a_day_later.model_dump_json()) <= size + 16


async def test_with_scylla_stopped_postgres_is_asked_for_no_more_than_the_read_limit(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    catch_ups: list[dict[str, Any]],
) -> None:
    """The record of a very busy market is read under the same limit, newest first: the
    chart loses its oldest points and never the latest price, and it still starts where
    the market opened."""
    trades = READ_LIMIT + 200
    market = await factory.market()
    recorded = await record(container, owner_engine, market, await factory.user(), trades)
    clock.advance(DAY)

    response = await container.history.history(market.id)

    versions = versions_of(response)
    assert catch_ups == [caught_up_after(0)]
    assert len(response.points) == MAX_POINTS
    assert rising(versions)
    assert response.points[0] == shown(container.market_view, recorded[:1])[0]
    assert response.points[-1] == shown(container.market_view, recorded[-1:])[0]
    assert (versions[0], versions[-1]) == (0, trades)
    assert min(versions[1:]) > trades - READ_LIMIT, "the 200 oldest trades were not read"
    assert response.points == thinned(
        shown(container.market_view, [recorded[0], *recorded[-READ_LIMIT:]]), MAX_POINTS
    )
    assert response.source is HistorySource.POSTGRES
    await assert_books_balance(container.uow_factory)


async def test_a_busy_market_scylla_is_behind_on_is_caught_up_with_the_trades_it_lacks_alone(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    owner_engine: AsyncEngine,
    catch_ups: list[dict[str, Any]],
) -> None:
    """ScyllaDB holds more points than one read returns, and the relay still owes the
    last hundred. What ScyllaDB returned has no hole, so Postgres is asked for the
    hundred after it, not for the whole chart again."""
    trades, owed = READ_LIMIT + 200, 100
    market = await factory.market()
    recorded = await record(container, owner_engine, market, await factory.user(), trades)
    delivered = trades - owed
    await hold(store, market, recorded, range(delivered + 1))
    clock.advance(DAY)

    response = await chart.history(market.id)

    read_from_scylla = range(delivered - READ_LIMIT + 1, delivered + 1)
    assert store.reads == [(market.id, opening_point(market).ts, clock.now(), READ_LIMIT)]
    assert catch_ups == [caught_up_after(delivered)]
    assert response.points == thinned(
        shown(
            container.market_view,
            [
                recorded[0],
                *(
                    (version, at, STORE_P_YES_MICRO)
                    for version, at, _ in recorded[1:]
                    if version in read_from_scylla
                ),
                *recorded[delivered + 1 :],
            ],
        ),
        MAX_POINTS,
    )
    assert len(response.points) == MAX_POINTS
    assert (versions_of(response)[0], versions_of(response)[-1]) == (0, trades)
    assert response.points[-1] == shown(container.market_view, recorded[-1:])[0]
    assert response.source is HistorySource.SCYLLA


async def test_a_chart_of_exactly_the_maximum_is_returned_whole_and_one_point_more_is_thinned(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A market below the maximum is returned as it is recorded, point for point."""
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    exactly = history_on(container, container.quote_history, max_points=TRADES + 1)
    one_less = history_on(container, container.quote_history, max_points=TRADES)

    whole = await exactly.history(market.id)
    thinner = await one_less.history(market.id)

    assert whole.points == shown(container.market_view, recorded)
    assert whole == await container.history.history(market.id)
    assert versions_of(thinner) == [0, 1, 2, 3, 4, 6]
    assert thinner.points == shown(
        container.market_view, [point for point in recorded if point[0] != 5]
    )
    await assert_books_balance(container.uow_factory)


async def test_what_the_read_limit_cuts_are_the_oldest_trades_and_the_chart_keeps_its_opening(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    catch_ups: list[dict[str, Any]],
) -> None:
    """Three of six trades are read, the newest three, and the opening is the market
    row's. Of those four points the maximum, three, are answered: the opening, the
    latest price, and one between them."""
    kept = 3
    market = await traded_market(container, factory, clock)
    service = history_on(container, container.quote_history, read_limit=kept, max_points=kept)

    response = await service.history(market.id)

    recorded = await recorded_chart(container.uow_factory, market)
    assert catch_ups == [caught_up_after(0, limit=kept)]
    assert versions_of(response) == [0, 4, 6]
    assert response.points == shown(container.market_view, [recorded[0], recorded[4], recorded[6]])
    await assert_books_balance(container.uow_factory)


async def test_a_hole_in_what_a_cut_read_returned_is_filled_from_postgres_from_there_on(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    catch_ups: list[dict[str, Any]],
) -> None:
    """ScyllaDB lacks version 4 and is asked for four points: it returns 2, 3, 5 and 6.
    Its part of the chart begins at version 2, the lowest it returned, and ends before
    the hole; from there on Postgres is the record."""
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)
    await hold(store, market, recorded, [0, 1, 2, 3, 5, 6])
    service = history_on(container, store, read_limit=4, max_points=4)

    response = await service.history(market.id)

    assert catch_ups == [caught_up_after(3, limit=4)]
    # The opening, 2 and 3 of ScyllaDB, 4 to 6 of Postgres: six points, of which four
    # are answered.
    merged = [
        recorded[0],
        *((version, at, STORE_P_YES_MICRO) for version, at, _ in recorded[2:4]),
        *recorded[4:],
    ]
    assert response.points == thinned(shown(container.market_view, merged), 4)
    assert versions_of(response) == [0, 2, 4, 6]
    assert response.source is HistorySource.SCYLLA
    await assert_books_balance(container.uow_factory)


@hypothesis_settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    held=st.sets(st.integers(min_value=0, max_value=TRADES)),
    read_limit=st.integers(min_value=2, max_value=TRADES + 2),
    max_points=st.integers(min_value=2, max_value=TRADES + 2),
)
@example(held={0, 1, 2, 3, 5, 6}, read_limit=4, max_points=4)
@example(held=set(range(TRADES + 1)), read_limit=2, max_points=2)
@example(held=set(), read_limit=2, max_points=2)
@example(held={1, 3}, read_limit=2, max_points=2)
async def test_whatever_scylla_holds_and_whatever_the_bounds_a_chart_keeps_its_ends_and_its_bounds(
    container: Container,
    recorded_market: tuple[Market, list[RecordedPoint]],
    monkeypatch: pytest.MonkeyPatch,
    held: set[int],
    read_limit: int,
    max_points: int,
) -> None:
    """For every set of versions ScyllaDB may hold and every pair of bounds startup
    lets through: the chart starts at the opening and ends at the market's latest
    version, its versions rise, it has at most the maximum of points, each of them is
    ScyllaDB's or the record's point of its version, and neither store was asked for
    more than the read limit."""
    assume(max_points <= read_limit)
    market, recorded = recorded_market
    store = MemoryHistory()
    await store.ensure_schema()
    await hold(store, market, recorded, held)
    asked: list[dict[str, Any]] = []
    read = TradeRepository.list_price_points

    async def spied(
        self: TradeRepository, market_id: UUID, **how: Any
    ) -> Sequence[tuple[int, datetime, int]]:
        asked.append(how)
        return await read(self, market_id, **how)

    with monkeypatch.context() as patched:
        patched.setattr(TradeRepository, "list_price_points", spied)
        response = await history_on(
            container, store, read_limit=read_limit, max_points=max_points
        ).history(market.id)

    versions = versions_of(response)
    assert rising(versions)
    assert (versions[0], versions[-1]) == (0, TRADES)
    assert len(versions) <= max_points
    of_the_record = {point.version: point for point in shown(container.market_view, recorded)}
    of_the_store = {
        version: point.model_copy(
            update={"p_yes": container.market_view.probabilities(STORE_P_YES_MICRO)[0]}
        )
        for version, point in of_the_record.items()
    }
    returned = sorted(held)[-read_limit:]
    for point in response.points:
        if point.version in returned:
            assert point in (of_the_store[point.version], of_the_record[point.version])
        else:
            assert point == of_the_record[point.version]
    assert [limit for _, _, _, limit in store.reads] == [read_limit]
    assert [how["limit"] for how in asked] in ([], [read_limit])
    assert response.source is (HistorySource.SCYLLA if held else HistorySource.POSTGRES)


# --- The right edge of a chart ------------------------------------------------------------


async def settled_or_not(
    container: Container, factory: Factory, clock: FrozenClock, status: MarketStatus
) -> tuple[Market, datetime]:
    """A market two people traded, brought to `status` by an admin an hour after its
    last trade, and settled an hour after that where `status` is a settlement. Returns
    it with the instant of the first of those actions; the clock stands an hour after
    the last of them."""
    admin = factory.identity(await factory.admin())
    lifecycle = container.market_lifecycle
    market = await traded_market(container, factory, clock, trades=2)
    acted_at = clock.advance(HOUR)
    if status is MarketStatus.HALTED:
        await lifecycle.halt(admin, market.id, HaltMarketRequest(reason="The source is in doubt."))
    elif status is not MarketStatus.OPEN:
        await lifecycle.close(admin, market.id, CloseMarketRequest())
    clock.advance(HOUR)
    if status is MarketStatus.RESOLVED:
        await lifecycle.resolve(
            admin, market.id, ResolveMarketRequest(outcome=Outcome.YES, evidence="The menu.")
        )
    elif status is MarketStatus.VOID:
        await lifecycle.void(admin, market.id, VoidMarketRequest(reason="The mensa was closed."))
    clock.advance(HOUR)
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    assert row.status is status
    return row, acted_at


@pytest.mark.parametrize("status", VISIBLE_STATUSES)
async def test_a_chart_ends_at_now_while_its_market_can_trade_and_where_trading_ended_after_that(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    relay: OutboxRelay,
    status: MarketStatus,
) -> None:
    """`until`, for a market in each status, read before and after its deadline, from
    Postgres and from ScyllaDB. An open and a halted market can still trade until
    `close_at`: their chart ends at the clock's now, and from `close_at` on at
    `close_at`. A market an admin closed, resolved or voided before its deadline
    stopped trading when it was closed. No point lies after the edge."""
    market, acted_at = await settled_or_not(container, factory, clock, status)
    can_still_trade = status in (MarketStatus.OPEN, MarketStatus.HALTED)
    await relay.drain_once()
    asked_at = clock.now()

    before_the_deadline = [
        await container.history.history(market.id),
        await chart.history(market.id),
    ]
    clock.set(market.close_at)
    from_the_deadline_on = [
        await container.history.history(market.id),
        await chart.history(market.id),
    ]

    assert asked_at == acted_at + 2 * HOUR < market.close_at
    for answer in before_the_deadline:
        assert answer.until == (asked_at if can_still_trade else acted_at)
    for answer in from_the_deadline_on:
        assert answer.until == (market.close_at if can_still_trade else acted_at)
    for answer in (*before_the_deadline, *from_the_deadline_on):
        assert versions_of(answer) == [0, 1, 2]
        assert all(point.t <= answer.until for point in answer.points)
    assert [answer.source for answer in before_the_deadline] == [
        HistorySource.POSTGRES,
        HistorySource.SCYLLA,
    ]
    # ScyllaDB is asked up to the edge and no further.
    assert [until for _, _, until, _ in store.reads] == [
        asked_at if can_still_trade else acted_at,
        market.close_at if can_still_trade else acted_at,
    ]
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("settle", ["resolve", "void"])
async def test_a_market_that_is_settled_after_its_deadline_stopped_trading_at_the_deadline(
    container: Container, factory: Factory, clock: FrozenClock, settle: str
) -> None:
    """The settlement closes the market on its way, a day late. Trading ended at
    `close_at`, not when the row was closed."""
    admin = factory.identity(await factory.admin())
    market = await traded_market(container, factory, clock, trades=2)
    clock.set(market.close_at + DAY)
    if settle == "resolve":
        await container.market_lifecycle.resolve(
            admin, market.id, ResolveMarketRequest(outcome=Outcome.NO, evidence="The menu.")
        )
    else:
        await container.market_lifecycle.void(
            admin, market.id, VoidMarketRequest(reason="The mensa was closed.")
        )
    clock.advance(DAY)

    response = await container.history.history(market.id)

    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    assert row.closed_at == market.close_at + DAY
    assert response.until == market.close_at
    assert all(point.t <= response.until for point in response.points)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("settle", ["resolve", "void"])
async def test_a_halted_market_that_is_settled_stopped_trading_when_the_settlement_closed_it(
    container: Container, factory: Factory, clock: FrozenClock, settle: str
) -> None:
    """No admin closed it: the settlement did, in its own transaction, and that is the
    moment the market was resolved or voided."""
    market, _ = await settled_or_not(container, factory, clock, MarketStatus.HALTED)
    admin = factory.identity(await factory.admin(1))
    settled_at = clock.now()
    if settle == "resolve":
        await container.market_lifecycle.resolve(
            admin, market.id, ResolveMarketRequest(outcome=Outcome.YES, evidence="The menu.")
        )
    else:
        await container.market_lifecycle.void(
            admin, market.id, VoidMarketRequest(reason="The mensa was closed.")
        )
    clock.advance(DAY)

    response = await container.history.history(market.id)

    assert response.until == settled_at < market.close_at
    assert all(point.t <= response.until for point in response.points)
    await assert_books_balance(container.uow_factory)


async def test_a_point_dated_after_the_end_of_trading_moves_the_edge_to_itself(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    store: MemoryHistory,
    chart: HistoryService,
    relay: OutboxRelay,
) -> None:
    """A clock that is set back between a trade and the close of its market dates the
    close before the trade. The chart still ends no earlier than its latest point,
    whichever store it was read from."""
    admin = factory.identity(await factory.admin())
    market = await factory.market()
    await queue_point(container.uow_factory, opening_point(market))
    alice = await factory.user(balance_micro=BUYER_BALANCE_MICRO)
    traded_at = clock.advance(HOUR)
    await buy_and_queue(
        factory, container.uow_factory, alice, market, Outcome.YES, shares_micro=20 * MICRO
    )
    clock.set(traded_at - timedelta(minutes=30))
    await container.market_lifecycle.close(admin, market.id, CloseMarketRequest())
    closed_at = clock.now()
    clock.advance(DAY)
    assert await relay.drain_once() == 2

    answers = [await container.history.history(market.id), await chart.history(market.id)]

    assert opening_point(market).ts < closed_at < traded_at
    for answer in answers:
        assert versions_of(answer) == [0, 1]
        assert answer.points[-1].t == traded_at
        assert answer.until == traded_at
    # ScyllaDB was asked up to the close; the trade dated after it came from the record.
    assert [until for _, _, until, _ in store.reads] == [closed_at]
    assert answers[1].source is HistorySource.SCYLLA
    await assert_books_balance(container.uow_factory)


async def test_the_route_names_the_right_edge_as_an_instant(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    market, closed_at = await settled_or_not(container, factory, clock, MarketStatus.CLOSED)
    as_user(await factory.user())

    body = (await client.get(f"/api/markets/{market.id}/history")).json()

    assert set(body) == {"points", "source", "until"}
    assert datetime.fromisoformat(body["until"]) == closed_at
    assert body["until"].endswith("Z"), "an instant in UTC, like every `t` of a point"
    assert [point["version"] for point in body["points"]] == [0, 1, 2]
    assert all(datetime.fromisoformat(point["t"]) <= closed_at for point in body["points"])


# --- The real ScyllaDB node (slow to start: run with `-m scylla`) ----------------------


@pytest.mark.scylla
async def test_once_the_relay_has_drained_the_same_chart_comes_from_the_real_scylla(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    scylla_client: ScyllaClient,
    scylla_settings: ScyllaSettings,
    catch_ups: list[dict[str, Any]],
) -> None:
    real = await ready(QuoteHistoryStore(scylla_client, scylla_settings))
    service = history_on(container, real)
    relay = OutboxRelay(container.uow_factory, real, scylla_settings, clock)
    clock.advance(timedelta(microseconds=123_456))
    market = await traded_market(container, factory, clock)
    recorded = await recorded_chart(container.uow_factory, market)

    undelivered = await service.history(market.id)

    assert undelivered.source is HistorySource.POSTGRES, "the node is up and holds nothing yet"
    assert undelivered.points == shown(container.market_view, recorded)

    assert await relay.drain_once() == TRADES + 1
    catch_ups.clear()
    delivered = await service.history(market.id)

    assert delivered.source is HistorySource.SCYLLA
    assert delivered.points == shown(
        container.market_view, [(version, to_milliseconds(at), p) for version, at, p in recorded]
    )
    assert catch_ups == [], "served by ScyllaDB alone"
    await assert_books_balance(container.uow_factory)


@pytest.mark.scylla
async def test_with_the_real_scylla_cut_off_the_chart_is_from_postgres_and_catches_up_on_its_return(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    scylla_proxy: SwitchableProxy,
) -> None:
    """SPEC §12, "Failures", on the node itself, through the application's own client:
    `scylla_proxy` puts `container` behind a connection the test can cut."""
    real = await ready(container.quote_history)
    relay = OutboxRelay(container.uow_factory, real, container.settings.scylla, clock)
    uow_factory = container.uow_factory
    market = await factory.market()
    await queue_point(uow_factory, opening_point(market))
    alice = await factory.user(balance_micro=BUYER_BALANCE_MICRO)
    await buy_and_queue(factory, uow_factory, alice, market, Outcome.YES, shares_micro=20 * MICRO)
    assert await relay.drain_once() == 2
    assert (await container.history.history(market.id)).source is HistorySource.SCYLLA

    scylla_proxy.cut()
    # Let the driver notice that its connections are gone before the next statement.
    await asyncio.sleep(0.2)
    for outcome in (Outcome.NO, Outcome.YES):
        clock.advance(timedelta(minutes=1))
        await buy_and_queue(factory, uow_factory, alice, market, outcome, shares_micro=30 * MICRO)
    recorded = await recorded_chart(uow_factory, market)

    while_cut = await container.history.history(market.id)

    assert while_cut.source is HistorySource.POSTGRES
    assert while_cut.points == shown(container.market_view, recorded)
    with pytest.raises(ScyllaUnavailableError):
        await relay.drain_once()
    assert len(await unpublished_ids(uow_factory)) == 2

    scylla_proxy.restore()
    async with asyncio.timeout(SCYLLA_RECOVERY_SECONDS):
        while await unpublished_ids(uow_factory):
            with suppress(ScyllaUnavailableError):
                await relay.drain_once()
            await asyncio.sleep(0.25)

    caught_up = await container.history.history(market.id)

    assert caught_up.source is HistorySource.SCYLLA
    assert caught_up.points == while_cut.points
    await assert_books_balance(uow_factory)
