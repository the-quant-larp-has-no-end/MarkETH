"""The trade transaction, rule by rule (SPEC §4, §8, §12, Appendix A.4).

Every trade here goes through `POST /api/markets/{id}/trades` or straight through
`TradingService.trade`, against real Postgres and Redis. What a trade should cost,
return and show is what `app.pricing.lmsr` gives for the market as it stood, shown the
way `MarketView` shows it; the limits are those of the settings.

A test that expects a refusal also expects that nothing was written: no trade, no
entry, no outbox row, and no Idempotency-Key that stays claimed.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Coroutine, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.enums import (
    JournalKind,
    MarketStatus,
    MarketTier,
    OutboxTopic,
    Outcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.errors import LimitExceededError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, format_cr, to_cr
from app.db.base import Base
from app.integrations.redis.market_cache import MarketCache
from app.integrations.scylla.quote_history import QuotePoint
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
from app.schemas.common import Limit
from app.schemas.live import QuoteEvent, StatusEvent
from app.schemas.markets import SellPortion
from app.schemas.trading import QuoteResponse, TradeRequest, TradeResponse
from app.services.ledger import LedgerRef, trade_key
from app.services.live_quotes import LiveEvent
from app.services.market_view import EVEN_ODDS_MICRO, MarketView
from app.services.outbox_events import quote_updated_payload
from app.services.trading import ORDER_WINDOW_SECONDS, OWN_MARKET_MESSAGE
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
from tests.support.economy import MIN_TRADE_SHARES, PRECISION, STAKE_LIMIT, TIER_B
from tests.support.factories import ActAs, Factory, PortalHeaders
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

_SETTINGS = load_base_settings()

SIGNUP_GRANT: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what the trader of these tests starts with."""

MIN_TRADE_AMOUNT: Final[int] = _SETTINGS.pricing.min_trade_micro
"""MIN_TRADE_CR in µCR: the smallest trade that is sized by an amount."""

SMALLEST_PRESET: Final[int] = min(_SETTINGS.pricing.stake_presets_micro)
"""The smallest buy button of the trade sheet (TRADE_STAKE_PRESETS_CR), in µCR."""

MAX_ORDERS: Final[int] = _SETTINGS.risk.max_orders_per_minute
"""MAX_ORDERS_PER_MINUTE: trade requests one user may send per minute."""

WAIT_SECONDS: Final[float] = 5.0
"""How long a test waits for what should happen at once, before it fails instead of
hanging."""

SHA256_HEX: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")

WRITTEN_TO: Final[tuple[type[Base], ...]] = (
    Trade,
    Position,
    JournalEntry,
    Posting,
    OutboxEvent,
    IdempotencyKey,
    Alert,
    AuditLogEntry,
)
"""Every table a trade, or the circuit breaker after one, writes to."""

type Body = dict[str, Any]
"""A trade request as JSON."""


# --- Requests ------------------------------------------------------------------------


def buy_for(outcome: Outcome, amount_micro: int, **more: Any) -> Body:
    """BUY sized by `amount_cr`. `more` holds a guard or a `quote_version`."""
    return {"side": "BUY", "outcome": outcome.value, "amount_cr": format_cr(amount_micro)} | more


def buy_shares(outcome: Outcome, shares_micro: int, **more: Any) -> Body:
    """BUY sized by `shares`."""
    return {"side": "BUY", "outcome": outcome.value, "shares": format_cr(shares_micro)} | more


def sell(outcome: Outcome, shares_micro: int, **more: Any) -> Body:
    """SELL sized by `shares`."""
    return {"side": "SELL", "outcome": outcome.value, "shares": format_cr(shares_micro)} | more


async def post_trade(
    client: httpx.AsyncClient, market_id: UUID, body: Body, key: UUID | None = None
) -> httpx.Response:
    """Send a trade as whoever `as_user` named, under `key` or under one of its own."""
    return await client.post(
        f"/api/markets/{market_id}/trades",
        json=body,
        headers={"Idempotency-Key": str(key or uuid7())},
    )


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is that error; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    details: dict[str, Any] = body["details"]
    return details


def executed(response: httpx.Response) -> TradeResponse:
    """The trade a 201 answers with."""
    assert response.status_code == 201, response.text
    return TradeResponse.model_validate(response.json())


# --- What lmsr and MarketView say ------------------------------------------------------


def state_of(market: Market) -> lmsr.MarketState:
    return lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)


def micro(amount: Decimal) -> int:
    """An amount of credits or shares in micro-units."""
    return int(amount * MICRO)


def lmsr_quote(market: Market, body: Body) -> lmsr.TradeQuote:
    """What `app.pricing.lmsr` gives for the trade of `body` on the quantities of
    `market`: the function of the request's mode, at the precision of the settings."""
    request = TradeRequest.model_validate(body)
    state = state_of(market)
    if request.amount_cr is not None:
        return lmsr.quote_buy_for_budget(
            state, request.outcome, micro(request.amount_cr), precision=PRECISION
        )
    assert request.shares is not None
    price = lmsr.quote_buy_shares if request.side is TradeSide.BUY else lmsr.quote_sell_shares
    return price(state, request.outcome, micro(request.shares), precision=PRECISION)


def shown(view: MarketView, market: Market, quote: lmsr.TradeQuote) -> QuoteResponse:
    """`quote` as the API shows it, in the figures `MarketView` makes of it."""
    p_yes_before, p_no_before = view.probabilities(quote.p_yes_before_micro)
    p_yes_after, p_no_after = view.probabilities(quote.p_yes_after_micro)
    return QuoteResponse(
        market_id=market.id,
        side=quote.side,
        outcome=quote.outcome,
        shares=to_cr(quote.shares_micro),
        amount_cr=to_cr(quote.amount_micro),
        average_price=view.average_price(quote.amount_micro, quote.shares_micro),
        p_yes_before=p_yes_before,
        p_no_before=p_no_before,
        p_yes_after=p_yes_after,
        p_no_after=p_no_after,
        quote_version=market.version,
    )


def trade_at(
    container: Container,
    before: Market,
    quote: lmsr.TradeQuote,
    *,
    trade_id: UUID,
    balance_micro: int,
    positions: Sequence[Position],
) -> TradeResponse:
    """The answer to a trade that executed at `quote` on the market as it was `before`.

    `balance_micro` is the trader's balance and `positions` are their rows in the
    market, both after the trade: the stake figures are `MarketView`'s for those rows.
    """
    view = container.market_view
    quoted = shown(view, before, quote)
    net_stake_micro = view.net_stake_micro(positions)
    return TradeResponse(
        trade_id=trade_id,
        market_id=before.id,
        side=quoted.side,
        outcome=quoted.outcome,
        shares=quoted.shares,
        amount_cr=quoted.amount_cr,
        average_price=quoted.average_price,
        p_yes_before=quoted.p_yes_before,
        p_no_before=quoted.p_no_before,
        p_yes_after=quoted.p_yes_after,
        p_no_after=quoted.p_no_after,
        market_version=before.version + 1,
        balance_cr=to_cr(balance_micro),
        net_stake_cr=to_cr(net_stake_micro),
        stake_left_cr=to_cr(view.stake_left_micro(net_stake_micro)),
        created_at=container.clock.now(),
    )


def lead_that_makes_the_smallest_sell_worthless(b_micro: int) -> int:
    """µshares YES has to lead NO by, in a market of `b_micro`, before the proceeds of
    selling `MIN_TRADE_SHARES` of NO round down to nothing (A.4)."""
    lead = b_micro
    while True:
        behind = lmsr.MarketState(q_yes=lead + MIN_TRADE_SHARES, q_no=MIN_TRADE_SHARES, b=b_micro)
        proceeds = lmsr.quote_sell_shares(behind, Outcome.NO, MIN_TRADE_SHARES, precision=PRECISION)
        if proceeds.amount_micro == 0:
            return lead
        lead *= 2


# --- What Postgres holds ---------------------------------------------------------------


async def stored(container: Container, market: Market) -> Market:
    """The market's row as Postgres has it now."""
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    return row


async def positions_of(container: Container, user: User, market: Market) -> list[Position]:
    """The user's positions in the market: at most one per outcome."""
    async with container.uow_factory() as uow:
        return list(await uow.positions.list_for_user_market(user.id, market.id))


