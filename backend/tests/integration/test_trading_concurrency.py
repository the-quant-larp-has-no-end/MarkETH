"""Trades that arrive at the same time (SPEC §8, §12 "Concurrency", Appendix A.4).

The coroutines of one `asyncio.gather` run on one thread, and the contention is real
all the same: every trade holds a connection of its own, and Postgres decides who waits
for whom by the rows they lock. A trade locks the market's row first and the trader's
account second, so trades on one market run one after another on fresh quantities, and
trades of one trader never overdraw the account.

A trade here is sent the way the trade sheet sends it: when the market's row was not
free within `POSTGRES_LOCK_TIMEOUT_MS`, which the API answers with 503, it is sent
again under the same Idempotency-Key. On a quiet machine that never happens.
"""

import asyncio
from collections.abc import Sequence
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import select

from app.container import Container
from app.core.enums import JournalKind, Outcome, TradeSide
from app.core.errors import (
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    InsufficientSharesError,
    LimitExceededError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, format_cr, to_cr
from app.db import database
from app.db.base import Base
from app.models import (
    Alert,
    AuditLogEntry,
    IdempotencyKey,
    JournalEntry,
    Market,
    OutboxEvent,
    Position,
    Trade,
    User,
)
from app.pricing import lmsr
from app.schemas.markets import SellPortion
from app.schemas.trading import TradeRequest, TradeResponse
from tests.support.books import assert_books_balance, count_rows, replay
from tests.support.economy import PRECISION, STAKE_LIMIT
from tests.support.factories import ActAs, Factory
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

_SETTINGS = load_base_settings()

SIGNUP_GRANT: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what a trader of these tests starts with."""

MIN_TRADE_AMOUNT: Final[int] = _SETTINGS.pricing.min_trade_micro
"""MIN_TRADE_CR in µCR: the smallest trade that is sized by an amount."""

SMALLEST_PRESET: Final[int] = min(_SETTINGS.pricing.stake_presets_micro)
"""The smallest buy button of the trade sheet (TRADE_STAKE_PRESETS_CR), in µCR."""

MAX_ORDERS: Final[int] = _SETTINGS.risk.max_orders_per_minute
"""MAX_ORDERS_PER_MINUTE: trade requests one user may send per minute."""

PARALLEL_TRADES: Final[int] = 50
"""SPEC §12: "50 parallel trades on one market end consistent"."""

SELLERS: Final[int] = 20
BUYERS_OF_EACH_OUTCOME: Final[int] = 15
SELLER_HOLDING: Final[int] = 40 * MICRO
"""YES shares every seller of the fifty holds, of which each sells half."""

SAME_KEY_REQUESTS: Final[int] = 10
"""SPEC §12: "one Idempotency-Key sent 10 times gives one trade"."""

DEADLOCK_SECONDS: Final[float] = 60.0
"""How long trades that should all get their turn may take before a test gives up.
Postgres itself breaks a deadlock after a second, by failing one of the two."""


# --- Sending trades ----------------------------------------------------------------------


def buy(outcome: Outcome, amount_micro: int) -> TradeRequest:
    return TradeRequest(side=TradeSide.BUY, outcome=outcome, amount_cr=to_cr(amount_micro))


def sell(outcome: Outcome, shares_micro: int) -> TradeRequest:
    return TradeRequest(side=TradeSide.SELL, outcome=outcome, shares=to_cr(shares_micro))


async def trade(
    container: Container,
    who: AuthenticatedUser,
    market: Market,
    request: TradeRequest,
    key: UUID | None = None,
) -> TradeResponse:
    """Trade as a client does: a request that waited for the market's row longer than
    the lock timeout is sent again under the same key. Every other answer, a refusal
    included, is final."""
    key = key or uuid7()
    for _ in range(MAX_ORDERS):
        try:
            return await container.trading.trade(who, market.id, key, request)
        except database.ERROR_FAMILIES as error:
            if not database.is_lock_timeout(error):
                raise
    raise AssertionError(f"the row of market {market.id} was never free in time")


async def at_once(*trades: Any) -> list[Any]:
    """Run trades at the same time and return what each ended with: its answer, or the
    error that refused it."""
    async with asyncio.timeout(DEADLOCK_SECONDS):
        return await asyncio.gather(*trades, return_exceptions=True)


def answers(results: Sequence[Any]) -> list[TradeResponse]:
    """The results that are executed trades; anything that is no refusal fails here."""
    unexpected = [
        result
        for result in results
        if not isinstance(
            result,
            (
                TradeResponse,
                LimitExceededError,
                InsufficientBalanceError,
                InsufficientSharesError,
                IdempotencyKeyReusedError,
            ),
        )
    ]
    assert unexpected == []
    return [result for result in results if isinstance(result, TradeResponse)]


# --- What Postgres holds ---------------------------------------------------------------


def state_of(market: Market) -> lmsr.MarketState:
    return lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)


async def stored(container: Container, market: Market) -> Market:
    """The market's row as Postgres has it now."""
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    return row


async def trades_of(
    container: Container, market: Market, after_version: int = 0
) -> Sequence[Trade]:
    """The market's trades after `after_version`, in the order they happened."""
    async with container.uow_factory() as uow:
        return await uow.trades.list_for_market(market.id, limit=1000, after_version=after_version)


async def positions_of(container: Container, user: User, market: Market) -> list[Position]:
    async with container.uow_factory() as uow:
        return list(await uow.positions.list_for_user_market(user.id, market.id))


async def market_balance(container: Container, market: Market) -> int:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_market_account(market.id)
    assert account is not None
    return account.balance_micro


async def rows[ModelT: Base](container: Container, model: type[ModelT]) -> Sequence[ModelT]:
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(model))).all()


