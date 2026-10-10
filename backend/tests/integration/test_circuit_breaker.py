"""The circuit breaker around a trade (SPEC §5, §8 step 8, Appendix A.4).

The books of a market are bent behind the application's back, through the owner role,
and then somebody trades. The trade's own writes are checked before its commit, so it
runs into what is wrong: it is rolled back as a whole, a second transaction halts the
market and leaves an alert for the admins, and the trader is told to try again shortly
(503).

Two tests need the moment between a trade's rollback and the breaker's transaction.
Their trading service is the container's with one thing replaced: the units of work it
opens, of which the second one a trade opens is the breaker's.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Self
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.enums import AlertSeverity, MarketStatus, Outcome, TradeSide
from app.core.errors import ServiceUnavailableError
from app.core.units import MICRO, format_cr, to_cr
from app.db.base import Base
from app.integrations.redis.market_cache import MarketCache
from app.models import (
    Alert,
    AuditLogEntry,
    IdempotencyKey,
    JournalEntry,
    Market,
    OutboxEvent,
    Position,
    Posting,
    Trade,
    User,
)
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.live import StatusEvent
from app.schemas.trading import TradeRequest
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.live_quotes import LiveEvent
from app.services.trading import TradingService
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
from tests.support.economy import PRECISION
from tests.support.factories import ActAs, Factory
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

_SETTINGS = load_base_settings()

SIGNUP_GRANT: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what a trader of these tests starts with."""

SMALLEST_PRESET: Final[int] = min(_SETTINGS.pricing.stake_presets_micro)
"""The smallest buy button of the trade sheet (TRADE_STAKE_PRESETS_CR), in µCR."""

HELD: Final[int] = 40 * MICRO
"""YES shares the holder of these tests has in the market before anything is bent."""

TRADING_LOGGER: Final[str] = "app.services.trading"

WAIT_SECONDS: Final[float] = 10.0
"""How long a test waits for what should happen at once, before it fails instead of
hanging."""

BUY: Final[dict[str, str]] = {
    "side": "BUY",
    "outcome": "NO",
    "amount_cr": format_cr(SMALLEST_PRESET),
}
"""The trade that runs into the broken books: sound in itself, and one the trader
could make on any other day."""

WRITTEN_BY_A_TRADE: Final[tuple[type[Base], ...]] = (
    Trade,
    Position,
    JournalEntry,
    Posting,
    OutboxEvent,
    IdempotencyKey,
)

BENT: Final[dict[str, tuple[str, ...]]] = {
    "ledger_nets_to_zero": (
        "UPDATE accounts SET balance_micro = balance_micro + 1 WHERE kind = 'VIS_MM_PNL'",
    ),
    "positions_match_market": (
        "UPDATE positions SET shares_micro = shares_micro + 1 WHERE market_id = :market_id",
    ),
    "market_within_budget": (
        "UPDATE positions SET shares_micro = shares_micro + :given WHERE market_id = :market_id",
        "UPDATE markets SET q_yes_micro = q_yes_micro + :given WHERE id = :market_id",
    ),
}
"""Per check of the breaker, statements that leave a book in which that check, and no
earlier one, fails for the next trade: a micro-credit from nowhere, a micro-share the
market never sold, and YES shares handed out for nothing until the market could lose
more than its budget."""


# --- Bending and reading the books ---------------------------------------------------------


async def bend(owner_engine: AsyncEngine, statements: Sequence[str], **parameters: Any) -> None:
    """Change the database as the owner role, behind the application's back."""
    async with owner_engine.begin() as connection:
        for statement in statements:
            used = {name: value for name, value in parameters.items() if f":{name}" in statement}
            await connection.execute(text(statement), used)


async def break_books(owner_engine: AsyncEngine, market: Market, invariant: str) -> None:
    await bend(owner_engine, BENT[invariant], market_id=market.id, given=2 * market.budget_micro)


async def stored(container: Container, market: Market) -> Market:
    """The market's row as Postgres has it now."""
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    return row


async def market_balance(container: Container, market: Market) -> int:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_market_account(market.id)
    assert account is not None
    return account.balance_micro


async def rows[ModelT: Base](container: Container, model: type[ModelT]) -> Sequence[ModelT]:
    """Every row of a table, oldest first for the tables whose key grows."""
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(model))).all()


@dataclass(frozen=True, slots=True)
class Untouched:
    """What a trade that was rolled back leaves exactly as it was."""

    rows: dict[str, int]
    quantities: tuple[int, int, int, int]
    balances: tuple[int, ...]


async def untouched(
    container: Container, factory: Factory, market: Market, *users: User
) -> Untouched:
    row = await stored(container, market)
    return Untouched(
        rows={
            model.__tablename__: await count_rows(container.uow_factory, model)
            for model in WRITTEN_BY_A_TRADE
        },
        quantities=(row.q_yes_micro, row.q_no_micro, row.version, row.volume_micro),
        balances=tuple([await factory.balance_micro(user) for user in users]),
    )