async def held(container: Container, user: User, market: Market, outcome: Outcome) -> int:
    """µshares of `outcome` the user holds in the market."""
    for position in await positions_of(container, user, market):
        if position.outcome is outcome:
            return position.shares_micro
    return 0


async def rows[ModelT: Base](container: Container, model: type[ModelT]) -> Sequence[ModelT]:
    """Every row of a table."""
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(model))).all()


async def trades_of(container: Container, market: Market) -> Sequence[Trade]:
    """The market's trades in the order they happened."""
    async with container.uow_factory() as uow:
        return await uow.trades.list_for_market(market.id, limit=1000)


async def booked(container: Container, trade_id: UUID) -> tuple[JournalEntry, dict[UUID, int]]:
    """The journal entry of a trade and what it posted to each account."""
    async with container.uow_factory() as uow:
        entry = await uow.journal.get_entry_by_idempotency_key(trade_key(trade_id))
        assert entry is not None
        postings = await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
        return entry, {posting.account_id: posting.amount_micro for posting in postings}


async def account_ids(container: Container, user: User, market: Market) -> tuple[UUID, UUID]:
    """The ids of the user's account and of the market's."""
    async with container.uow_factory() as uow:
        of_user = await uow.accounts.get_user_account(user.id)
        of_market = await uow.accounts.get_market_account(market.id)
    assert of_user is not None
    assert of_market is not None
    return of_user.id, of_market.id


@dataclass(frozen=True, slots=True)
class Books:
    """What a refused trade must leave exactly as it was."""

    rows: dict[str, int]
    """How many rows each table of `WRITTEN_TO` holds."""

    market: tuple[int, int, int, int, MarketStatus]
    """The market's q of YES and of NO, its version, its volume and its stored status."""

    balance_micro: int
    """The trader's balance."""


async def books(container: Container, factory: Factory, market: Market, user: User) -> Books:
    row = await stored(container, market)
    return Books(
        rows={
            model.__tablename__: await count_rows(container.uow_factory, model)
            for model in WRITTEN_TO
        },
        market=(row.q_yes_micro, row.q_no_micro, row.version, row.volume_micro, row.status),
        balance_micro=await factory.balance_micro(user),
    )


# --- Waiting, and listening ------------------------------------------------------------


@asynccontextmanager
async def holding_the_row_of(container: Container, market: Market) -> AsyncIterator[None]:
    """Another transaction holds the market's row for as long as the block is open."""
    async with container.uow_factory() as holder:
        await holder.markets.get_for_update(market.id)
        yield