async def assert_the_breaker_tripped_for_nobody(container: Container) -> None:
    """No trade was taken for a broken book: no alert, and no market halted."""
    assert await rows(container, Alert) == []
    assert await rows(container, AuditLogEntry) == []


# --- SPEC §12: fifty parallel trades, one key ten times ------------------------------------


async def test_fifty_parallel_trades_on_one_market_end_consistent(
    container: Container, factory: Factory
) -> None:
    """Fifty traders at once, buying YES, buying NO and selling YES. Every trade stands
    exactly once, each was priced on what the one before it left, and nobody paid or
    received anything but the amount of their own trade."""
    view = container.market_view
    market = await factory.market()
    plan: list[tuple[User, TradeRequest]] = []
    for _ in range(SELLERS):
        seller = await factory.user(balance_micro=SELLER_HOLDING)
        await factory.position(seller, market, Outcome.YES, shares_micro=SELLER_HOLDING)
        half = next(
            preset
            for preset in view.sell_presets(
                await positions_of(container, seller, market), can_trade=True
            )
            if (preset.outcome, preset.portion) == (Outcome.YES, SellPortion.HALF)
        )
        assert half.allowed
        plan.append(
            (seller, TradeRequest(side=TradeSide.SELL, outcome=Outcome.YES, shares=half.shares))
        )
    for outcome in Outcome:
        for _ in range(BUYERS_OF_EACH_OUTCOME):
            buyer = await factory.user(balance_micro=SMALLEST_PRESET)
            plan.append((buyer, buy(outcome, SMALLEST_PRESET)))
    assert len(plan) == PARALLEL_TRADES
    before = await stored(container, market)
    balances = {user.id: await factory.balance_micro(user) for user, _ in plan}
    in_the_market = await market_balance(container, market)

    results = await at_once(
        *(trade(container, factory.identity(user), market, request) for user, request in plan)
    )

    executed = answers(results)
    assert len(executed) == PARALLEL_TRADES
    versions = list(range(before.version + 1, before.version + PARALLEL_TRADES + 1))
    assert sorted(answer.market_version for answer in executed) == versions
    after = await stored(container, market)
    trades = await trades_of(container, market, after_version=before.version)
    assert [made.market_version for made in trades] == versions
    assert {made.id for made in trades} == {answer.trade_id for answer in executed}
    # Priced again one after another, from the market as it was, the trades reproduce
    # to the micro-unit and end where the market is: none ran on stale quantities.
    assert replay(state_of(before), trades, precision=PRECISION) == state_of(after)
    assert (after.version, after.volume_micro) == (
        versions[-1],
        before.volume_micro + sum(made.amount_micro for made in trades),
    )
    # Everybody was charged or paid their own trade, and the market's account the sum.
    made_by = {made.user_id: made for made in trades}
    assert len(made_by) == PARALLEL_TRADES
    paid_in = 0
    for user, request in plan:
        made = made_by[user.id]
        assert (made.side, made.outcome) == (request.side, request.outcome)
        signed = made.amount_micro if made.side is TradeSide.BUY else -made.amount_micro
        assert await factory.balance_micro(user) == balances[user.id] - signed
        paid_in += signed
    assert await market_balance(container, market) == in_the_market + paid_in
    # One outbox row and one claimed key per trade.
    assert (
        sorted(event.payload["version"] for event in await rows(container, OutboxEvent)) == versions
    )
    assert await count_rows(container.uow_factory, IdempotencyKey) == PARALLEL_TRADES
    await assert_the_breaker_tripped_for_nobody(container)
    await assert_books_balance(container.uow_factory)