def lmsr_buy(market: Market) -> lmsr.TradeQuote:
    """What `BUY` costs and leaves on the quantities of `market`."""
    state = lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)
    return lmsr.quote_buy_for_budget(state, Outcome.NO, SMALLEST_PRESET, precision=PRECISION)


def found_by_the_check(
    invariant: str, market: Market, quote: lmsr.TradeQuote, in_the_market_micro: int
) -> dict[str, Any]:
    """The context the failing check reports for `BUY` on `market`, whose books were
    bent with `BENT[invariant]`. The check sees the trade's own writes: the quantities
    after it, and its cost in the market's account, which held `in_the_market_micro`."""
    if invariant == "ledger_nets_to_zero":
        return {"total_balance_micro": 1}
    if invariant == "positions_match_market":
        return {
            "market_id": str(market.id),
            "q_yes_micro": quote.after.q_yes,
            "q_no_micro": quote.after.q_no,
            "positions_yes_micro": quote.after.q_yes + 1,
            "positions_no_micro": quote.after.q_no,
        }
    owed_micro = max(quote.after.q_yes, quote.after.q_no)
    return {
        "market_id": str(market.id),
        "exposure_micro": owed_micro - (in_the_market_micro + quote.amount_micro),
        "budget_micro": market.budget_micro,
    }


def alert_context(found: dict[str, Any], market: Market, user: User) -> dict[str, Any]:
    """What an alert keeps: what the check found, where, and the trade that found it."""
    return {
        **found,
        "market_id": str(market.id),
        "user_id": str(user.id),
        "request": TradeRequest.model_validate(BUY).model_dump(mode="json"),
    }


async def post_trade(
    client: httpx.AsyncClient, market: Market, key: UUID | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/markets/{market.id}/trades",
        json=BUY,
        headers={"Idempotency-Key": str(key or uuid7())},
    )


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is that error; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    details: dict[str, Any] = body["details"]
    return details


async def next_event(events: AsyncIterator[LiveEvent]) -> LiveEvent:
    return await asyncio.wait_for(anext(events), timeout=WAIT_SECONDS)


# --- A trading service whose breaker can be met on its way ---------------------------------


class AfterSomethingElse(UnitOfWork):
    """A unit of work that awaits `before` ahead of its BEGIN."""

    def __init__(self, container: Container, before: Callable[[], Awaitable[object]]) -> None:
        super().__init__(container.database.session_factory)
        self._before = before

    async def __aenter__(self) -> Self:
        await self._before()
        return await super().__aenter__()


def breaker_after(container: Container, before: Callable[[], Awaitable[object]]) -> UowFactory:
    """The units of work of a trading service whose circuit breaker waits for `before`.

    A trade opens one unit of work for its transaction and, if that was rolled back
    for a broken invariant, a second one for the breaker. Each trade runs in a task of
    its own here, so the second unit of work of a task is a breaker's: `before` is
    awaited ahead of its BEGIN, when the trade is rolled back and the breaker holds
    nothing yet.
    """
    opened: dict[asyncio.Task[Any] | None, int] = {}

    def factory() -> UnitOfWork:
        task = asyncio.current_task()
        opened[task] = opened.get(task, 0) + 1
        if opened[task] == 2:
            return AfterSomethingElse(container, before)
        return container.uow_factory()

    return factory


def trading_service(container: Container, uow_factory: UowFactory) -> TradingService:
    """A `TradingService` wired like the container's, on other units of work."""
    return TradingService(
        uow_factory,
        container.settings,
        container.clock,
        container.market_view,
        container.quotes,
        container.ledger,
        container.invariants,
        container.audit,
        container.rate_limiter,
        container.fallback_rate_limiter,
        container.market_cache,
        container.live_quotes,
    )


# --- Who trades what ----------------------------------------------------------------------


@pytest.fixture
async def market(factory: Factory) -> Market:
    """An OPEN market in which somebody holds `HELD` YES shares, bought at their price."""
    market = await factory.market()
    holder = await factory.user(balance_micro=HELD)
    await factory.position(holder, market, Outcome.YES, shares_micro=HELD)
    return market


@pytest.fixture
async def trader(factory: Factory, as_user: ActAs) -> User:
    """A user with the signup grant, who is the one calling."""
    user = await factory.user(balance_micro=SIGNUP_GRANT)
    as_user(user)
    return user


# --- What a trade on broken books leaves behind ---------------------------------------------