async def sent_and_waiting_for_a_lock[T](
    owner_engine: AsyncEngine, request: Coroutine[Any, Any, T]
) -> asyncio.Task[T]:
    """Start `request` and return once a statement of the database waits for a lock,
    which is the request waiting for the row somebody holds.

    The connection that watches is open before the request starts and commits after
    every look: Postgres shows a transaction the activity it saw first.
    """
    waiting = text(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with owner_engine.connect() as connection:
        watching = await connection.execution_options(isolation_level="AUTOCOMMIT")
        task = asyncio.create_task(request)
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                while True:
                    if await watching.scalar(waiting):
                        break
                    await asyncio.sleep(0.01)
        except BaseException:
            task.cancel()
            raise
    return task


async def next_event(events: AsyncIterator[LiveEvent]) -> LiveEvent:
    """The next event of a stream; a failure if none comes."""
    return await asyncio.wait_for(anext(events), timeout=WAIT_SECONDS)


def quote_event(container: Container, market: Market) -> QuoteEvent:
    """The quote event of `market` as it stands, in the numbers `MarketView` reports."""
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


# --- Who trades what -------------------------------------------------------------------


@pytest.fixture
async def market(factory: Factory) -> Market:
    """An OPEN market of the DEFAULT tier nobody has traded."""
    return await factory.market()


@pytest.fixture
async def trader(factory: Factory, as_user: ActAs) -> User:
    """A user with the signup grant, who is the one calling until a test says otherwise."""
    user = await factory.user(balance_micro=SIGNUP_GRANT)
    as_user(user)
    return user


async def someone_holding(
    factory: Factory, market: Market, outcome: Outcome, shares_micro: int
) -> User:
    """Another user, who holds `shares_micro` of `outcome`. A share costs less than one
    credit, so a balance of as many credits pays for them."""
    user = await factory.user(balance_micro=shares_micro)
    await factory.position(user, market, outcome, shares_micro=shares_micro)
    return user


# --- A trade executes at its quote -------------------------------------------------------


async def test_a_buy_confirms_at_the_quoted_cost_and_moves_the_price(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """The acceptance criterion of SPEC §12, with the numbers of the settings: a buy of
    YES for the whole stake limit, guarded by the quote the trader saw."""
    size = buy_for(Outcome.YES, STAKE_LIMIT)
    quote = (await client.get(f"/api/markets/{market.id}/quote", params=size)).json()

    response = await post_trade(
        client,
        market.id,
        size | {"min_shares": quote["shares"], "quote_version": quote["quote_version"]},
    )

    trade = response.json()
    assert response.status_code == 201, response.text
    for figure in (
        "market_id",
        "side",
        "outcome",
        "shares",
        "amount_cr",
        "average_price",
        "p_yes_before",
        "p_no_before",
        "p_yes_after",
        "p_no_after",
    ):
        assert trade[figure] == quote[figure], figure
    assert trade["market_version"] == quote["quote_version"] + 1
    # The cost is the budget, the shares are what lmsr sells for it, and the price moved
    # from even odds to where lmsr puts it.
    priced = lmsr_quote(market, size)
    view = container.market_view
    assert trade["amount_cr"] == format_cr(STAKE_LIMIT) == format_cr(priced.amount_micro)
    assert trade["shares"] == format_cr(priced.shares_micro)
    assert trade["p_yes_before"] == view.probabilities(EVEN_ODDS_MICRO)[0]
    assert trade["p_yes_after"] == view.probabilities(priced.p_yes_after_micro)[0]
    assert trade["p_yes_after"] > trade["p_yes_before"]
    assert trade["balance_cr"] == format_cr(SIGNUP_GRANT - STAKE_LIMIT)
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - STAKE_LIMIT
    await assert_books_balance(container.uow_factory)


async def test_a_trade_under_the_portal_s_headers_is_the_trade_of_the_user_they_name(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    portal_headers: PortalHeaders,
) -> None:
    """Nobody is acted for here: the request goes through the real seam, and whoever
    the two headers name is the one who pays."""
    alice = await factory.user(balance_micro=SIGNUP_GRANT)
    bob = await factory.user(balance_micro=SIGNUP_GRANT)
    path = f"/api/markets/{market.id}/trades"
    body = buy_for(Outcome.YES, SMALLEST_PRESET)

    unnamed = await client.post(path, json=body, headers={"Idempotency-Key": str(uuid7())})
    named = await client.post(
        path, json=body, headers=portal_headers(alice) | {"Idempotency-Key": str(uuid7())}
    )

    assert_error(unnamed, 401, "UNAUTHENTICATED")
    trade = executed(named)
    (row,) = await rows(container, Trade)
    assert (row.id, row.user_id) == (trade.trade_id, alice.id)
    assert await factory.balance_micro(alice) == SIGNUP_GRANT - SMALLEST_PRESET
    assert await factory.balance_micro(bob) == SIGNUP_GRANT
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "body",
    [
        buy_for(Outcome.YES, SMALLEST_PRESET),
        buy_for(Outcome.NO, 7_654_321),
        buy_shares(Outcome.YES, 30 * MICRO),
        buy_shares(Outcome.NO, 12_345_678),
        sell(Outcome.YES, 20 * MICRO),
        sell(Outcome.YES, 60 * MICRO),
    ],
    ids=["buy-amount", "buy-odd-amount", "buy-shares", "buy-odd-shares", "sell-some", "sell-all"],
)
async def test_a_trade_of_each_mode_executes_at_what_lmsr_quotes_for_the_market_as_it_stands(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    await someone_holding(factory, market, Outcome.NO, 45 * MICRO)
    await factory.position(trader, market, Outcome.YES, shares_micro=60 * MICRO)
    before = await stored(container, market)
    balance_before = await factory.balance_micro(trader)
    held_before = await held(container, trader, market, Outcome(body["outcome"]))
    quote = lmsr_quote(before, body)

    trade = executed(await post_trade(client, market.id, body))

    buying = quote.side is TradeSide.BUY
    balance_after = balance_before + (-quote.amount_micro if buying else quote.amount_micro)
    held_after = held_before + (quote.shares_micro if buying else -quote.shares_micro)
    positions = await positions_of(container, trader, market)
    assert trade == trade_at(
        container,
        before,
        quote,
        trade_id=trade.trade_id,
        balance_micro=balance_after,
        positions=positions,
    )
    assert await factory.balance_micro(trader) == balance_after
    assert await held(container, trader, market, quote.outcome) == held_after
    after = await stored(container, market)
    assert state_of(after) == quote.after
    assert (after.version, after.volume_micro) == (
        before.version + 1,
        before.volume_micro + quote.amount_micro,
    )
    await assert_books_balance(container.uow_factory)


async def test_selling_half_returns_what_lmsr_pays_for_those_shares_rounded_down(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """The acceptance criterion "selling half returns the exact reverse cost, rounded
    down": half is the sell button of the trade sheet, and the proceeds are never more
    than those shares cost."""
    bought = executed(await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT)))
    half = next(
        preset
        for preset in container.market_view.sell_presets(
            await positions_of(container, trader, market), can_trade=True
        )
        if (preset.outcome, preset.portion) == (Outcome.YES, SellPortion.HALF)
    )
    assert half.allowed
    before = await stored(container, market)
    proceeds = lmsr.quote_sell_shares(
        state_of(before), Outcome.YES, micro(half.shares), precision=PRECISION
    )

    sold = executed(await post_trade(client, market.id, sell(Outcome.YES, micro(half.shares))))

    assert (sold.shares, sold.amount_cr) == (half.shares, to_cr(proceeds.amount_micro))
    # Bought back at once, those shares cost what they just returned, or a micro-credit
    # more: nothing is made by going round.
    again = lmsr.quote_buy_shares(
        proceeds.after, Outcome.YES, micro(half.shares), precision=PRECISION
    )
    assert 0 <= again.amount_micro - proceeds.amount_micro <= 1
    assert await factory.balance_micro(trader) == (
        SIGNUP_GRANT - micro(bought.amount_cr) + proceeds.amount_micro
    )
    await assert_books_balance(container.uow_factory)


# --- What a trade writes -----------------------------------------------------------------


async def test_a_buy_writes_its_entry_position_trade_market_and_outbox_row_together(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    """SPEC §8 step 7, and the postings of A.8: the cost goes from the buyer to the
    market's account."""
    before = await stored(container, market)
    body = buy_for(Outcome.NO, SMALLEST_PRESET)
    quote = lmsr_quote(before, body)
    key = uuid7()
    now = clock.advance(timedelta(minutes=5))

    response = await post_trade(client, market.id, body, key)

    trade_id = executed(response).trade_id
    after = await stored(container, market)
    assert state_of(after) == quote.after
    assert (after.version, after.volume_micro) == (1, quote.amount_micro)

    (trade,) = await rows(container, Trade)
    assert (trade.id, trade.market_id, trade.user_id) == (trade_id, market.id, trader.id)
    assert (trade.side, trade.outcome) == (TradeSide.BUY, Outcome.NO)
    assert (trade.shares_micro, trade.amount_micro) == (quote.shares_micro, quote.amount_micro)
    assert (trade.p_yes_before_micro, trade.p_yes_after_micro) == (
        quote.p_yes_before_micro,
        quote.p_yes_after_micro,
    )
    assert (trade.market_version, trade.created_at) == (after.version, now)

    (position,) = await positions_of(container, trader, market)
    assert (position.outcome, position.shares_micro) == (Outcome.NO, quote.shares_micro)
    assert (position.cost_micro, position.proceeds_micro) == (quote.amount_micro, 0)

    entry, postings = await booked(container, trade_id)
    of_trader, of_market = await account_ids(container, trader, market)
    assert (entry.kind, entry.ref_type, entry.ref_id) == (
        JournalKind.TRADE_BUY,
        LedgerRef.TRADE,
        trade_id,
    )
    assert entry.created_at == now
    assert postings == {of_trader: -quote.amount_micro, of_market: quote.amount_micro}

    (event,) = await rows(container, OutboxEvent)
    point = QuotePoint(
        market_id=market.id,
        ts=now,
        version=after.version,
        p_yes_micro=quote.p_yes_after_micro,
        q_yes_micro=after.q_yes_micro,
        q_no_micro=after.q_no_micro,
        trade_id=trade_id,
    )
    assert (event.topic, event.payload) == (OutboxTopic.QUOTE_UPDATED, quote_updated_payload(point))
    assert (event.created_at, event.published_at) == (now, None)

    (claimed,) = await rows(container, IdempotencyKey)
    assert (claimed.user_id, claimed.key) == (trader.id, str(key))
    assert claimed.response_json == response.json()
    assert SHA256_HEX.fullmatch(claimed.request_hash)
    assert await rows(container, Alert) == []
    assert await rows(container, AuditLogEntry) == []
    await assert_books_balance(container.uow_factory)


async def test_a_sell_books_its_proceeds_from_the_market_s_account_to_the_seller(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    await factory.position(trader, market, Outcome.YES, shares_micro=60 * MICRO)
    (owned,) = await positions_of(container, trader, market)
    before = await stored(container, market)
    body = sell(Outcome.YES, 25 * MICRO)
    quote = lmsr_quote(before, body)

    trade_id = executed(await post_trade(client, market.id, body)).trade_id

    entry, postings = await booked(container, trade_id)
    of_trader, of_market = await account_ids(container, trader, market)
    assert entry.kind is JournalKind.TRADE_SELL
    assert postings == {of_market: -quote.amount_micro, of_trader: quote.amount_micro}
    (position,) = await positions_of(container, trader, market)
    assert position.shares_micro == owned.shares_micro - quote.shares_micro
    assert (position.cost_micro, position.proceeds_micro) == (owned.cost_micro, quote.amount_micro)
    after = await stored(container, market)
    assert state_of(after) == quote.after
    assert after.volume_micro == before.volume_micro + quote.amount_micro
    sold = (await trades_of(container, market))[-1]
    assert (sold.id, sold.side, sold.amount_micro) == (trade_id, TradeSide.SELL, quote.amount_micro)
    assert sold.market_version == after.version == before.version + 1
    await assert_books_balance(container.uow_factory)


async def test_every_trade_adds_to_the_running_sums_of_its_position_and_one_row_to_trades(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """Cost and proceeds only ever grow, per outcome: the portfolio, a void and the
    leaderboard read them, and the leaderboard counts the rows of `trades`."""
    script = [
        buy_for(Outcome.YES, 30 * MICRO),
        buy_for(Outcome.YES, 20 * MICRO),
        buy_for(Outcome.NO, 10 * MICRO),
        sell(Outcome.YES, 15 * MICRO),
        sell(Outcome.YES, 5 * MICRO),
    ]
    cost = dict.fromkeys(Outcome, 0)
    proceeds = dict.fromkeys(Outcome, 0)
    shares = dict.fromkeys(Outcome, 0)

    for body in script:
        quote = lmsr_quote(await stored(container, market), body)
        executed(await post_trade(client, market.id, body))
        if quote.side is TradeSide.BUY:
            cost[quote.outcome] += quote.amount_micro
            shares[quote.outcome] += quote.shares_micro
        else:
            proceeds[quote.outcome] += quote.amount_micro
            shares[quote.outcome] -= quote.shares_micro

    positions = {p.outcome: p for p in await positions_of(container, trader, market)}
    for outcome in Outcome:
        assert (
            positions[outcome].shares_micro,
            positions[outcome].cost_micro,
            positions[outcome].proceeds_micro,
        ) == (shares[outcome], cost[outcome], proceeds[outcome])
    trades = await trades_of(container, market)
    assert [trade.market_version for trade in trades] == [1, 2, 3, 4, 5]
    assert (await stored(container, market)).volume_micro == sum(t.amount_micro for t in trades)
    assert await count_rows(container.uow_factory, OutboxEvent) == len(script)
    await assert_books_balance(container.uow_factory)


# --- The stake limit (SPEC §4, §12) ------------------------------------------------------


async def test_a_buy_that_takes_the_net_stake_above_the_limit_is_refused_with_its_details(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """A buy up to exactly MAX_STAKE_PER_USER_MARKET passes; the smallest trade on top
    of it does not."""
    at_the_limit = executed(await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT)))
    assert (at_the_limit.net_stake_cr, at_the_limit.stake_left_cr) == (to_cr(STAKE_LIMIT), to_cr(0))
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))

    assert assert_error(refused, 409, "LIMIT_EXCEEDED") == {
        "limit": Limit.MAX_STAKE_PER_USER_MARKET.value,
        "stake_limit_cr": format_cr(STAKE_LIMIT),
        "stake_left_cr": format_cr(0),
    }
    assert await books(container, factory, market, trader) == before
    await assert_books_balance(container.uow_factory)


async def test_a_single_buy_above_the_limit_is_refused_and_says_how_much_stake_is_left(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    executed(await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET)))
    view = container.market_view
    stake_left = view.stake_left_micro(
        view.net_stake_micro(await positions_of(container, trader, market))
    )
    assert 0 < stake_left < STAKE_LIMIT
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, buy_for(Outcome.YES, stake_left + 1))

    assert assert_error(refused, 409, "LIMIT_EXCEEDED") == {
        "limit": "MAX_STAKE_PER_USER_MARKET",
        "stake_limit_cr": format_cr(STAKE_LIMIT),
        "stake_left_cr": format_cr(stake_left),
    }
    assert await books(container, factory, market, trader) == before
    # What the answer says is left is a buy that passes.
    filled = executed(await post_trade(client, market.id, buy_for(Outcome.YES, stake_left)))
    assert (filled.net_stake_cr, filled.stake_left_cr) == (to_cr(STAKE_LIMIT), to_cr(0))
    await assert_books_balance(container.uow_factory)