async def test_one_idempotency_key_sent_ten_times_at_once_gives_one_trade(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """Through the route, as ten taps on Confirm. Each of the ten counts against the
    rate limit, so the limit has to allow that many."""
    assert MAX_ORDERS >= SAME_KEY_REQUESTS
    market = await factory.market()
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    as_user(trader)
    key = uuid7()
    body = {"side": "BUY", "outcome": "YES", "amount_cr": format_cr(STAKE_LIMIT)}

    responses = await at_once(
        *(
            client.post(
                f"/api/markets/{market.id}/trades", json=body, headers={"Idempotency-Key": str(key)}
            )
            for _ in range(SAME_KEY_REQUESTS)
        )
    )

    assert [response.status_code for response in responses] == [201] * SAME_KEY_REQUESTS
    first = responses[0].json()
    assert all(response.json() == first for response in responses)
    (made,) = await trades_of(container, market)
    assert (str(made.id), made.user_id, made.amount_micro) == (
        first["trade_id"],
        trader.id,
        STAKE_LIMIT,
    )
    bought = [
        entry
        for entry in await rows(container, JournalEntry)
        if entry.kind is JournalKind.TRADE_BUY
    ]
    assert [entry.ref_id for entry in bought] == [made.id]
    assert await count_rows(container.uow_factory, OutboxEvent) == 1
    assert await count_rows(container.uow_factory, IdempotencyKey) == 1
    assert (await stored(container, market)).version == 1
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - STAKE_LIMIT
    await assert_the_breaker_tripped_for_nobody(container)
    await assert_books_balance(container.uow_factory)


async def test_one_key_sent_with_two_bodies_at_once_trades_one_of_them(
    container: Container, factory: Factory
) -> None:
    """Whichever claims the key first executes; the other waits for it and is then a
    reuse of the key (422)."""
    market = await factory.market()
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    who = factory.identity(trader)
    key = uuid7()
    requests = [buy(Outcome.YES, SMALLEST_PRESET), buy(Outcome.NO, SMALLEST_PRESET)]

    results = await at_once(*(trade(container, who, market, request, key) for request in requests))

    (executed,) = answers(results)
    assert [type(result) for result in results if result is not executed] == [
        IdempotencyKeyReusedError
    ]
    (made,) = await trades_of(container, market)
    assert (made.id, made.outcome) == (executed.trade_id, executed.outcome)
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - SMALLEST_PRESET
    await assert_books_balance(container.uow_factory)


# --- Locks: market row first, account second ----------------------------------------------


async def test_trades_of_one_user_on_two_markets_at_once_do_not_deadlock(
    container: Container, factory: Factory
) -> None:
    """Each holds the row of its own market and both want the one account. Neither
    holds the account while it asks for a market's row, so one simply goes first."""
    first, second = await factory.market(), await factory.market()
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    who = factory.identity(trader)
    rounds = (MAX_ORDERS - 2) // 2

    for _ in range(rounds):
        results = await at_once(
            trade(container, who, first, buy(Outcome.YES, MIN_TRADE_AMOUNT)),
            trade(container, who, second, buy(Outcome.NO, MIN_TRADE_AMOUNT)),
        )
        assert len(answers(results)) == 2

    for market in (first, second):
        made = await trades_of(container, market)
        assert [one.market_version for one in made] == list(range(1, rounds + 1))
        zero = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
        assert replay(zero, made, precision=PRECISION) == state_of(await stored(container, market))
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - 2 * rounds * MIN_TRADE_AMOUNT
    await assert_the_breaker_tripped_for_nobody(container)
    await assert_books_balance(container.uow_factory)


async def test_many_users_trading_several_markets_at_once_all_get_their_turn(
    container: Container, factory: Factory
) -> None:
    """Every user in every market at the same moment: trades of one market queue for
    its row, trades of one user for their account, and trades of different markets
    commit while another's books are being checked. Nobody deadlocks, and no check sees
    half of somebody else's trade."""
    markets = [await factory.market() for _ in range(3)]
    users = [await factory.user(balance_micro=SIGNUP_GRANT) for _ in range(8)]
    plan = [
        (user, market, buy(Outcome.YES if (i + j) % 2 else Outcome.NO, SMALLEST_PRESET))
        for i, user in enumerate(users)
        for j, market in enumerate(markets)
    ]
    assert len(markets) < MAX_ORDERS

    results = await at_once(
        *(
            trade(container, factory.identity(user), market, request)
            for user, market, request in plan
        )
    )

    assert len(answers(results)) == len(plan)
    for market in markets:
        made = await trades_of(container, market)
        assert [one.market_version for one in made] == list(range(1, len(users) + 1))
        assert {one.user_id for one in made} == {user.id for user in users}
        zero = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
        assert replay(zero, made, precision=PRECISION) == state_of(await stored(container, market))
    for user in users:
        assert await factory.balance_micro(user) == SIGNUP_GRANT - len(markets) * SMALLEST_PRESET
    await assert_the_breaker_tripped_for_nobody(container)
    await assert_books_balance(container.uow_factory)


# --- What a trader cannot do by being quick -----------------------------------------------


async def test_parallel_buys_of_one_user_in_one_market_cannot_pass_the_stake_limit_together(
    container: Container, factory: Factory
) -> None:
    """Five buys of the whole limit at once: the market's row lets them in one at a
    time, and each sees the stake the one before it left."""
    market = await factory.market()
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    who = factory.identity(trader)
    assert SIGNUP_GRANT >= 5 * STAKE_LIMIT, "the balance would cover all five"

    results = await at_once(
        *(trade(container, who, market, buy(Outcome.YES, STAKE_LIMIT)) for _ in range(5))
    )

    (executed,) = answers(results)
    refused = [result for result in results if result is not executed]
    assert [type(result) for result in refused] == [LimitExceededError] * 4
    view = container.market_view
    assert view.net_stake_micro(await positions_of(container, trader, market)) == STAKE_LIMIT
    assert executed.stake_left_cr == to_cr(0)
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - STAKE_LIMIT
    assert await count_rows(container.uow_factory, Trade) == 1
    await assert_books_balance(container.uow_factory)


async def test_parallel_buys_on_two_markets_cannot_spend_one_balance_twice(
    container: Container, factory: Factory
) -> None:
    """A balance that pays for one of two buys, sent to two markets at once: their rows
    do not order them, the account's lock does, and the second reads the balance the
    first left."""
    first, second = await factory.market(), await factory.market()
    trader = await factory.user(balance_micro=SMALLEST_PRESET)
    who = factory.identity(trader)

    results = await at_once(
        trade(container, who, first, buy(Outcome.YES, SMALLEST_PRESET)),
        trade(container, who, second, buy(Outcome.YES, SMALLEST_PRESET)),
    )

    (executed,) = answers(results)
    (refused,) = [result for result in results if result is not executed]
    assert isinstance(refused, InsufficientBalanceError)
    assert refused.details == {"balance_cr": to_cr(0), "required_cr": to_cr(SMALLEST_PRESET)}
    assert executed.balance_cr == to_cr(0)
    assert await factory.balance_micro(trader) == 0
    assert await count_rows(container.uow_factory, Trade) == 1
    traded = [(await stored(container, market)).version for market in (first, second)]
    assert sorted(traded) == [0, 1]
    await assert_books_balance(container.uow_factory)


async def test_parallel_sells_cannot_sell_the_same_shares_twice(
    container: Container, factory: Factory
) -> None:
    """Nobody writes a market's positions without holding its row, so the second sell
    reads the holding the first one emptied."""
    market = await factory.market()
    owned = 30 * MICRO
    seller = await factory.user(balance_micro=owned)
    await factory.position(seller, market, Outcome.YES, shares_micro=owned)
    who = factory.identity(seller)

    results = await at_once(
        *(trade(container, who, market, sell(Outcome.YES, owned)) for _ in range(3))
    )

    (executed,) = answers(results)
    refused = [result for result in results if result is not executed]
    assert [type(result) for result in refused] == [InsufficientSharesError] * 2
    (position,) = await positions_of(container, seller, market)
    assert (position.shares_micro, position.proceeds_micro) == (0, int(executed.amount_cr * MICRO))
    assert state_of(await stored(container, market)) == lmsr.MarketState(
        q_yes=0, q_no=0, b=market.b_micro
    )
    await assert_books_balance(container.uow_factory)