@pytest.mark.parametrize("invariant", list(BENT))
async def test_a_trade_on_broken_books_is_rolled_back_halts_the_market_and_alerts_the_admins(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    invariant: str,
) -> None:
    await break_books(owner_engine, market, invariant)
    bent = await stored(container, market)
    found = found_by_the_check(
        invariant, bent, lmsr_buy(bent), await market_balance(container, market)
    )
    before = await untouched(container, factory, market, trader)
    now = clock.advance(timedelta(minutes=3))
    key = uuid7()

    refused = await post_trade(client, market, key)

    # The trader is told to come back, and nothing of the trade stands.
    assert assert_error(refused, 503, "SERVICE_UNAVAILABLE") == {}
    assert refused.json()["message"] == ServiceUnavailableError.default_message
    assert await untouched(container, factory, market, trader) == before
    assert before.balances == (SIGNUP_GRANT,)
    # The market is halted by nobody in particular, on the record.
    assert (await stored(container, market)).status is MarketStatus.HALTED
    (halt,) = await rows(container, AuditLogEntry)
    assert (halt.actor_id, halt.action) == (None, AuditAction.MARKET_HALT)
    assert halt.target == AuditService.target(AuditTarget.MARKET, market.id)
    assert (halt.before, halt.after) == ({"status": "OPEN"}, {"status": "HALTED"})
    assert halt.created_at == now
    # The admins find one alert that says which check failed, on what, and for which trade.
    async with container.uow_factory() as uow:
        (alert,) = await uow.alerts.list_unacknowledged()
    assert (alert.severity, alert.message) == (
        AlertSeverity.CRITICAL,
        f"Invariant violated: {invariant}",
    )
    assert alert.context == alert_context(found, market, trader)
    assert (alert.created_at, alert.acked_by, alert.acked_at) == (now, None, None)

    # Sent again under its key, the trade meets a market that has stopped trading: the
    # breaker is not asked a second time.
    again = await post_trade(client, market, key)
    assert_error(again, 409, "MARKET_NOT_OPEN")
    assert await count_rows(container.uow_factory, Alert) == 1
    assert await count_rows(container.uow_factory, AuditLogEntry) == 1
    assert await untouched(container, factory, market, trader) == before


async def test_books_that_are_sound_trip_nothing(
    client: httpx.AsyncClient, container: Container, market: Market, trader: User
) -> None:
    """The same trade on the same market, with nothing bent."""
    response = await post_trade(client, market)

    assert response.status_code == 201, response.text
    assert (await stored(container, market)).status is MarketStatus.OPEN
    assert await rows(container, Alert) == []
    assert await rows(container, AuditLogEntry) == []
    await assert_books_balance(container.uow_factory)