async def test_after_selling_a_trader_may_buy_again_up_to_the_limit(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """SPEC §12: sell then buy again up to the limit is allowed. Selling lowers the net
    stake by the proceeds, and exactly that much may be staked again."""
    view = container.market_view
    executed(await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT)))
    half = await held(container, trader, market, Outcome.YES) // 2

    sold = executed(await post_trade(client, market.id, sell(Outcome.YES, half)))

    positions = await positions_of(container, trader, market)
    stake_left = view.stake_left_micro(view.net_stake_micro(positions))
    assert sold.stake_left_cr == to_cr(stake_left) == sold.amount_cr
    assert 0 < stake_left < STAKE_LIMIT

    again = executed(await post_trade(client, market.id, buy_for(Outcome.YES, stake_left)))
    assert (again.net_stake_cr, again.stake_left_cr) == (to_cr(STAKE_LIMIT), to_cr(0))
    once_more = await post_trade(client, market.id, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))
    assert assert_error(once_more, 409, "LIMIT_EXCEEDED")["stake_left_cr"] == format_cr(0)
    await assert_books_balance(container.uow_factory)


async def test_the_stake_limit_counts_both_outcomes_of_a_market_together_and_each_market_alone(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    most = STAKE_LIMIT - SMALLEST_PRESET
    executed(await post_trade(client, market.id, buy_for(Outcome.YES, most)))

    on_the_other_side = await post_trade(
        client, market.id, buy_for(Outcome.NO, SMALLEST_PRESET + 1)
    )
    assert assert_error(on_the_other_side, 409, "LIMIT_EXCEEDED")["stake_left_cr"] == format_cr(
        SMALLEST_PRESET
    )
    rest = executed(await post_trade(client, market.id, buy_for(Outcome.NO, SMALLEST_PRESET)))
    assert rest.stake_left_cr == to_cr(0)

    elsewhere = await factory.market()
    fresh = executed(await post_trade(client, elsewhere.id, buy_for(Outcome.YES, STAKE_LIMIT)))
    assert fresh.net_stake_cr == to_cr(STAKE_LIMIT)
    await assert_books_balance(container.uow_factory)


async def test_a_buy_button_the_trade_sheet_offers_passes_and_one_greyed_out_for_the_stake_does_not(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """The buy buttons are `MarketView.stake_presets` of the stake that is left (A.11),
    and the trade agrees with them all the way to the limit: the smallest one offered
    is bought until none is, and each time the largest one greyed out is refused."""
    view = container.market_view
    presets = sorted(container.settings.pricing.stake_presets_micro)
    assert STAKE_LIMIT + presets[-1] <= SIGNUP_GRANT, "only the stake greys a button out here"
    assert 2 * (STAKE_LIMIT // presets[0]) < MAX_ORDERS, "or the rate limit would answer"

    while True:
        stake_left = view.stake_left_micro(
            view.net_stake_micro(await positions_of(container, trader, market))
        )
        buttons = view.stake_presets(
            stake_left, await factory.balance_micro(trader), can_trade=True
        )
        offered = [micro(button.amount_cr) for button in buttons if button.allowed]
        greyed_out = [micro(button.amount_cr) for button in buttons if not button.allowed]
        if greyed_out:
            refused = await post_trade(client, market.id, buy_for(Outcome.YES, max(greyed_out)))
            assert assert_error(refused, 409, "LIMIT_EXCEEDED")["stake_left_cr"] == format_cr(
                stake_left
            )
        if not offered:
            break
        bought = executed(await post_trade(client, market.id, buy_for(Outcome.YES, min(offered))))
        assert bought.stake_left_cr == to_cr(stake_left - min(offered))

    assert stake_left < presets[0]
    await assert_books_balance(container.uow_factory)


async def test_a_trader_at_the_limit_may_always_sell(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """Sells are never limited (SPEC §4)."""
    executed(await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT)))
    everything = await held(container, trader, market, Outcome.YES)

    sold = executed(await post_trade(client, market.id, sell(Outcome.YES, everything)))

    assert sold.shares == to_cr(everything)
    assert await held(container, trader, market, Outcome.YES) == 0
    await assert_books_balance(container.uow_factory)


async def test_the_limit_is_on_the_net_stake_after_the_buy_so_a_gain_taken_can_be_staked_too(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """A trader who sold for more than they paid has a net stake of zero and the whole
    limit left. The buy they may make is larger than that by their gain, because the
    net stake of SPEC §4 is cost minus proceeds after the trade."""
    view = container.market_view
    executed(await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET)))
    await someone_holding(factory, market, Outcome.YES, 600 * MICRO)
    everything = await held(container, trader, market, Outcome.YES)
    sold = executed(await post_trade(client, market.id, sell(Outcome.YES, everything)))
    gain = micro(sold.amount_cr) - SMALLEST_PRESET
    assert gain > 0, "the price rose between the buy and the sell"
    assert (sold.net_stake_cr, sold.stake_left_cr) == (to_cr(0), to_cr(STAKE_LIMIT))

    one_too_many = await post_trade(client, market.id, buy_for(Outcome.NO, STAKE_LIMIT + gain + 1))
    assert assert_error(one_too_many, 409, "LIMIT_EXCEEDED") == {
        "limit": "MAX_STAKE_PER_USER_MARKET",
        "stake_limit_cr": format_cr(STAKE_LIMIT),
        "stake_left_cr": format_cr(STAKE_LIMIT),
    }
    all_of_it = executed(
        await post_trade(client, market.id, buy_for(Outcome.NO, STAKE_LIMIT + gain))
    )

    positions = await positions_of(container, trader, market)
    assert view.net_stake_micro(positions) == STAKE_LIMIT
    assert (all_of_it.net_stake_cr, all_of_it.stake_left_cr) == (to_cr(STAKE_LIMIT), to_cr(0))
    await assert_books_balance(container.uow_factory)


stake_buys = st.tuples(
    st.just(TradeSide.BUY),
    st.sampled_from(Outcome),
    st.integers(min_value=MIN_TRADE_AMOUNT, max_value=STAKE_LIMIT + SMALLEST_PRESET),
)
stake_sells = st.tuples(
    st.just(TradeSide.SELL), st.sampled_from(Outcome), st.sampled_from(SellPortion)
)


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(steps=st.lists(stake_buys | stake_sells, min_size=1, max_size=MAX_ORDERS - 2))
async def test_whatever_a_trader_buys_and_sells_their_net_stake_never_passes_the_limit(
    container: Container,
    factory: Factory,
    steps: list[tuple[TradeSide, Outcome, Any]],
) -> None:
    """One trader alone in a market, buying any amount and selling half or all of a
    holding in any order: a buy executes exactly when the net stake after it is within
    the limit, and a refusal says what was left."""
    assert SIGNUP_GRANT >= 2 * STAKE_LIMIT + SMALLEST_PRESET, "the balance never decides here"
    view = container.market_view
    trader = await factory.user(balance_micro=SIGNUP_GRANT)
    who = factory.identity(trader)
    market = await factory.market()

    for side, outcome, size in steps:
        positions = await positions_of(container, trader, market)
        stake_before = view.net_stake_micro(positions)

        if side is TradeSide.SELL:
            preset = next(
                preset
                for preset in view.sell_presets(positions, can_trade=True)
                if (preset.outcome, preset.portion) == (outcome, size)
            )
            if not preset.allowed:
                continue
            selling = TradeRequest(side=side, outcome=outcome, shares=preset.shares)
            sold = await container.trading.trade(who, market.id, uuid7(), selling)
            assert sold.net_stake_cr <= to_cr(stake_before)
            continue

        buying = TradeRequest(side=side, outcome=outcome, amount_cr=to_cr(size))
        if stake_before + size > view.stake_limit_micro:
            with pytest.raises(LimitExceededError) as refused:
                await container.trading.trade(who, market.id, uuid7(), buying)
            assert refused.value.details == {
                "limit": Limit.MAX_STAKE_PER_USER_MARKET,
                "stake_limit_cr": to_cr(view.stake_limit_micro),
                "stake_left_cr": to_cr(view.stake_left_micro(stake_before)),
            }
            continue
        bought = await container.trading.trade(who, market.id, uuid7(), buying)
        stake_after = view.net_stake_micro(await positions_of(container, trader, market))
        assert stake_after <= view.stake_limit_micro
        assert (bought.net_stake_cr, bought.stake_left_cr) == (
            to_cr(stake_after),
            to_cr(view.stake_left_micro(stake_after)),
        )

    await assert_books_balance(container.uow_factory)


# --- Balance and holdings ------------------------------------------------------------------


async def test_a_buy_the_balance_does_not_cover_is_insufficient_balance_with_both_amounts(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    as_user: ActAs,
) -> None:
    balance = SMALLEST_PRESET - MICRO
    poor = await factory.user(balance_micro=balance)
    as_user(poor)
    before = await books(container, factory, market, poor)

    refused = await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET))

    assert assert_error(refused, 409, "INSUFFICIENT_BALANCE") == {
        "balance_cr": format_cr(balance),
        "required_cr": format_cr(SMALLEST_PRESET),
    }
    assert await books(container, factory, market, poor) == before
    # A buy by shares is refused for its cost, which lmsr rounds up.
    cost = lmsr_quote(market, buy_shares(Outcome.YES, 2 * balance)).amount_micro
    assert cost > balance
    by_shares = await post_trade(client, market.id, buy_shares(Outcome.YES, 2 * balance))
    assert assert_error(by_shares, 409, "INSUFFICIENT_BALANCE") == {
        "balance_cr": format_cr(balance),
        "required_cr": format_cr(cost),
    }
    await assert_books_balance(container.uow_factory)


