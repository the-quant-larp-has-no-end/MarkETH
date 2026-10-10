"""The price chart of a market: `HistoryService.history` (SPEC §9, §10, §12, A.4).

SPEC §12, "Failures": with ScyllaDB stopped trades succeed and the chart catches up
after its return. The default suite runs with ScyllaDB as a closed port, so every chart
here that is read through `container.history` is the "stopped" half: complete, and
built from the `trades` table.

A ScyllaDB that answers is `MemoryHistory` (tests/support/history.py) in the default
suite and the real node in the tests marked `scylla`, at the end of the file.

The trades are the factory's, each with the outbox row a trade queues, so nothing here
waits for the trading or the lifecycle service. A point the tests put into the store by
hand carries `STORE_P_YES_MICRO`, a price no trade leaves: the chart then shows which of
its points the store served and which Postgres.
"""

import asyncio
import itertools
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, example, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from app.container import Container
from app.core.config import ScyllaSettings
from app.core.enums import MarketStatus, Outcome, UserRole, UserStatus
from app.core.errors import NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO
from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from app.models import Market, Trade
from app.relay.outbox_relay import OutboxRelay
from app.repositories.trading import TradeRepository
from app.schemas.live import HistoryPoint, HistoryResponse, HistorySource
from app.services import history as history_module
from app.services.history import MAX_POSTGRES_POINTS, HistoryService
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

pytestmark = pytest.mark.integration

TRADES: Final[int] = 6
"""Trades of `traded_market`, so its chart has the versions 0 to 6."""

STORE_P_YES_MICRO: Final[int] = 987_654
"""The YES price of a point a test puts into the store by hand. No trade here leaves it."""

BUYER_BALANCE_MICRO: Final[int] = 1_000 * MICRO

SCYLLA_RECOVERY_SECONDS: Final[float] = 30.0
"""The driver retries a dead node every 5 seconds; this leaves it several attempts."""

VISIBLE_STATUSES: Final[list[MarketStatus]] = sorted(set(MarketStatus) - HIDDEN_STATUSES)


@pytest.fixture
async def store() -> MemoryHistory:
    """A ScyllaDB that answers: its schema is in place and it holds nothing."""
    memory = MemoryHistory()
    await memory.ensure_schema()
    return memory


def history_on(container: Container, store: QuoteHistoryStore) -> HistoryService:
    """The application's history service, reading `store` instead of the suite's
    unreachable ScyllaDB."""
    return HistoryService(
        container.uow_factory, container.settings, container.clock, container.market_view, store
    )


@pytest.fixture
def chart(container: Container, store: MemoryHistory) -> HistoryService:
    return history_on(container, store)


@pytest.fixture
def relay(container: Container, store: MemoryHistory) -> OutboxRelay:
    return OutboxRelay(container.uow_factory, store, container.settings.scylla, container.clock)


@pytest.fixture
def catch_ups(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """How each read of a market's trades was asked for, in order. The history service
    reads them for one reason only: to catch a chart up from Postgres."""
    seen: list[dict[str, Any]] = []
    read = TradeRepository.list_for_market

    async def recorded(self: TradeRepository, market_id: UUID, **how: Any) -> Sequence[Trade]:
        seen.append(how)
        return await read(self, market_id, **how)

    monkeypatch.setattr(TradeRepository, "list_for_market", recorded)
    return seen


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


async def test_a_very_busy_market_loses_its_oldest_points_and_never_the_latest_price(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read from Postgres is cut at `MAX_POSTGRES_POINTS`, and what goes are the
    oldest points."""
    kept = 3
    monkeypatch.setattr(history_module, "MAX_POSTGRES_POINTS", kept)
    market = await traded_market(container, factory, clock)

    response = await container.history.history(market.id)

    recorded = await recorded_chart(container.uow_factory, market)
    assert response.points == shown(container.market_view, [recorded[0], *recorded[-kept:]])
    assert [point.version for point in response.points] == [0, 4, 5, 6]
    await assert_books_balance(container.uow_factory)


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
    assert catch_ups == [{"limit": MAX_POSTGRES_POINTS, "after_version": 3, "newest_first": True}]
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
    assert catch_ups == [{"limit": MAX_POSTGRES_POINTS, "after_version": 2, "newest_first": True}]
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
    assert catch_ups == [{"limit": MAX_POSTGRES_POINTS, "after_version": 0, "newest_first": True}]
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
        (market.id, opened_at, opened_at + timedelta(hours=5)),
        (market.id, opened_at, market.close_at),
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