async def test_a_halt_by_the_breaker_reaches_open_streams_and_drops_the_cached_lists(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    """What every lifecycle change does after its commit (A.4): open phones refetch a
    market that stopped trading, and no list shows it as open for another TTL."""
    cache = container.market_cache
    cached = MarketCache.list_key([MarketStatus.OPEN, MarketStatus.HALTED])
    await cache.set(cached, '{"items":[]}')
    await break_books(owner_engine, market, "positions_match_market")

    async with container.live_quotes.subscribe(market.id) as events:
        await next_event(events)
        now = clock.advance(timedelta(seconds=5))

        assert_error(await post_trade(client, market), 503, "SERVICE_UNAVAILABLE")
        published = await next_event(events)

    assert published == StatusEvent(
        type="status",
        market_id=market.id,
        status=MarketStatus.HALTED,
        trading_open=False,
        ts=now,
    )
    assert await cache.get(cached) is None


async def test_the_violation_is_logged_as_an_error(
    client: httpx.AsyncClient,
    market: Market,
    trader: User,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await break_books(owner_engine, market, "ledger_nets_to_zero")

    with caplog.at_level(logging.ERROR, logger=TRADING_LOGGER):
        assert_error(await post_trade(client, market), 503, "SERVICE_UNAVAILABLE")

    (record,) = [record for record in caplog.records if record.name == TRADING_LOGGER]
    assert record.levelno == logging.ERROR
    message = record.getMessage()
    assert str(market.id) in message
    assert "ledger_nets_to_zero" in message
    assert "total_balance_micro" in message


# --- A market is halted once -----------------------------------------------------------------


async def test_a_second_broken_trade_alerts_again_and_does_not_halt_the_market_again(
    container: Container,
    factory: Factory,
    market: Market,
    owner_engine: AsyncEngine,
) -> None:
    """Two trades at once on broken books. The second gets the market's row when the
    first was rolled back, before any breaker has halted anything, and runs into the
    same check. Both breakers then want the row at the same moment: the one that gets
    it halts the market and puts that on record, the other waits for the row, finds
    the market halted and only leaves its alert."""
    alice = await factory.user(balance_micro=SIGNUP_GRANT)
    bob = await factory.user(balance_micro=SIGNUP_GRANT)
    await break_books(owner_engine, market, "positions_match_market")
    before = await untouched(container, factory, market, alice, bob)
    # Neither breaker begins before both trades were rolled back, so both trades met
    # an open market, and the two breakers begin together.
    both_rolled_back = asyncio.Barrier(2)
    trading = trading_service(container, breaker_after(container, both_rolled_back.wait))
    request = TradeRequest.model_validate(BUY)

    async with asyncio.timeout(WAIT_SECONDS):
        results = await asyncio.gather(
            *(
                trading.trade(factory.identity(user), market.id, uuid7(), request)
                for user in (alice, bob)
            ),
            return_exceptions=True,
        )

    assert [type(result) for result in results] == [ServiceUnavailableError] * 2
    assert (await stored(container, market)).status is MarketStatus.HALTED
    (halt,) = await rows(container, AuditLogEntry)
    assert (halt.actor_id, halt.before, halt.after) == (
        None,
        {"status": "OPEN"},
        {"status": "HALTED"},
    )
    alerts = await rows(container, Alert)
    assert [alert.message for alert in alerts] == ["Invariant violated: positions_match_market"] * 2
    assert {alert.context["user_id"] for alert in alerts} == {str(alice.id), str(bob.id)}
    assert await untouched(container, factory, market, alice, bob) == before


async def test_a_market_somebody_else_took_out_of_open_is_left_as_it_is(
    container: Container,
    factory: Factory,
    market: Market,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    """An admin closes the market between the trade's rollback and the breaker's
    transaction. The breaker halts nothing then, so it records no halt; the alert is
    left all the same, and the market stays closed."""
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    await break_books(owner_engine, market, "positions_match_market")
    before = await untouched(container, factory, market, trader)

    async def an_admin_closes_the_market() -> None:
        await bend(
            owner_engine,
            ["UPDATE markets SET status = 'CLOSED', closed_at = :now WHERE id = :market_id"],
            market_id=market.id,
            now=clock.now(),
        )

    trading = trading_service(container, breaker_after(container, an_admin_closes_the_market))
    cached = MarketCache.list_key([MarketStatus.CLOSED])
    await container.market_cache.set(cached, '{"items":[]}')

    async with container.live_quotes.subscribe(market.id) as events:
        await next_event(events)
        with pytest.raises(ServiceUnavailableError):
            await trading.trade(
                factory.identity(trader), market.id, uuid7(), TradeRequest.model_validate(BUY)
            )
        published = await next_event(events)

    assert (await stored(container, market)).status is MarketStatus.CLOSED
    assert await rows(container, AuditLogEntry) == []
    (alert,) = await rows(container, Alert)
    assert (alert.severity, alert.message) == (
        AlertSeverity.CRITICAL,
        "Invariant violated: positions_match_market",
    )
    assert alert.context["user_id"] == str(trader.id)
    # The cache and the streams are told in either case, with the status as it is.
    assert isinstance(published, StatusEvent)
    assert (published.status, published.trading_open) == (MarketStatus.CLOSED, False)
    assert await container.market_cache.get(cached) is None
    assert await untouched(container, factory, market, trader) == before


# --- What is not the breaker's ----------------------------------------------------------------


async def test_a_constraint_postgres_refuses_is_a_bug_and_not_a_reason_to_halt(
    container: Container,
    factory: Factory,
    owner_engine: AsyncEngine,
) -> None:
    """The market's account was emptied behind the application's back, into
    `vis:mm_pnl`, so the ledger still nets to zero. A sell would take the account below
    zero: Postgres refuses the posting, the trade fails as an error nobody translates
    (500), and the breaker, which was never reached, halts nothing."""
    market = await factory.market()
    seller = await factory.user(balance_micro=HELD)
    await factory.position(seller, market, Outcome.YES, shares_micro=HELD)
    in_the_market = await market_balance(container, market)
    await bend(
        owner_engine,
        [
            "UPDATE accounts SET balance_micro = 0 WHERE market_id = :market_id",
            "UPDATE accounts SET balance_micro = balance_micro + :moved WHERE kind = 'VIS_MM_PNL'",
        ],
        market_id=market.id,
        moved=in_the_market,
    )
    before = await untouched(container, factory, market, seller)
    selling = TradeRequest(side=TradeSide.SELL, outcome=Outcome.YES, shares=to_cr(HELD))

    with pytest.raises(IntegrityError, match="ck_accounts_market_balance_non_negative"):
        await container.trading.trade(factory.identity(seller), market.id, uuid7(), selling)

    assert await untouched(container, factory, market, seller) == before
    assert (await stored(container, market)).status is MarketStatus.OPEN
    assert await rows(container, Alert) == []
    assert await rows(container, AuditLogEntry) == []