async def test_a_buy_for_the_whole_balance_leaves_it_at_zero(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    as_user: ActAs,
) -> None:
    spender = await factory.user(balance_micro=SMALLEST_PRESET)
    as_user(spender)

    bought = executed(await post_trade(client, market.id, buy_for(Outcome.NO, SMALLEST_PRESET)))

    assert bought.balance_cr == to_cr(0)
    assert await factory.balance_micro(spender) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_sell_of_more_shares_than_the_seller_holds_is_insufficient_shares(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """No short positions (SPEC §4): one micro-share more than the holding is refused,
    the holding itself sells."""
    owned = 60 * MICRO
    await factory.position(trader, market, Outcome.YES, shares_micro=owned)
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, sell(Outcome.YES, owned + 1))

    assert assert_error(refused, 409, "INSUFFICIENT_SHARES") == {}
    assert await books(container, factory, market, trader) == before
    sold = executed(await post_trade(client, market.id, sell(Outcome.YES, owned)))
    assert sold.shares == to_cr(owned)
    await assert_books_balance(container.uow_factory)


async def test_shares_other_people_hold_cannot_be_sold(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """The market has sold plenty of both outcomes to somebody else, so it could take
    these sells: what refuses them is the seller's own position, which holds no NO
    share and fewer YES shares than asked for."""
    owned = 50 * MICRO
    await someone_holding(factory, market, Outcome.NO, 80 * MICRO)
    await someone_holding(factory, market, Outcome.YES, 80 * MICRO)
    await factory.position(trader, market, Outcome.YES, shares_micro=owned)
    before = await books(container, factory, market, trader)

    for body in (sell(Outcome.NO, 10 * MICRO), sell(Outcome.YES, owned + 1)):
        assert (
            assert_error(await post_trade(client, market.id, body), 409, "INSUFFICIENT_SHARES")
            == {}
        )

    assert await books(container, factory, market, trader) == before
    executed(await post_trade(client, market.id, sell(Outcome.YES, owned)))
    assert await held(container, trader, market, Outcome.YES) == 0
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "body",
    [
        buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1),
        buy_shares(Outcome.YES, MIN_TRADE_SHARES - 1),
        sell(Outcome.YES, MIN_TRADE_SHARES - 1),
    ],
    ids=["buy-by-amount", "buy-by-shares", "sell"],
)
async def test_a_trade_below_the_smallest_size_of_its_mode_is_trade_too_small(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    await factory.position(trader, market, Outcome.YES, shares_micro=10 * MICRO)
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, body)

    assert assert_error(refused, 422, "TRADE_TOO_SMALL") == {}
    assert await books(container, factory, market, trader) == before
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "body",
    [
        buy_for(Outcome.YES, MIN_TRADE_AMOUNT),
        buy_shares(Outcome.YES, MIN_TRADE_SHARES),
        sell(Outcome.YES, MIN_TRADE_SHARES),
    ],
    ids=["buy-by-amount", "buy-by-shares", "sell"],
)
async def test_the_smallest_trade_of_each_mode_executes(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    await factory.position(trader, market, Outcome.YES, shares_micro=10 * MICRO)
    quote = lmsr_quote(await stored(container, market), body)

    trade = executed(await post_trade(client, market.id, body))

    assert (trade.shares, trade.amount_cr) == (to_cr(quote.shares_micro), to_cr(quote.amount_micro))
    await assert_books_balance(container.uow_factory)


async def test_a_sell_that_brings_in_nothing_executes_without_a_journal_entry(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    clock: FrozenClock,
) -> None:
    """A.4: shares priced below a millionth of a credit sell for 0. Selling is always
    allowed, so everything a trade writes is written, except an entry, which would have
    no postings (A.8)."""
    tight = await factory.market(tier=MarketTier.TIGHT)
    seller = await someone_holding(factory, tight, Outcome.NO, MIN_TRADE_SHARES)
    lead = lead_that_makes_the_smallest_sell_worthless(TIER_B[MarketTier.TIGHT])
    await someone_holding(factory, tight, Outcome.YES, lead + MIN_TRADE_SHARES)
    before = await stored(container, tight)
    balance = await factory.balance_micro(seller)
    entries = await count_rows(container.uow_factory, JournalEntry)
    body = sell(Outcome.NO, MIN_TRADE_SHARES)
    quote = lmsr_quote(before, body)
    assert quote.amount_micro == 0
    as_user(seller)
    now = clock.advance(timedelta(hours=1))

    sold = executed(await post_trade(client, tight.id, body))

    (position,) = await positions_of(container, seller, tight)
    assert sold == trade_at(
        container,
        before,
        quote,
        trade_id=sold.trade_id,
        balance_micro=balance,
        positions=[position],
    )
    assert (sold.amount_cr, sold.average_price, sold.balance_cr) == (to_cr(0), 0.0, to_cr(balance))
    assert await factory.balance_micro(seller) == balance
    assert await count_rows(container.uow_factory, JournalEntry) == entries
    assert (position.shares_micro, position.proceeds_micro) == (0, 0)
    after = await stored(container, tight)
    assert state_of(after) == quote.after
    assert (after.q_no_micro, after.version, after.volume_micro) == (
        0,
        before.version + 1,
        before.volume_micro,
    )
    trade = (await trades_of(container, tight))[-1]
    assert (trade.id, trade.side, trade.shares_micro, trade.amount_micro) == (
        sold.trade_id,
        TradeSide.SELL,
        MIN_TRADE_SHARES,
        0,
    )
    assert (trade.market_version, trade.created_at) == (after.version, now)
    (event,) = await rows(container, OutboxEvent)
    assert (event.payload["version"], event.payload["trade_id"]) == (after.version, str(trade.id))
    await assert_books_balance(container.uow_factory)


# --- The slippage guards (A.4) ---------------------------------------------------------------


def with_a_guard_at(body: Body, quote: lmsr.TradeQuote, *, worse_by: int) -> Body:
    """`body` with the guard of its mode set to what `quote` gives, or tightened past
    it by `worse_by` micro-units, so that the quote falls short of it."""
    if "amount_cr" in body:
        return body | {"min_shares": format_cr(quote.shares_micro + worse_by)}
    if body["side"] == "BUY":
        return body | {"max_cost_cr": format_cr(quote.amount_micro - worse_by)}
    return body | {"min_proceeds_cr": format_cr(quote.amount_micro + worse_by)}


GUARDED: Final[list[Any]] = [
    pytest.param(buy_for(Outcome.YES, SMALLEST_PRESET), id="buy-by-amount-min-shares"),
    pytest.param(buy_shares(Outcome.YES, 30 * MICRO), id="buy-by-shares-max-cost"),
    pytest.param(sell(Outcome.YES, 20 * MICRO), id="sell-min-proceeds"),
]
"""The three request modes, each of which has its own guard."""


@pytest.mark.parametrize("body", GUARDED)
async def test_a_guard_the_quote_meets_exactly_lets_the_trade_through(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    await factory.position(trader, market, Outcome.YES, shares_micro=40 * MICRO)
    quote = lmsr_quote(await stored(container, market), body)

    trade = executed(await post_trade(client, market.id, with_a_guard_at(body, quote, worse_by=0)))

    assert (trade.shares, trade.amount_cr) == (to_cr(quote.shares_micro), to_cr(quote.amount_micro))
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("body", GUARDED)
async def test_a_guard_the_quote_misses_by_one_micro_unit_is_quote_stale_with_a_fresh_quote(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    await factory.position(trader, market, Outcome.YES, shares_micro=40 * MICRO)
    current = await stored(container, market)
    quote = lmsr_quote(current, body)
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, with_a_guard_at(body, quote, worse_by=1))

    details = assert_error(refused, 409, "QUOTE_STALE")
    fresh = shown(container.market_view, current, quote)
    assert details == {"quote": fresh.model_dump(mode="json")}
    assert QuoteResponse.model_validate(details["quote"]) == fresh
    assert await books(container, factory, market, trader) == before
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("body", GUARDED)
async def test_a_trade_guarded_by_a_quote_somebody_traded_past_gets_the_quote_of_the_market_now(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    body: Body,
) -> None:
    """What the trade sheet lives through: it shows a quote, somebody else trades, and
    Confirm is answered with the quote to show instead, the one `GET /quote` gives."""
    await factory.position(trader, market, Outcome.YES, shares_micro=40 * MICRO)
    seen = lmsr_quote(await stored(container, market), body)
    guarded = with_a_guard_at(body, seen, worse_by=0) | {"quote_version": market.version}
    # The other trade moves the price against this one: up for a buy of YES, down for a sell.
    against = Outcome.YES if body["side"] == "BUY" else Outcome.NO
    await someone_holding(factory, market, against, 50 * MICRO)
    key = uuid7()

    refused = await post_trade(client, market.id, guarded, key)

    details = assert_error(refused, 409, "QUOTE_STALE")
    asked = await client.get(f"/api/markets/{market.id}/quote", params=body)
    now = await stored(container, market)
    assert details["quote"] == asked.json()
    assert details["quote"] == shown(container.market_view, now, lmsr_quote(now, body)).model_dump(
        mode="json"
    )
    assert details["quote"]["quote_version"] == now.version == guarded["quote_version"] + 1
    # Confirming the quote of now under the same key executes at it.
    again = with_a_guard_at(body, lmsr_quote(now, body), worse_by=0)
    confirmed = executed(await post_trade(client, market.id, again, key))
    assert confirmed.shares == to_cr(lmsr_quote(now, body).shares_micro)
    assert confirmed.amount_cr == to_cr(lmsr_quote(now, body).amount_micro)
    await assert_books_balance(container.uow_factory)


async def test_the_quote_version_of_a_request_decides_nothing(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """A.4: `quote_version` is informational; only a guard refuses a moved price."""
    await someone_holding(factory, market, Outcome.YES, 50 * MICRO)
    current = await stored(container, market)
    outdated = buy_for(Outcome.YES, SMALLEST_PRESET, quote_version=0)
    assert current.version > outdated["quote_version"]

    trade = executed(await post_trade(client, market.id, outdated))

    assert trade.shares == to_cr(lmsr_quote(current, outdated).shares_micro)
    await assert_books_balance(container.uow_factory)


# --- A market that does not trade (SPEC §6, §12) -------------------------------------------------


@pytest.mark.parametrize("status", [MarketStatus.HALTED, MarketStatus.CLOSED])
async def test_trading_a_halted_or_closed_market_is_market_not_open_for_buyers_and_holders(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    trader: User,
    status: MarketStatus,
) -> None:
    """Somebody who holds shares cannot sell them either: no trading means none."""
    stopped = await factory.market(status=status)
    await factory.position(trader, stopped, Outcome.YES, shares_micro=20 * MICRO)
    before = await books(container, factory, stopped, trader)

    for body in (buy_for(Outcome.YES, SMALLEST_PRESET), sell(Outcome.YES, 10 * MICRO)):
        assert (
            assert_error(await post_trade(client, stopped.id, body), 409, "MARKET_NOT_OPEN") == {}
        )

    assert await books(container, factory, stopped, trader) == before
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("status", [MarketStatus.RESOLVED, MarketStatus.VOID])
async def test_trading_a_settled_market_is_market_not_open(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    trader: User,
    status: MarketStatus,
) -> None:
    settled = await factory.market(status=status)
    before = await books(container, factory, settled, trader)

    refused = await post_trade(client, settled.id, buy_for(Outcome.YES, SMALLEST_PRESET))

    assert_error(refused, 409, "MARKET_NOT_OPEN")
    assert await books(container, factory, settled, trader) == before
    await assert_books_balance(container.uow_factory)


async def test_an_open_market_trades_until_its_deadline_and_not_from_then_on(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    """A.4: nothing flips the column at `close_at`; `now < close_at` is checked with
    every trade. The market stays stored OPEN and is closed to buys and sells alike."""
    clock.set(market.close_at - timedelta(microseconds=1))
    last = executed(await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET)))
    assert last.created_at == clock.now()
    before = await books(container, factory, market, trader)

    clock.set(market.close_at)
    for body in (buy_for(Outcome.YES, SMALLEST_PRESET), sell(Outcome.YES, MIN_TRADE_SHARES)):
        assert_error(await post_trade(client, market.id, body), 409, "MARKET_NOT_OPEN")

    assert (await stored(container, market)).status is MarketStatus.OPEN
    assert await books(container, factory, market, trader) == before
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "hidden",
    [None, MarketStatus.PROPOSED, MarketStatus.REJECTED],
    ids=["unknown", "proposed", "rejected"],
)
async def test_a_market_that_is_unknown_proposed_or_rejected_is_not_found(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    trader: User,
    as_user: ActAs,
    hidden: MarketStatus | None,
) -> None:
    """Also for its proposer: there is nothing to trade, so nobody is told more."""
    proposer = await factory.user(balance_micro=SIGNUP_GRANT)
    market_id = (
        uuid7() if hidden is None else (await factory.market(status=hidden, created_by=proposer)).id
    )

    for who in (trader, proposer):
        as_user(who)
        refused = await post_trade(client, market_id, buy_for(Outcome.YES, SMALLEST_PRESET))
        assert assert_error(refused, 404, "NOT_FOUND") == {}

    assert await count_rows(container.uow_factory, IdempotencyKey) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_trade_that_waited_for_the_market_s_row_until_the_deadline_passed_is_refused(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    """The clock is read once the row is locked: what counts is when the trade's turn
    came, not when its request arrived."""
    before = await books(container, factory, market, trader)

    async with holding_the_row_of(container, market):
        waiting = await sent_and_waiting_for_a_lock(
            owner_engine, post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET))
        )
        clock.set(market.close_at)
    refused = await waiting

    assert_error(refused, 409, "MARKET_NOT_OPEN")
    assert await books(container, factory, market, trader) == before
    await assert_books_balance(container.uow_factory)


