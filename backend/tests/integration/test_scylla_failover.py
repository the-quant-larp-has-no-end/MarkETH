"""ScyllaDB goes away and comes back while markets trade (SPEC §9, §12 "Failures",
Appendix A.4, A.10).

"With Scylla stopped trades succeed and the chart catches up after restart." Here that
is the whole application on a real ScyllaDB node: markets are proposed, approved and
traded through the routes, the relay runs as its process runs it, and the chart is read
from `GET /markets/{id}/history`. The node is cut off through `scylla_proxy`, which is
what its host going away looks like to the application, and restored with its data.

Postgres is the record throughout. What a chart has to show is what `trades` holds
(`recorded_chart`), wherever its points were read from.

These tests start a ScyllaDB container, which takes about a minute: run them with
`uv run pytest -m scylla`.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Final
from uuid import UUID, uuid7

import httpx
import pytest

from app.container import Container
from app.core.enums import MarketStatus
from app.core.units import MICRO, format_cr, to_micro
from app.models import Market, User
from app.relay.outbox_relay import OutboxRelay
from app.schemas.health import DependencyStatus, HealthResponse
from app.schemas.live import HistoryPoint, HistoryResponse, HistorySource
from app.schemas.markets import MarketDetailResponse
from app.schemas.trading import TradeResponse
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.history import ready, recorded_chart, unpublished_ids
from tests.support.network import SwitchableProxy

pytestmark = [pytest.mark.integration, pytest.mark.scylla]

WALLET: Final[int] = 500 * MICRO
"""What a trader of these tests starts with."""

RELAY_POLL_MS: Final[int] = 100
"""How long the relay of these tests idles between two looks at the outbox."""

RECOVERY_SECONDS: Final[float] = 60.0
"""The driver tries a node it lost again every few seconds; this leaves it many tries."""

MINUTE: Final[timedelta] = timedelta(minutes=1)
WEEK: Final[timedelta] = timedelta(days=7)


@asynccontextmanager
async def relay_running(container: Container) -> AsyncIterator[None]:
    """The outbox relay, running as `python -m app.relay` runs it, on the stores of
    `container`, for as long as the block is open."""
    scylla = container.settings.scylla.model_copy(update={"outbox_poll_interval_ms": RELAY_POLL_MS})
    relay = OutboxRelay(container.uow_factory, container.quote_history, scylla, container.clock)
    stop = asyncio.Event()
    running = asyncio.create_task(relay.run_forever(stop))
    try:
        yield
    finally:
        stop.set()
        await asyncio.wait_for(running, timeout=RECOVERY_SECONDS)


async def until(condition: Callable[[], Awaitable[bool]], what: str) -> None:
    """Wait until `condition` holds; fail, saying what did not happen, after the
    recovery time."""
    try:
        async with asyncio.timeout(RECOVERY_SECONDS):
            while True:
                if await condition():
                    return
                await asyncio.sleep(0.2)
    except TimeoutError:
        raise AssertionError(f"after {RECOVERY_SECONDS:g} s still not: {what}") from None


async def drained(container: Container) -> None:
    """The relay has delivered every row of the outbox."""

    async def nothing_owed() -> bool:
        return not await unpublished_ids(container.uow_factory)

    await until(nothing_owed, "the outbox is drained")


async def scylla_is(client: httpx.AsyncClient, wanted: DependencyStatus) -> None:
    """`GET /health` reports ScyllaDB as `wanted`."""

    async def reported() -> bool:
        health = await client.get("/api/health")
        return HealthResponse.model_validate(health.json()).scylla is wanted

    await until(reported, f"/health says ScyllaDB is {wanted}")


class Desk:
    """The routes, called as somebody through `as_user`."""

    def __init__(self, client: httpx.AsyncClient, as_user: ActAs) -> None:
        self._client = client
        self._as_user = as_user

    async def open_market(self, proposer: User, admin: User, clock: FrozenClock) -> UUID:
        """A market proposed and approved through the routes. The approval queues the
        opening point of its chart."""
        close_at = clock.now() + WEEK
        self._as_user(proposer)
        proposed = await self._client.post(
            "/api/markets",
            json={
                "question": "Will the Polymensa serve Älplermagronen on Friday?",
                "yes_definition": "It is on the lunch menu that day.",
                "no_definition": "It is not on the lunch menu that day.",
                "resolution_criteria": "The menu published for that Friday decides.",
                "resolution_source": "The menu plan on ethz.ch",
                "close_at": close_at.isoformat(),
                "resolve_by": (close_at + WEEK).isoformat(),
                "policy_accepted": True,
            },
        )
        assert proposed.status_code == 201, proposed.text
        market_id = UUID(proposed.json()["id"])
        self._as_user(admin)
        approved = await self._client.post(
            f"/api/admin/markets/{market_id}/approve", json={"tier": "DEFAULT"}
        )
        assert approved.status_code == 200, approved.text
        assert MarketDetailResponse.model_validate(approved.json()).status is MarketStatus.OPEN
        return market_id

    async def buy(
        self, who: User, market_id: UUID, outcome: str, amount_micro: int
    ) -> TradeResponse:
        """A trade through the route. It has to succeed, ScyllaDB or no ScyllaDB."""
        self._as_user(who)
        response = await self._client.post(
            f"/api/markets/{market_id}/trades",
            json={"side": "BUY", "outcome": outcome, "amount_cr": format_cr(amount_micro)},
            headers={"Idempotency-Key": str(uuid7())},
        )
        assert response.status_code == 201, response.text
        return TradeResponse.model_validate(response.json())

    async def chart(self, who: User, market_id: UUID) -> HistoryResponse:
        self._as_user(who)
        response = await self._client.get(f"/api/markets/{market_id}/history")
        assert response.status_code == 200, response.text
        return HistoryResponse.model_validate(response.json())


@pytest.fixture
def desk(client: httpx.AsyncClient, as_user: ActAs) -> Desk:
    return Desk(client, as_user)


async def stored(container: Container, market_id: UUID) -> Market:
    async with container.uow_factory() as uow:
        market = await uow.markets.get(market_id)
    assert market is not None
    return market


async def recorded(container: Container, market_id: UUID) -> list[HistoryPoint]:
    """The chart of a market as Postgres records it, in the numbers the API shows: the
    opening at even odds and the YES price after every trade."""
    view = container.market_view
    chart = await recorded_chart(container.uow_factory, await stored(container, market_id))
    return [
        HistoryPoint(t=at, p_yes=view.probabilities(p_yes_micro)[0], version=version)
        for version, at, p_yes_micro in chart
    ]


async def test_with_scylla_cut_off_trades_succeed_and_the_chart_catches_up_once_it_returns(
    desk: Desk,
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    scylla_proxy: SwitchableProxy,
) -> None:
    """SPEC §12, "Failures", in one run.

    ScyllaDB up: the relay copies every committed price, and the chart is ScyllaDB's.
    ScyllaDB cut off: every trade still answers 201, a market is still approved, the
    chart is complete from Postgres and says so, and the outbox backs up. ScyllaDB
    back: the relay drains what backed up without anybody's help, and the same chart
    comes from ScyllaDB again, the opening point of the market that was approved
    during the outage included.
    """
    await ready(container.quote_history)
    proposer = await factory.user()
    alice, bob = [await factory.user(balance_micro=WALLET) for _ in range(2)]
    admin = await factory.admin()

    async with relay_running(container):
        market_id = await desk.open_market(proposer, admin, clock)
        for who, outcome, amount in ((alice, "YES", 50), (bob, "NO", 25)):
            clock.advance(MINUTE)
            await desk.buy(who, market_id, outcome, amount * MICRO)
        await drained(container)

        while_up = await desk.chart(alice, market_id)

        assert while_up.source is HistorySource.SCYLLA
        assert [point.version for point in while_up.points] == [0, 1, 2]
        assert while_up.points == await recorded(container, market_id)

        scylla_proxy.cut()
        await scylla_is(client, DependencyStatus.DOWN)
        bought: list[TradeResponse] = []
        for who, outcome, amount in ((alice, "YES", 25), (bob, "NO", 50), (alice, "NO", 25)):
            clock.advance(MINUTE)
            bought.append(await desk.buy(who, market_id, outcome, amount * MICRO))
        clock.advance(MINUTE)
        second_id = await desk.open_market(proposer, admin, clock)
        clock.advance(MINUTE)
        await desk.buy(bob, second_id, "YES", 25 * MICRO)

        while_cut = await desk.chart(alice, market_id)
        second_while_cut = await desk.chart(alice, second_id)

        assert [trade.market_version for trade in bought] == [3, 4, 5]
        assert while_cut.source is HistorySource.POSTGRES
        assert [point.version for point in while_cut.points] == [0, 1, 2, 3, 4, 5]
        assert while_cut.points == await recorded(container, market_id)
        assert while_cut.points[:3] == while_up.points
        assert while_cut.points[-1].p_yes == bought[-1].p_yes_after
        assert second_while_cut.source is HistorySource.POSTGRES
        assert second_while_cut.points == await recorded(container, second_id)
        # Three trades, an approval and one more trade wait for ScyllaDB.
        assert len(await unpublished_ids(container.uow_factory)) == 5
        await assert_books_balance(container.uow_factory)

        scylla_proxy.restore()
        await drained(container)

        caught_up = await desk.chart(alice, market_id)
        second_caught_up = await desk.chart(alice, second_id)

    assert caught_up.source is HistorySource.SCYLLA
    assert caught_up.points == while_cut.points
    assert second_caught_up.source is HistorySource.SCYLLA
    assert [point.version for point in second_caught_up.points] == [0, 1]
    assert second_caught_up.points == second_while_cut.points
    assert (await stored(container, market_id)).version == 5
    await assert_books_balance(container.uow_factory)


async def test_a_trade_while_scylla_is_cut_off_is_answered_as_one_while_it_is_up(
    desk: Desk,
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    scylla_proxy: SwitchableProxy,
) -> None:
    """A trade never asks ScyllaDB anything: it queues a row for the relay in its own
    transaction. So its answer, the market page and the list are the same with the
    node away, and the row waits."""
    await ready(container.quote_history)
    proposer = await factory.user()
    alice = await factory.user(balance_micro=WALLET)
    market_id = await desk.open_market(proposer, await factory.admin(), clock)
    scylla_proxy.cut()
    await scylla_is(client, DependencyStatus.DOWN)
    owed = len(await unpublished_ids(container.uow_factory))

    clock.advance(MINUTE)
    answer = await desk.buy(alice, market_id, "YES", 50 * MICRO)
    page = await client.get(f"/api/markets/{market_id}")
    listed = await client.get("/api/markets")

    assert answer.market_version == 1
    assert to_micro(answer.balance_cr) == WALLET - to_micro(answer.amount_cr)
    assert (page.status_code, listed.status_code) == (200, 200)
    assert page.json()["p_yes"] == answer.p_yes_after
    assert [item["version"] for item in listed.json()["items"]] == [1]
    assert len(await unpublished_ids(container.uow_factory)) == owed + 1
    scylla_proxy.restore()
    await scylla_is(client, DependencyStatus.UP)
    await assert_books_balance(container.uow_factory)