async def test_a_trade_is_dated_when_its_turn_came_not_when_its_request_arrived(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    """The instant read under the lock is the trade's everywhere, so the timestamps of
    a market's trades never run against their versions."""
    arrived = clock.now()

    async with holding_the_row_of(container, market):
        waiting = await sent_and_waiting_for_a_lock(
            owner_engine, post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET))
        )
        turn = clock.advance(timedelta(seconds=30))
    trade = executed(await waiting)

    assert trade.created_at == turn > arrived
    (row,) = await rows(container, Trade)
    entry, _ = await booked(container, trade.trade_id)
    (event,) = await rows(container, OutboxEvent)
    assert (row.created_at, entry.created_at, event.created_at) == (turn, turn, turn)
    assert datetime.fromisoformat(event.payload["ts"]) == turn
    await assert_books_balance(container.uow_factory)


# --- Self-dealing (A.4) ------------------------------------------------------------------


async def test_the_proposer_of_a_market_may_not_trade_it(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    trader: User,
    as_user: ActAs,
) -> None:
    own = await factory.market(created_by=trader)
    # The factory applies no rule, so the proposer can be made to hold shares here.
    await factory.position(trader, own, Outcome.YES, shares_micro=20 * MICRO)
    before = await books(container, factory, own, trader)

    for body in (buy_for(Outcome.NO, SMALLEST_PRESET), sell(Outcome.YES, 10 * MICRO)):
        refused = await post_trade(client, own.id, body)
        assert assert_error(refused, 403, "FORBIDDEN") == {}
        assert refused.json()["message"] == OWN_MARKET_MESSAGE

    assert await books(container, factory, own, trader) == before
    # Everybody else trades it.
    as_user(await factory.user(balance_micro=SIGNUP_GRANT))
    executed(await post_trade(client, own.id, buy_for(Outcome.NO, SMALLEST_PRESET)))
    await assert_books_balance(container.uow_factory)


# --- Which refusal comes first ---------------------------------------------------------------


async def test_an_answer_names_the_first_thing_that_is_wrong(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    as_user: ActAs,
) -> None:
    """In the order of the steps of SPEC §8: an unknown market, self-dealing, a market
    that does not trade, shares not held, a size too small, a broken guard, the stake
    limit, the balance."""
    poor = await factory.user(balance_micro=MIN_TRADE_AMOUNT)
    as_user(poor)
    own_halted = await factory.market(status=MarketStatus.HALTED, created_by=poor)
    hidden = await factory.market(status=MarketStatus.PROPOSED, created_by=poor)
    halted = await factory.market(status=MarketStatus.HALTED)
    a_guard_no_quote_meets = {"min_shares": format_cr(10 * STAKE_LIMIT)}
    too_small = MIN_TRADE_AMOUNT - 1

    ladder = [
        (hidden, buy_for(Outcome.YES, too_small), 404, "NOT_FOUND"),
        (own_halted, sell(Outcome.YES, MIN_TRADE_SHARES - 1), 403, "FORBIDDEN"),
        (halted, sell(Outcome.YES, MIN_TRADE_SHARES - 1), 409, "MARKET_NOT_OPEN"),
        (market, sell(Outcome.YES, MIN_TRADE_SHARES - 1), 409, "INSUFFICIENT_SHARES"),
        (market, buy_for(Outcome.YES, too_small, **a_guard_no_quote_meets), 422, "TRADE_TOO_SMALL"),
        (
            market,
            buy_for(Outcome.YES, 2 * STAKE_LIMIT, **a_guard_no_quote_meets),
            409,
            "QUOTE_STALE",
        ),
        (market, buy_for(Outcome.YES, 2 * STAKE_LIMIT), 409, "LIMIT_EXCEEDED"),
        (market, buy_for(Outcome.YES, STAKE_LIMIT), 409, "INSUFFICIENT_BALANCE"),
    ]
    assert len(ladder) < MAX_ORDERS, "or the rate limit would answer first"

    for traded, body, status_code, code in ladder:
        assert_error(await post_trade(client, traded.id, body), status_code, code)

    assert await count_rows(container.uow_factory, Trade) == 0
    assert await count_rows(container.uow_factory, IdempotencyKey) == 0
    await assert_books_balance(container.uow_factory)


# --- Idempotency (SPEC §8 step 2, A.4) -----------------------------------------------------


async def test_the_same_key_and_body_returns_the_stored_trade_and_trades_nothing(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    """A double tap on Confirm creates exactly one trade (SPEC §12)."""
    key = uuid7()
    body = buy_for(Outcome.YES, SMALLEST_PRESET)
    first = await post_trade(client, market.id, body, key)
    executed(first)
    before = await books(container, factory, market, trader)

    clock.advance(timedelta(seconds=20))
    replays = [await post_trade(client, market.id, body, key) for _ in range(3)]

    for replay in replays:
        assert replay.status_code == 201
        assert replay.json() == first.json()
    assert await books(container, factory, market, trader) == before
    assert before.rows[Trade.__tablename__] == 1
    assert before.balance_micro == SIGNUP_GRANT - SMALLEST_PRESET
    await assert_books_balance(container.uow_factory)


async def test_the_same_amount_written_another_way_is_the_same_request(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """A.4: the hash is of the request as the API writes it, and that writes an amount
    in one way only."""
    key = uuid7()
    body = buy_for(Outcome.YES, SMALLEST_PRESET)
    first = await post_trade(client, market.id, body, key)
    executed(first)
    canonical = body["amount_cr"]
    spellings = ["0" + canonical, str(to_cr(SMALLEST_PRESET)), SMALLEST_PRESET // MICRO]
    assert canonical not in spellings

    for spelling in spellings:
        replay = await post_trade(client, market.id, body | {"amount_cr": spelling}, key)
        assert (replay.status_code, replay.json()) == (201, first.json())

    assert await count_rows(container.uow_factory, Trade) == 1
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "other",
    [
        buy_for(Outcome.YES, SMALLEST_PRESET + 1),
        buy_for(Outcome.NO, SMALLEST_PRESET),
        buy_shares(Outcome.YES, SMALLEST_PRESET),
        buy_for(Outcome.YES, SMALLEST_PRESET, min_shares=format_cr(MIN_TRADE_SHARES)),
        buy_for(Outcome.YES, SMALLEST_PRESET, quote_version=0),
    ],
    ids=["another-amount", "another-outcome", "another-mode", "a-guard-added", "a-version-added"],
)
async def test_the_same_key_with_another_body_is_idempotency_key_reused(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    other: Body,
) -> None:
    key = uuid7()
    first = await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET), key)
    executed(first)
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, other, key)

    assert assert_error(refused, 422, "IDEMPOTENCY_KEY_REUSED") == {}
    assert await books(container, factory, market, trader) == before
    # The key still answers the request it belongs to.
    replay = await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET), key)
    assert replay.json() == first.json()
    await assert_books_balance(container.uow_factory)


async def test_the_same_key_and_body_for_another_market_is_another_request(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """The request hash covers the market, also one that does not exist: a key is not
    replayed onto a market its trade was not made in."""
    key = uuid7()
    body = buy_for(Outcome.YES, SMALLEST_PRESET)
    executed(await post_trade(client, market.id, body, key))
    elsewhere = await factory.market()

    for market_id in (elsewhere.id, uuid7()):
        assert_error(await post_trade(client, market_id, body, key), 422, "IDEMPOTENCY_KEY_REUSED")

    assert await count_rows(container.uow_factory, Trade) == 1
    assert (await stored(container, elsewhere)).version == 0
    await assert_books_balance(container.uow_factory)


async def test_a_key_is_one_user_s_and_says_nothing_to_another(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    as_user: ActAs,
) -> None:
    key = uuid7()
    body = buy_for(Outcome.YES, SMALLEST_PRESET)
    first = executed(await post_trade(client, market.id, body, key))
    other = await factory.user(balance_micro=SIGNUP_GRANT)
    as_user(other)

    second = executed(await post_trade(client, market.id, body, key))

    assert second.trade_id != first.trade_id
    assert (first.market_version, second.market_version) == (1, 2)
    assert await factory.balance_micro(other) == SIGNUP_GRANT - SMALLEST_PRESET
    await assert_books_balance(container.uow_factory)


async def test_a_trade_that_fails_frees_its_key(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
) -> None:
    """A.4: the claim is rolled back with everything else, so the same key may carry
    the corrected request, which is neither a reuse nor a replay of the refusal."""
    key = uuid7()
    too_much = buy_for(Outcome.YES, STAKE_LIMIT + 1)

    for _ in range(2):
        assert_error(await post_trade(client, market.id, too_much, key), 409, "LIMIT_EXCEEDED")
    assert await count_rows(container.uow_factory, IdempotencyKey) == 0

    corrected = executed(
        await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT), key)
    )

    assert corrected.amount_cr == to_cr(STAKE_LIMIT)
    (claimed,) = await rows(container, IdempotencyKey)
    assert (claimed.user_id, claimed.key) == (trader.id, str(key))
    assert await factory.balance_micro(trader) == SIGNUP_GRANT - STAKE_LIMIT
    await assert_books_balance(container.uow_factory)


async def test_a_stored_trade_is_returned_whatever_became_of_the_market_and_the_trader_since(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    """A replay looks at nothing but the key: the market has passed its deadline and
    the trader could not afford the trade again, and the answer is the one of then."""
    poor = await factory.user(balance_micro=SMALLEST_PRESET)
    key = uuid7()
    body = buy_for(Outcome.YES, SMALLEST_PRESET)
    who = factory.identity(poor)
    request = TradeRequest.model_validate(body)
    first = await container.trading.trade(who, market.id, key, request)
    assert first.balance_cr == to_cr(0)

    clock.set(market.close_at + timedelta(days=1))
    replay = await container.trading.trade(who, market.id, key, request)

    assert replay == first
    assert await count_rows(container.uow_factory, Trade) == 1
    await assert_books_balance(container.uow_factory)


# --- The rate limit (SPEC §5, A.4) ---------------------------------------------------------


async def test_the_request_after_max_orders_per_minute_is_rate_limited(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    as_user: ActAs,
) -> None:
    body = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)
    for _ in range(MAX_ORDERS):
        executed(await post_trade(client, market.id, body))
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, body)

    details = assert_error(refused, 429, "RATE_LIMITED")
    assert set(details) == {"retry_after_seconds"}
    assert 1 <= details["retry_after_seconds"] <= ORDER_WINDOW_SECONDS
    assert refused.headers["retry-after"] == str(details["retry_after_seconds"])
    assert await books(container, factory, market, trader) == before
    # The limit is each user's own.
    as_user(await factory.user(balance_micro=SIGNUP_GRANT))
    executed(await post_trade(client, market.id, body))
    await assert_books_balance(container.uow_factory)


async def test_every_request_counts_against_the_limit_whatever_it_was_answered(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """Replays and refusals included: the limit is on requests, before any of them is
    looked at. The request over the limit is not looked at either."""
    key = uuid7()
    body = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)
    executed(await post_trade(client, market.id, body, key))
    assert_error(await post_trade(client, uuid7(), body), 404, "NOT_FOUND")
    assert_error(
        await post_trade(client, market.id, buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1)),
        422,
        "TRADE_TOO_SMALL",
    )
    for _ in range(MAX_ORDERS - 3):
        assert (await post_trade(client, market.id, body, key)).status_code == 201

    for over_the_limit in (
        await post_trade(client, market.id, body, key),
        await post_trade(client, uuid7(), body),
        await post_trade(client, market.id, body),
    ):
        assert_error(over_the_limit, 429, "RATE_LIMITED")

    assert await count_rows(container.uow_factory, Trade) == 1
    await assert_books_balance(container.uow_factory)


@pytest.mark.redis_down
async def test_without_redis_trades_commit_and_the_limit_is_kept_in_process(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    """SPEC §9 and §12: with Redis stopped a trade still commits, counted by the
    in-process limiter with the same rule (A.4). That one runs on the application's
    clock: a minute after the first request the window is over."""
    body = buy_for(Outcome.NO, MIN_TRADE_AMOUNT)
    for _ in range(MAX_ORDERS):
        executed(await post_trade(client, market.id, body))
    before = await books(container, factory, market, trader)

    refused = await post_trade(client, market.id, body)

    assert assert_error(refused, 429, "RATE_LIMITED") == {
        "retry_after_seconds": ORDER_WINDOW_SECONDS
    }
    assert refused.headers["retry-after"] == str(ORDER_WINDOW_SECONDS)
    assert await books(container, factory, market, trader) == before
    assert before.balance_micro == SIGNUP_GRANT - MAX_ORDERS * MIN_TRADE_AMOUNT

    clock.advance(timedelta(seconds=ORDER_WINDOW_SECONDS))
    executed(await post_trade(client, market.id, body))
    assert (await stored(container, market)).version == MAX_ORDERS + 1
    await assert_books_balance(container.uow_factory)


# --- After the commit (SPEC §8 step 9) -------------------------------------------------------


async def test_a_trade_drops_the_cached_market_lists(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """Every list, whatever statuses it is of: the price and the volume on any of them
    may have changed."""
    cache = container.market_cache
    keys = [MarketCache.list_key(list(MarketStatus)), MarketCache.list_key([MarketStatus.OPEN])]
    for key in keys:
        await cache.set(key, '{"items":[]}')
    assert [await cache.get(key) for key in keys] == ['{"items":[]}'] * 2

    executed(await post_trade(client, market.id, buy_for(Outcome.YES, SMALLEST_PRESET)))

    assert [await cache.get(key) for key in keys] == [None, None]
    await assert_books_balance(container.uow_factory)


async def test_a_trade_publishes_the_new_quote_to_the_streams_of_its_market(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
    clock: FrozenClock,
) -> None:
    async with container.live_quotes.subscribe(market.id) as events:
        assert await next_event(events) == quote_event(container, market)
        clock.advance(timedelta(seconds=10))

        trade = executed(await post_trade(client, market.id, buy_for(Outcome.YES, STAKE_LIMIT)))
        published = await next_event(events)

    after = await stored(container, market)
    assert published == quote_event(container, after)
    assert isinstance(published, QuoteEvent)
    assert (published.version, published.volume_cr) == (trade.market_version, trade.amount_cr)
    assert (published.p_yes, published.p_no) == (trade.p_yes_after, trade.p_no_after)
    assert published.ts == clock.now()
    await assert_books_balance(container.uow_factory)


async def test_a_replay_and_a_refused_trade_announce_nothing(
    client: httpx.AsyncClient,
    container: Container,
    market: Market,
    trader: User,
) -> None:
    """Neither changed anything: the lists stay cached and no event goes out."""
    key = uuid7()
    body = buy_for(Outcome.YES, STAKE_LIMIT)
    executed(await post_trade(client, market.id, body, key))
    cache = container.market_cache
    cached = MarketCache.list_key([MarketStatus.OPEN, MarketStatus.HALTED])
    await cache.set(cached, '{"items":[]}')

    async with container.live_quotes.subscribe(market.id) as events:
        await next_event(events)
        assert (await post_trade(client, market.id, body, key)).status_code == 201
        assert_error(await post_trade(client, market.id, body), 409, "LIMIT_EXCEEDED")
        # Whatever was published before this would arrive before it.
        await container.live_quotes.publish_status(market)
        assert isinstance(await next_event(events), StatusEvent)

    assert await cache.get(cached) == '{"items":[]}'
    await assert_books_balance(container.uow_factory)


# --- Postgres decides ----------------------------------------------------------------------


@pytest.mark.postgres_down
async def test_without_postgres_a_trade_is_answered_503(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """SPEC §9: nothing financial is decided anywhere else. The lost connection is
    nobody's to translate in the service; the API answers it for every route."""
    as_user(
        AuthenticatedUser(
            id=uuid7(),
            email="trader@ethz.ch",
            name="Trader",
            username="trader",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
        )
    )

    refused = await post_trade(client, uuid7(), buy_for(Outcome.YES, SMALLEST_PRESET))

    assert assert_error(refused, 503, "SERVICE_UNAVAILABLE") == {}
