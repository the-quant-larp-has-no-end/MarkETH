"""Pricing a trade without executing it (SPEC §4, §10, Appendix A.4, A.9).

A trade is sized in one of three ways, the request modes of A.4: a buy for an amount, a
buy of shares, a sell of shares. `QuoteService.price` prices each with the LMSR function
made for it, on the quantities of the market row it is given; `to_response` shows the
result in credits, shares and display probabilities.

`quote` is the two on the row as Postgres has it now, for the one who asks: it applies
`admit` and `check_buy`, the rules a trade is held to before it writes, so the amount
somebody types is judged by the server. What a quote refuses is held against what the
trade answers to the same request by the same person: the same status, code, message
and details.

Every expected figure is what `app.pricing.lmsr` gives for the same market, at the
precision of the settings, shown the way `MarketView` shows it.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from app.container import Container
from app.core.enums import (
    STORED_MARKET_STATUSES,
    MarketStatus,
    MarketTier,
    Outcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.errors import (
    ForbiddenError,
    InsufficientBalanceError,
    InsufficientSharesError,
    LimitExceededError,
    MarketNotOpenError,
    TradeTooSmallError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, format_cr, format_shares, format_tokens, to_cr
from app.db.base import Base
from app.models import Market, Position, Trade, User
from app.pricing import lmsr
from app.schemas.common import REQUEST_INTEGER_DIGITS, Limit
from app.schemas.markets import MarketDetailResponse
from app.schemas.trading import QuoteResponse, TradeResponse, TradeSize
from app.services.market_view import MarketView
from app.services.quotes import OWN_MARKET_MESSAGE, QuoteService
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.economy import (
    DEFAULT_B,
    MARKET_B,
    MIN_TRADE_SHARES,
    PRECISION,
    STAKE_LIMIT,
    TIER_B,
)
from tests.support.factories import ActAs, Factory
from tests.support.settings import load_base_settings
from tests.support.statements import recorded_statements, takes_a_row_lock, writes

pytestmark = pytest.mark.integration

MIN_TRADE_AMOUNT: Final[int] = load_base_settings().pricing.min_trade_micro
"""MIN_TRADE_CR in µCR: the smallest trade that is sized by an amount."""

LARGEST_REQUEST: Final[int] = 10**REQUEST_INTEGER_DIGITS * MICRO - 1
"""The largest size or budget the request model lets through, in micro-units."""

YES_SOLD: Final[int] = 120 * MICRO
NO_SOLD: Final[int] = 45 * MICRO
"""What the traded market of these tests has sold of each outcome."""

WALLET: Final[int] = 3 * STAKE_LIMIT
"""What somebody who asks for a quote starts with, unless the balance is what a test is
about: more than the stake limit, so that the limit decides and not the balance."""

MAX_ORDERS: Final[int] = load_base_settings().risk.max_orders_per_minute
"""MAX_ORDERS_PER_MINUTE: trade requests one user may send per minute."""

WAIT_SECONDS: Final[float] = 5.0
"""How long a test waits for what should come at once, before it fails instead of
hanging."""

type Sizing = Callable[[Outcome, int], TradeSize]
"""One request mode of A.4: makes the request for an outcome and a size in micro-units."""


# --- The three request modes --------------------------------------------------------


def buy_for(outcome: Outcome, amount_micro: int) -> TradeSize:
    """BUY sized by `amount_cr`: as many shares as the budget pays for."""
    return TradeSize(side=TradeSide.BUY, outcome=outcome, amount_cr=to_cr(amount_micro))


def buy_shares(outcome: Outcome, shares_micro: int) -> TradeSize:
    """BUY sized by `shares`."""
    return TradeSize(side=TradeSide.BUY, outcome=outcome, shares=to_cr(shares_micro))


def sell_shares(outcome: Outcome, shares_micro: int) -> TradeSize:
    """SELL sized by `shares`."""
    return TradeSize(side=TradeSide.SELL, outcome=outcome, shares=to_cr(shares_micro))


def lmsr_quote(market: Market, size: TradeSize, *, precision: int = PRECISION) -> lmsr.TradeQuote:
    """What `app.pricing.lmsr` gives for `size` on the quantities of `market`: the
    function of the request's mode, at the precision of the settings unless another
    is named."""
    state = state_of(market)
    if size.amount_cr is not None:
        return lmsr.quote_buy_for_budget(
            state, size.outcome, micro(size.amount_cr), precision=precision
        )
    assert size.shares is not None
    price = lmsr.quote_buy_shares if size.side is TradeSide.BUY else lmsr.quote_sell_shares
    return price(state, size.outcome, micro(size.shares), precision=precision)


def micro(amount: Decimal) -> int:
    """A size of a request in micro-units."""
    return int(amount * MICRO)


def state_of(market: Market) -> lmsr.MarketState:
    return lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)


def market_at(
    q_yes_micro: int,
    q_no_micro: int,
    *,
    b_micro: int = DEFAULT_B,
    version: int = 0,
    status: MarketStatus = MarketStatus.OPEN,
    close_at: datetime = TEST_NOW + timedelta(days=7),
    created_by: UUID | None = None,
) -> Market:
    """A market row as far as pricing and the rules of a trade read it: its quantities,
    its b and its version, its status, its deadline and who proposed it (somebody
    else, unless a test names the proposer). It is in no database."""
    return Market(
        id=uuid7(),
        q_yes_micro=q_yes_micro,
        q_no_micro=q_no_micro,
        b_micro=b_micro,
        version=version,
        status=status,
        close_at=close_at,
        created_by=created_by or uuid7(),
    )


def caller(user_id: UUID) -> AuthenticatedUser:
    """A registered user with this id, as a guard hands one to a service. There is no
    such row in any database."""
    return AuthenticatedUser(
        id=user_id,
        email=f"{user_id.hex}@ethz.ch",
        name="Tina Trader",
        username="tina",
        degree_programme="BSc Computer Science",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )


def holding(market: Market, user_id: UUID, outcome: Outcome, **amounts: int) -> Position:
    """A position row of `user_id` in `market`. It is in no database."""
    values = {"shares_micro": 0, "cost_micro": 0, "proceeds_micro": 0} | amounts
    return Position(user_id=user_id, market_id=market.id, outcome=outcome, **values)


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
        payout_cr=to_cr(quote.shares_micro),
        p_yes_before=p_yes_before,
        p_no_before=p_no_before,
        p_yes_after=p_yes_after,
        p_no_after=p_no_after,
        quote_version=market.version,
    )


def quote_service(container: Container, **pricing: int) -> QuoteService:
    """A `QuoteService` wired like the container's, with other values for some of the
    pricing settings."""
    changed = container.settings.model_copy(
        update={"pricing": container.settings.pricing.model_copy(update=pricing)}
    )
    return QuoteService(container.uow_factory, changed, container.clock, MarketView(changed))


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


def query_of(size: TradeSize) -> dict[str, str]:
    """`size` as the query string of `GET /markets/{id}/quote`."""
    query = {"side": size.side.value, "outcome": size.outcome.value}
    if size.amount_cr is not None:
        query["amount_cr"] = format_cr(micro(size.amount_cr))
    if size.shares is not None:
        query["shares"] = format_cr(micro(size.shares))
    return query


async def get_quote(client: httpx.AsyncClient, market_id: UUID, size: TradeSize) -> httpx.Response:
    return await client.get(f"/api/markets/{market_id}/quote", params=query_of(size))


async def post_trade(client: httpx.AsyncClient, market_id: UUID, size: TradeSize) -> httpx.Response:
    """The trade a quote of `size` is the quote of: the same size, without a guard,
    under a key of its own."""
    return await client.post(
        f"/api/markets/{market_id}/trades",
        json=query_of(size),
        headers={"Idempotency-Key": str(uuid7())},
    )


async def refused_alike(
    client: httpx.AsyncClient,
    container: Container,
    market_id: UUID,
    size: TradeSize,
    status_code: int,
    code: str,
) -> dict[str, Any]:
    """Ask for the quote of `size` and send the trade, as whoever is calling. Both are
    refused with `code`, in one and the same body, and neither wrote anything. Returns
    the `details` they share."""
    before = await everything(container)

    quoted = await get_quote(client, market_id, size)
    traded = await post_trade(client, market_id, size)

    assert quoted.status_code == status_code, quoted.text
    assert traded.status_code == status_code, traded.text
    assert quoted.json() == traded.json(), "the quote answers what the trade would"
    assert quoted.json()["code"] == code
    assert await everything(container) == before
    details: dict[str, Any] = quoted.json()["details"]
    return details


async def everything(container: Container) -> dict[str, list[tuple[object, ...]]]:
    """Every row of every table. Two readings are equal exactly when nothing was
    written in between."""
    found: dict[str, list[tuple[object, ...]]] = {}
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            rows = await uow.session.execute(select(table).order_by(*table.primary_key.columns))
            found[table.name] = [tuple(row) for row in rows]
    return found


async def stored(container: Container, market_id: UUID) -> Market:
    """The market row as Postgres has it now."""
    async with container.uow_factory() as uow:
        market = await uow.markets.get(market_id)
    assert market is not None
    return market


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is that error; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    details: dict[str, Any] = body["details"]
    return details


@pytest.fixture
async def holder(factory: Factory) -> User:
    """Who holds the YES shares of the traded market, with tokens to buy more."""
    return await factory.user(balance_micro=WALLET)


@pytest.fixture
async def traded(factory: Factory, holder: User) -> Market:
    """An OPEN market of the DEFAULT tier that sold `YES_SOLD`, all of it to `holder`,
    and `NO_SOLD` to somebody else."""
    market = await factory.market()
    bob = await factory.user(balance_micro=NO_SOLD)
    await factory.position(holder, market, Outcome.YES, shares_micro=YES_SOLD)
    await factory.position(bob, market, Outcome.NO, shares_micro=NO_SOLD)
    return market


SIZINGS: Final[list[Any]] = [
    pytest.param(buy_for, 25 * MICRO, id="buy-by-amount"),
    pytest.param(buy_shares, 30 * MICRO, id="buy-by-shares"),
    pytest.param(sell_shares, 20 * MICRO, id="sell-by-shares"),
]
"""The three request modes of A.4, each with a size the traded market can take."""


# --- price: one LMSR function per request mode ----------------------------------------


@pytest.mark.parametrize("outcome", list(Outcome))
def test_a_buy_sized_by_an_amount_is_priced_as_the_shares_that_budget_pays_for(
    container: Container, outcome: Outcome
) -> None:
    market = market_at(YES_SOLD, NO_SOLD)
    budget_micro = 25 * MICRO

    quote = container.quotes.price(market, buy_for(outcome, budget_micro))

    assert quote == lmsr.quote_buy_for_budget(
        state_of(market), outcome, budget_micro, precision=PRECISION
    )
    assert (quote.side, quote.outcome) == (TradeSide.BUY, outcome)
    # A.4: the trader is charged exactly the budget, for at least as many shares.
    assert quote.amount_micro == budget_micro
    assert quote.shares_micro >= budget_micro


@pytest.mark.parametrize("outcome", list(Outcome))
def test_a_buy_sized_by_shares_is_priced_as_the_cost_of_those_shares(
    container: Container, outcome: Outcome
) -> None:
    market = market_at(YES_SOLD, NO_SOLD)
    shares_micro = 30 * MICRO

    quote = container.quotes.price(market, buy_shares(outcome, shares_micro))

    assert quote == lmsr.quote_buy_shares(
        state_of(market), outcome, shares_micro, precision=PRECISION
    )
    assert (quote.side, quote.shares_micro) == (TradeSide.BUY, shares_micro)
    assert 0 < quote.amount_micro < shares_micro, "a share costs less than one credit"


@pytest.mark.parametrize("outcome", list(Outcome))
def test_a_sell_is_priced_as_the_proceeds_of_those_shares(
    container: Container, outcome: Outcome
) -> None:
    market = market_at(YES_SOLD, NO_SOLD)
    shares_micro = 20 * MICRO

    quote = container.quotes.price(market, sell_shares(outcome, shares_micro))

    assert quote == lmsr.quote_sell_shares(
        state_of(market), outcome, shares_micro, precision=PRECISION
    )
    assert (quote.side, quote.shares_micro) == (TradeSide.SELL, shares_micro)
    assert 0 < quote.amount_micro < shares_micro


quantities = st.integers(min_value=0, max_value=50 * max(MARKET_B))
"""What a market may have sold of an outcome: up to fifty times the largest b."""


@st.composite
def priced_requests(draw: st.DrawFn) -> tuple[Market, TradeSize]:
    """A market in any state and a request, in any of the three modes, that is large
    enough to be priced. A sell asks for no more than the market has sold."""
    market = market_at(draw(quantities), draw(quantities), b_micro=draw(st.sampled_from(MARKET_B)))
    outcome = draw(st.sampled_from(Outcome))
    sold_micro = market.q_yes_micro if outcome is Outcome.YES else market.q_no_micro
    sizings: list[tuple[Sizing, int, int]] = [
        (buy_for, MIN_TRADE_AMOUNT, LARGEST_REQUEST),
        (buy_shares, MIN_TRADE_SHARES, LARGEST_REQUEST),
    ]
    if sold_micro >= MIN_TRADE_SHARES:
        sizings.append((sell_shares, MIN_TRADE_SHARES, min(sold_micro, LARGEST_REQUEST)))
    sizing, smallest, largest = draw(st.sampled_from(sizings))
    return market, sizing(outcome, draw(st.integers(min_value=smallest, max_value=largest)))


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(priced=priced_requests())
@example(priced=(market_at(0, 0), buy_for(Outcome.YES, MIN_TRADE_AMOUNT)))
@example(priced=(market_at(0, 0), buy_shares(Outcome.NO, MIN_TRADE_SHARES)))
@example(priced=(market_at(YES_SOLD, 0), sell_shares(Outcome.YES, YES_SOLD)))
@example(priced=(market_at(0, 0, b_micro=max(MARKET_B)), buy_for(Outcome.NO, LARGEST_REQUEST)))
def test_whatever_the_market_and_the_size_a_request_is_priced_as_lmsr_prices_its_mode(
    container: Container, priced: tuple[Market, TradeSize]
) -> None:
    """The row's quantities and b, the request's outcome and size, and the precision of
    the settings reach the LMSR function of the request's mode unchanged."""
    market, size = priced

    quote = container.quotes.price(market, size)

    assert quote == lmsr_quote(market, size)
    assert quote.before == state_of(market)
    assert (quote.side, quote.outcome) == (size.side, size.outcome)


@pytest.mark.parametrize(("sizing", "size_micro"), SIZINGS)
def test_each_mode_is_priced_with_the_digits_of_pricing_decimal_precision(
    container: Container, sizing: Sizing, size_micro: int
) -> None:
    """Shown with a b that only more digits than the settings name can price: lmsr takes
    it from a service set to those digits and refuses it from the one of the settings,
    where such a b is a bug and no answer to a user."""
    more_digits = PRECISION + 10
    b_micro = 10 ** ((PRECISION - 17) // 3 + 1)
    assert lmsr.is_quotable(b_micro, precision=more_digits)
    assert not lmsr.is_quotable(b_micro, precision=PRECISION)
    market = market_at(YES_SOLD, NO_SOLD, b_micro=b_micro)
    size = sizing(Outcome.YES, size_micro)

    quote = quote_service(container, decimal_precision=more_digits).price(market, size)

    assert quote == lmsr_quote(market, size, precision=more_digits)
    with pytest.raises(ValueError, match=f"cannot be quoted with {PRECISION} digits"):
        container.quotes.price(market, size)


# --- price: the smallest trade ----------------------------------------------------------


def test_a_trade_sized_by_an_amount_is_at_least_min_trade_cr(container: Container) -> None:
    market = market_at(YES_SOLD, NO_SOLD)

    with pytest.raises(TradeTooSmallError) as too_small:
        container.quotes.price(market, buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1))

    assert too_small.value.message == "The smallest trade is 1 token."
    assert too_small.value.message == f"The smallest trade is {format_tokens(MIN_TRADE_AMOUNT)}."
    at_the_minimum = container.quotes.price(market, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))
    assert at_the_minimum.amount_micro == MIN_TRADE_AMOUNT


@pytest.mark.parametrize("sizing", [buy_shares, sell_shares], ids=["buy", "sell"])
def test_a_trade_sized_by_shares_is_at_least_min_trade_shares(
    container: Container, sizing: Sizing
) -> None:
    market = market_at(YES_SOLD, NO_SOLD)

    with pytest.raises(TradeTooSmallError) as too_small:
        container.quotes.price(market, sizing(Outcome.YES, MIN_TRADE_SHARES - 1))

    assert too_small.value.message == "The smallest trade is 1 share."
    assert too_small.value.message == f"The smallest trade is {format_shares(MIN_TRADE_SHARES)}."
    at_the_minimum = container.quotes.price(market, sizing(Outcome.YES, MIN_TRADE_SHARES))
    assert at_the_minimum.shares_micro == MIN_TRADE_SHARES


def test_each_way_of_sizing_a_trade_has_its_own_minimum(container: Container) -> None:
    """MIN_TRADE_CR is for an amount and MIN_TRADE_SHARES for shares: with two values
    that differ, each size is held against its own and not against the other."""
    two_credits, five_shares = 2 * MICRO, 5 * MICRO
    quotes = quote_service(
        container, min_trade_micro=two_credits, min_trade_shares_micro=five_shares
    )
    market = market_at(YES_SOLD, NO_SOLD)

    assert quotes.price(market, buy_for(Outcome.YES, two_credits)).amount_micro == two_credits
    with pytest.raises(TradeTooSmallError) as below_the_amount:
        quotes.price(market, buy_for(Outcome.YES, two_credits - 1))
    assert below_the_amount.value.message == "The smallest trade is 2 tokens."
    for sizing in (buy_shares, sell_shares):
        assert quotes.price(market, sizing(Outcome.YES, five_shares)).shares_micro == five_shares
        with pytest.raises(TradeTooSmallError) as below_the_shares:
            quotes.price(market, sizing(Outcome.YES, five_shares - 1))
        assert below_the_shares.value.message == "The smallest trade is 5 shares."


# --- price: no more than the market has sold ------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "sold_micro"), [(Outcome.YES, YES_SOLD), (Outcome.NO, NO_SOLD)], ids=["yes", "no"]
)
def test_a_sell_of_more_than_the_market_has_sold_of_the_outcome_is_refused(
    container: Container, outcome: Outcome, sold_micro: int
) -> None:
    """Nobody can hold such shares, and lmsr must not see a negative quantity: it would
    price one (`quote_sell_shares` leaves the check to its caller)."""
    market = market_at(YES_SOLD, NO_SOLD)

    with pytest.raises(InsufficientSharesError):
        container.quotes.price(market, sell_shares(outcome, sold_micro + 1))

    everything = container.quotes.price(market, sell_shares(outcome, sold_micro))
    assert everything == lmsr.quote_sell_shares(
        state_of(market), outcome, sold_micro, precision=PRECISION
    )
    left = everything.after.q_yes if outcome is Outcome.YES else everything.after.q_no
    assert left == 0


def test_a_sell_too_small_to_trade_is_too_small_whatever_the_market_has_sold(
    container: Container,
) -> None:
    """The size is judged before the market is asked what it has sold."""
    with pytest.raises(TradeTooSmallError):
        container.quotes.price(market_at(0, 0), sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1))
    with pytest.raises(InsufficientSharesError):
        container.quotes.price(market_at(0, 0), sell_shares(Outcome.YES, MIN_TRADE_SHARES))


# --- to_response: a quote as the API shows it ------------------------------------------------


@pytest.mark.parametrize(("sizing", "size_micro"), SIZINGS)
def test_a_quote_is_shown_in_credits_shares_and_display_probabilities(
    container: Container, sizing: Sizing, size_micro: int
) -> None:
    view = container.market_view
    market = market_at(YES_SOLD, NO_SOLD, version=7)
    quote = lmsr_quote(market, sizing(Outcome.YES, size_micro))

    response = container.quotes.to_response(market, quote)

    assert response == shown(view, market, quote)
    assert (response.market_id, response.quote_version) == (market.id, 7)
    assert (response.shares, response.amount_cr) == (
        to_cr(quote.shares_micro),
        to_cr(quote.amount_micro),
    )
    assert response.average_price == view.average_price(quote.amount_micro, quote.shares_micro)
    assert (response.p_yes_before, response.p_no_before) == view.probabilities(
        view.p_yes_micro(market)
    )
    assert (response.p_yes_after, response.p_no_after) == view.probabilities(
        quote.p_yes_after_micro
    )
    # A buy of YES raises its price and a sell lowers it, by more than the display hides.
    if quote.side is TradeSide.BUY:
        assert response.p_yes_after > response.p_yes_before
    else:
        assert response.p_yes_after < response.p_yes_before


def test_a_shown_quote_travels_with_amounts_as_strings_and_prices_as_numbers(
    container: Container,
) -> None:
    """A.2. This is also the `details.quote` of a `QUOTE_STALE`."""
    market = market_at(YES_SOLD, NO_SOLD, version=3)
    quote = lmsr_quote(market, buy_for(Outcome.NO, 25 * MICRO))

    body = container.quotes.to_response(market, quote).model_dump(mode="json")

    assert body["market_id"] == str(market.id)
    assert (body["side"], body["outcome"], body["quote_version"]) == ("BUY", "NO", 3)
    assert body["amount_cr"] == format_cr(quote.amount_micro)
    assert body["shares"] == format_cr(quote.shares_micro)
    for price in ("average_price", "p_yes_before", "p_no_before", "p_yes_after", "p_no_after"):
        assert isinstance(body[price], float)


def test_a_sell_that_would_bring_in_nothing_is_a_quote_like_any_other(
    container: Container,
) -> None:
    """A.4: shares priced below a millionth of a credit round down to no proceeds."""
    b_micro = TIER_B[MarketTier.TIGHT]
    lead_micro = lead_that_makes_the_smallest_sell_worthless(b_micro)
    market = market_at(lead_micro + MIN_TRADE_SHARES, MIN_TRADE_SHARES, b_micro=b_micro)

    quote = container.quotes.price(market, sell_shares(Outcome.NO, MIN_TRADE_SHARES))
    response = container.quotes.to_response(market, quote)

    assert quote == lmsr.quote_sell_shares(
        state_of(market), Outcome.NO, MIN_TRADE_SHARES, precision=PRECISION
    )
    assert (quote.amount_micro, quote.shares_micro) == (0, MIN_TRADE_SHARES)
    assert response == shown(container.market_view, market, quote)
    assert (response.amount_cr, response.average_price) == (to_cr(0), 0.0)


# --- admit and check_buy: the rules of a trade, on rows -----------------------------------------


def test_a_trade_is_admitted_at_the_price_of_its_size(container: Container) -> None:
    """Whoever may trade gets the quote `price` gives: admitting adds no figure."""
    trader = uuid7()
    market = market_at(YES_SOLD, NO_SOLD)
    held = [holding(market, trader, Outcome.YES, shares_micro=YES_SOLD)]
    someone = caller(trader)

    for size in (
        buy_for(Outcome.NO, 25 * MICRO),
        buy_shares(Outcome.YES, 30 * MICRO),
        sell_shares(Outcome.YES, YES_SOLD),
    ):
        admitted = container.quotes.admit(someone, market, held, size, TEST_NOW)

        assert admitted == container.quotes.price(market, size) == lmsr_quote(market, size)


def test_the_proposer_of_a_market_is_admitted_to_no_trade_in_it(container: Container) -> None:
    """A.4, self-dealing: refused before anything else is looked at, so also in a
    market that does not trade and for a size too small to be one."""
    proposer = uuid7()
    own = market_at(YES_SOLD, NO_SOLD, created_by=proposer)
    own_halted = market_at(YES_SOLD, NO_SOLD, created_by=proposer, status=MarketStatus.HALTED)
    held = [holding(own, proposer, Outcome.YES, shares_micro=YES_SOLD)]

    for market in (own, own_halted):
        for size in (
            buy_for(Outcome.YES, 25 * MICRO),
            buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1),
            sell_shares(Outcome.YES, YES_SOLD),
        ):
            with pytest.raises(ForbiddenError) as refused:
                container.quotes.admit(caller(proposer), market, held, size, TEST_NOW)
            assert (refused.value.status_code, refused.value.message) == (403, OWN_MARKET_MESSAGE)


@pytest.mark.parametrize(
    "status", [status for status in STORED_MARKET_STATUSES if status is not MarketStatus.OPEN]
)
def test_nobody_is_admitted_to_a_market_that_is_not_open(
    container: Container, status: MarketStatus
) -> None:
    trader = uuid7()
    market = market_at(YES_SOLD, NO_SOLD, status=status)
    held = [holding(market, trader, Outcome.YES, shares_micro=YES_SOLD)]

    for size in (buy_for(Outcome.YES, 25 * MICRO), sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1)):
        with pytest.raises(MarketNotOpenError):
            container.quotes.admit(caller(trader), market, held, size, TEST_NOW)


def test_an_open_market_admits_trades_until_its_deadline_and_none_from_then_on(
    container: Container,
) -> None:
    """A.4: `now` is what the caller read, and `now >= close_at` is closed."""
    market = market_at(YES_SOLD, NO_SOLD)
    someone = caller(uuid7())
    size = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)

    a_moment_before = market.close_at - timedelta(microseconds=1)
    assert container.quotes.admit(someone, market, [], size, a_moment_before).amount_micro > 0
    with pytest.raises(MarketNotOpenError):
        container.quotes.admit(someone, market, [], size, market.close_at)


def test_a_sell_is_admitted_for_no_more_than_the_seller_holds_of_that_outcome(
    container: Container,
) -> None:
    """No short positions (SPEC §4). The market has sold plenty of both outcomes; what
    counts is the seller's own row of the outcome sold, to the micro-share."""
    seller = caller(uuid7())
    market = market_at(YES_SOLD, NO_SOLD)
    owned = 50 * MICRO
    held = [holding(market, seller.id, Outcome.YES, shares_micro=owned)]

    assert container.quotes.admit(
        seller, market, held, sell_shares(Outcome.YES, owned), TEST_NOW
    ) == lmsr_quote(market, sell_shares(Outcome.YES, owned))
    for beyond in (sell_shares(Outcome.YES, owned + 1), sell_shares(Outcome.NO, MIN_TRADE_SHARES)):
        with pytest.raises(InsufficientSharesError):
            container.quotes.admit(seller, market, held, beyond, TEST_NOW)
    with pytest.raises(InsufficientSharesError):
        container.quotes.admit(seller, market, [], sell_shares(Outcome.YES, owned), TEST_NOW)
    # A holding is asked for before the size is: shares nobody holds are not "too small".
    with pytest.raises(InsufficientSharesError):
        container.quotes.admit(
            seller, market, [], sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), TEST_NOW
        )
    with pytest.raises(TradeTooSmallError):
        container.quotes.admit(
            seller, market, held, sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), TEST_NOW
        )


def test_a_buy_is_checked_against_the_stake_limit_and_then_against_the_balance(
    container: Container,
) -> None:
    """SPEC §8 step 6, with the details a client shows. The stake left it names is the
    one before the buy, which is `MarketView`'s."""
    view = container.market_view
    buyer = uuid7()
    market = market_at(YES_SOLD, NO_SOLD)
    left = 30 * MICRO
    staked = [
        holding(market, buyer, Outcome.YES, shares_micro=5 * MICRO, cost_micro=STAKE_LIMIT - left)
    ]
    assert view.stake_left_micro(view.net_stake_micro(staked)) == left
    exactly = container.quotes.price(market, buy_for(Outcome.NO, left))
    one_more = container.quotes.price(market, buy_for(Outcome.NO, left + 1))

    container.quotes.check_buy(staked, left, exactly)
    with pytest.raises(LimitExceededError) as over_the_limit:
        container.quotes.check_buy(staked, WALLET, one_more)
    with pytest.raises(LimitExceededError):
        container.quotes.check_buy(staked, 0, one_more)
    with pytest.raises(InsufficientBalanceError) as too_poor:
        container.quotes.check_buy(staked, left - 1, exactly)

    assert over_the_limit.value.details == {
        "limit": Limit.MAX_STAKE_PER_USER_MARKET,
        "stake_limit_cr": to_cr(STAKE_LIMIT),
        "stake_left_cr": to_cr(left),
        "required_cr": to_cr(left + 1),
    }
    assert over_the_limit.value.message == (
        "This costs 30.000001 tokens. You can still put 30 tokens into this market."
    )
    assert too_poor.value.details == {"balance_cr": to_cr(left - 1), "required_cr": to_cr(left)}
    assert too_poor.value.message == "This costs 30 tokens. You have 29.999999 tokens."


def test_a_buy_with_nothing_left_of_the_stake_limit_is_told_that_the_limit_is_reached(
    container: Container,
) -> None:
    """The sentence names the limit, because there is no amount left to name. The
    details say what the buy costs all the same."""
    buyer = uuid7()
    market = market_at(YES_SOLD, NO_SOLD)
    at_the_limit = [
        holding(market, buyer, Outcome.YES, shares_micro=5 * MICRO, cost_micro=STAKE_LIMIT)
    ]
    smallest = container.quotes.price(market, buy_shares(Outcome.NO, MIN_TRADE_SHARES))

    with pytest.raises(LimitExceededError) as refused:
        container.quotes.check_buy(at_the_limit, WALLET, smallest)

    assert refused.value.message == "You have reached the limit of 100 tokens in this market."
    assert refused.value.message == (
        f"You have reached the limit of {format_tokens(STAKE_LIMIT)} in this market."
    )
    assert refused.value.details == {
        "limit": Limit.MAX_STAKE_PER_USER_MARKET,
        "stake_limit_cr": to_cr(STAKE_LIMIT),
        "stake_left_cr": to_cr(0),
        "required_cr": to_cr(smallest.amount_micro),
    }


def test_a_quote_says_what_its_shares_pay(container: Container) -> None:
    """A share pays one credit if its outcome comes true, so `payout_cr` is the shares
    of the trade counted in credits: for a buy of shares, which costs what
    `quote_buy_shares` says, for a buy for an amount and for a sell, where it is what
    the seller gives up."""
    market = market_at(YES_SOLD, NO_SOLD)
    fifty = buy_shares(Outcome.NO, 50 * MICRO)

    bought = container.quotes.to_response(market, container.quotes.price(market, fifty))

    assert bought.amount_cr == to_cr(lmsr_quote(market, fifty).amount_micro)
    assert (bought.shares, bought.payout_cr) == (to_cr(50 * MICRO), to_cr(50 * MICRO))
    assert bought.model_dump(mode="json")["payout_cr"] == "50"
    assert bought.amount_cr < bought.payout_cr, "a share costs less than it pays"
    for size in (buy_for(Outcome.YES, 30 * MICRO), sell_shares(Outcome.YES, 25 * MICRO)):
        quote = container.quotes.price(market, size)
        response = container.quotes.to_response(market, quote)
        assert response.payout_cr == response.shares == to_cr(quote.shares_micro)


def test_a_gain_taken_out_of_a_market_can_be_staked_in_it_again(container: Container) -> None:
    """The limit is on cost minus proceeds after the buy. Somebody who sold for more
    than they paid may buy for the whole limit and their gain on top, and not one
    micro-credit more."""
    buyer = uuid7()
    market = market_at(YES_SOLD, NO_SOLD)
    gain = 12 * MICRO
    sold_out = [
        holding(market, buyer, Outcome.YES, cost_micro=40 * MICRO, proceeds_micro=40 * MICRO + gain)
    ]
    all_of_it = container.quotes.price(market, buy_for(Outcome.NO, STAKE_LIMIT + gain))
    one_more = container.quotes.price(market, buy_for(Outcome.NO, STAKE_LIMIT + gain + 1))

    container.quotes.check_buy(sold_out, WALLET, all_of_it)
    with pytest.raises(LimitExceededError) as refused:
        container.quotes.check_buy(sold_out, WALLET, one_more)

    assert refused.value.details["stake_left_cr"] == to_cr(STAKE_LIMIT)


# --- quote: GET /markets/{id}/quote -----------------------------------------------------------


@pytest.mark.parametrize(("sizing", "size_micro"), SIZINGS)
async def test_the_quote_of_each_request_mode_is_what_lmsr_gives_for_the_market_as_it_stands(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
    sizing: Sizing,
    size_micro: int,
) -> None:
    as_user(holder)
    size = sizing(Outcome.YES, size_micro)

    response = await get_quote(client, traded.id, size)

    assert response.status_code == 200, response.text
    expected = shown(container.market_view, traded, lmsr_quote(traded, size))
    assert QuoteResponse.model_validate(response.json()) == expected
    assert response.json() == expected.model_dump(mode="json")
    assert response.json()["quote_version"] == traded.version == 2


async def test_a_quote_is_for_the_one_who_asks(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """One market, one size, four people: who has the tokens gets the figures, and
    everybody else the refusal their own trade would get."""
    penniless = await factory.user()
    proposer = await factory.user(balance_micro=WALLET)
    own = await factory.market(created_by=proposer)
    size = buy_for(Outcome.NO, 25 * MICRO)

    as_user(holder)
    for_the_holder = await get_quote(client, traded.id, size)
    as_user(penniless)
    for_the_penniless = await get_quote(client, traded.id, size)
    as_user(proposer)
    for_the_proposer = await get_quote(client, own.id, size)
    elsewhere = await get_quote(client, traded.id, size)

    priced = shown(container.market_view, traded, lmsr_quote(traded, size)).model_dump(mode="json")
    assert (for_the_holder.status_code, for_the_holder.json()) == (200, priced)
    assert assert_error(for_the_penniless, 409, "INSUFFICIENT_BALANCE") == {
        "balance_cr": "0",
        "required_cr": format_cr(25 * MICRO),
    }
    assert assert_error(for_the_proposer, 403, "FORBIDDEN") == {}
    assert for_the_proposer.json()["message"] == OWN_MARKET_MESSAGE
    # In a market somebody else proposed, the proposer is a trader like any other.
    assert (elsewhere.status_code, elsewhere.json()) == (200, priced)


async def test_a_quote_comes_from_the_quantities_of_this_moment(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """Not cached: the quote after a trade is priced on what that trade left."""
    as_user(holder)
    size = buy_for(Outcome.YES, 25 * MICRO)
    before = (await get_quote(client, traded.id, size)).json()
    carol = await factory.user(balance_micro=50 * MICRO)

    await factory.position(carol, traded, Outcome.YES, shares_micro=50 * MICRO)
    after = (await get_quote(client, traded.id, size)).json()

    assert after == shown(container.market_view, traded, lmsr_quote(traded, size)).model_dump(
        mode="json"
    )
    assert after["quote_version"] == before["quote_version"] + 1
    assert after["p_yes_before"] > before["p_yes_before"]
    assert Decimal(after["shares"]) < Decimal(before["shares"]), "the same budget buys less now"
    await assert_books_balance(container.uow_factory)


async def test_a_quote_writes_nothing_whether_it_is_answered_or_refused(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """Not a row in any table: no trade, no key, no entry, and the market as it was."""
    as_user(holder)
    before = await everything(container)

    answered = [
        await get_quote(client, traded.id, sizing(Outcome.YES, size_micro))
        for sizing, size_micro in ((buy_for, 25 * MICRO), (sell_shares, 20 * MICRO))
    ]
    refused = [
        await get_quote(client, traded.id, size)
        for size in (
            buy_for(Outcome.YES, 2 * STAKE_LIMIT),
            buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1),
            sell_shares(Outcome.NO, MIN_TRADE_SHARES),
        )
    ]

    assert [response.status_code for response in answered] == [200, 200]
    assert [response.status_code for response in refused] == [409, 422, 409]
    assert await everything(container) == before
    async with container.uow_factory() as uow:
        stored = await uow.markets.get(traded.id)
    assert stored is not None
    assert (stored.q_yes_micro, stored.q_no_micro, stored.version) == (YES_SOLD, NO_SOLD, 2)
    assert await count_rows(container.uow_factory, Trade) == 2, "the two of the fixture"
    await assert_books_balance(container.uow_factory)


async def test_a_quote_counts_against_no_limit_of_trade_requests(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """The trade sheet asks with every amount somebody types. However often it asks,
    the trade that follows is not rate limited for it."""
    as_user(holder)
    size = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)

    quotes = [await get_quote(client, traded.id, size) for _ in range(2 * MAX_ORDERS)]
    trade = await post_trade(client, traded.id, size)

    assert {response.status_code for response in quotes} == {200}
    assert trade.status_code == 201, trade.text
    await assert_books_balance(container.uow_factory)


async def test_a_quote_does_not_wait_for_a_trade_that_holds_the_rows(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """It locks nothing and waits for no lock: while a transaction holds every row a
    quote reads, the market's row, the asker's account and their positions, as a trade
    in flight holds the first two, the quote is answered from what is committed."""
    as_user(holder)
    size = buy_for(Outcome.YES, 25 * MICRO)

    async with container.uow_factory() as trade_in_flight:
        await trade_in_flight.markets.get_for_update(traded.id)
        await trade_in_flight.accounts.get_user_account(holder.id, for_update=True)
        held = await trade_in_flight.positions.list_for_user_market(
            holder.id, traded.id, for_update=True
        )
        response = await asyncio.wait_for(get_quote(client, traded.id, size), WAIT_SECONDS)
        sold = await asyncio.wait_for(
            get_quote(client, traded.id, sell_shares(Outcome.YES, 20 * MICRO)), WAIT_SECONDS
        )

    assert len(held) == 1
    assert (response.status_code, sold.status_code) == (200, 200), response.text
    assert response.json() == shown(
        container.market_view, traded, lmsr_quote(traded, size)
    ).model_dump(mode="json")


async def test_no_statement_of_a_quote_takes_a_lock_or_writes(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """A quote reads and does nothing else: whether it is answered or refused, every
    statement it sends is a SELECT without a locking clause, and none asks for an
    advisory lock. The trade of the same size sends the locks a quote does without:
    the market's row, then the trader's account."""
    as_user(holder)
    sizes = (
        buy_for(Outcome.YES, 25 * MICRO),
        sell_shares(Outcome.YES, 20 * MICRO),
        buy_for(Outcome.YES, 2 * STAKE_LIMIT),
        sell_shares(Outcome.NO, MIN_TRADE_SHARES),
    )

    with recorded_statements(container) as of_the_quotes:
        quotes = [await get_quote(client, traded.id, size) for size in sizes]
    with recorded_statements(container) as of_the_trade:
        trade = await post_trade(client, traded.id, sizes[0])

    assert [response.status_code for response in quotes] == [200, 200, 409, 409]
    assert len(of_the_quotes) >= 3 * len(sizes), "the market, the account and the positions"
    for statement in of_the_quotes:
        assert statement.startswith("SELECT"), statement
        assert not takes_a_row_lock(statement), statement
        assert not writes(statement), statement
        assert "pg_advisory" not in statement, statement
    assert trade.status_code == 201, trade.text
    locked = [statement for statement in of_the_trade if takes_a_row_lock(statement)]
    assert "FROM markets" in locked[0], "a trade locks the market row first"
    assert "FROM accounts" in locked[1], "and the trader's account second"
    assert any(writes(statement) for statement in of_the_trade)
    await assert_books_balance(container.uow_factory)


# --- quote: what the trade would answer -----------------------------------------------------


async def test_a_typed_amount_above_the_stake_left_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """`LIMIT_EXCEEDED` with the limit and what is left of it. What is left is itself
    an amount the quote answers, and the trade executes."""
    view = container.market_view
    market = await factory.market()
    trader = await factory.user(balance_micro=WALLET)
    left = 30 * MICRO
    await factory.position(
        trader, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=STAKE_LIMIT - left
    )
    async with container.uow_factory() as uow:
        positions = await uow.positions.list_for_user_market(trader.id, market.id)
    assert view.stake_left_micro(view.net_stake_micro(positions)) == left
    as_user(trader)

    for outcome, too_much in ((Outcome.NO, left + 1), (Outcome.YES, STAKE_LIMIT)):
        details = await refused_alike(
            client, container, market.id, buy_for(outcome, too_much), 409, "LIMIT_EXCEEDED"
        )
        assert details == {
            "limit": "MAX_STAKE_PER_USER_MARKET",
            "stake_limit_cr": format_cr(STAKE_LIMIT),
            "stake_left_cr": format_cr(left),
            "required_cr": format_cr(too_much),
        }
    told = await get_quote(client, market.id, buy_for(Outcome.NO, left + 1))
    assert told.json()["message"] == (
        "This costs 30.000001 tokens. You can still put 30 tokens into this market."
    )
    by_shares = buy_shares(Outcome.NO, 80 * MICRO)
    cost = lmsr_quote(market, by_shares).amount_micro
    assert left < cost, "eighty shares cost more than what is left"
    details = await refused_alike(client, container, market.id, by_shares, 409, "LIMIT_EXCEEDED")
    assert details["required_cr"] == format_cr(cost)
    told = await get_quote(client, market.id, by_shares)
    assert told.json()["message"] == (
        f"This costs {format_tokens(cost)}. You can still put 30 tokens into this market."
    )

    exactly = buy_for(Outcome.NO, left)
    quoted = await get_quote(client, market.id, exactly)
    bought = await post_trade(client, market.id, exactly)
    assert quoted.status_code == 200, quoted.text
    assert bought.status_code == 201, bought.text
    assert TradeResponse.model_validate(bought.json()).stake_left_cr == to_cr(0)
    assert quoted.json()["shares"] == bought.json()["shares"]
    smallest = buy_shares(Outcome.NO, MIN_TRADE_SHARES)
    details = await refused_alike(client, container, market.id, smallest, 409, "LIMIT_EXCEEDED")
    assert (details["stake_left_cr"], details["required_cr"]) == (
        "0",
        format_cr(
            container.quotes.price(await stored(container, market.id), smallest).amount_micro
        ),
    )
    told = await get_quote(client, market.id, smallest)
    assert told.json()["message"] == "You have reached the limit of 100 tokens in this market."
    await assert_books_balance(container.uow_factory)


async def test_a_typed_amount_above_the_balance_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """`INSUFFICIENT_BALANCE` with what the account holds and what the buy takes: for a
    budget that is the budget, for a number of shares their cost, which lmsr rounds
    up. A buy for the whole balance, half a credit included, is quoted and executes."""
    market = await factory.market()
    balance = 20 * MICRO + MICRO // 2
    poor = await factory.user(balance_micro=balance)
    as_user(poor)

    details = await refused_alike(
        client, container, market.id, buy_for(Outcome.YES, 21 * MICRO), 409, "INSUFFICIENT_BALANCE"
    )
    assert details == {"balance_cr": format_cr(balance), "required_cr": format_cr(21 * MICRO)}
    told = await get_quote(client, market.id, buy_for(Outcome.YES, 21 * MICRO))
    assert told.json()["message"] == "This costs 21 tokens. You have 20.5 tokens."
    one_micro_more = await refused_alike(
        client, container, market.id, buy_for(Outcome.YES, balance + 1), 409, "INSUFFICIENT_BALANCE"
    )
    assert one_micro_more["required_cr"] == format_cr(balance + 1)

    by_shares = buy_shares(Outcome.YES, 2 * balance)
    cost = lmsr_quote(market, by_shares).amount_micro
    assert balance < cost <= STAKE_LIMIT, "the balance refuses this buy, not the stake limit"
    details = await refused_alike(
        client, container, market.id, by_shares, 409, "INSUFFICIENT_BALANCE"
    )
    assert details == {"balance_cr": format_cr(balance), "required_cr": format_cr(cost)}
    told = await get_quote(client, market.id, by_shares)
    assert told.json()["message"] == f"This costs {format_tokens(cost)}. You have 20.5 tokens."

    everything_they_have = buy_for(Outcome.YES, balance)
    quoted = await get_quote(client, market.id, everything_they_have)
    bought = await post_trade(client, market.id, everything_they_have)
    assert (quoted.status_code, quoted.json()["amount_cr"]) == (200, format_cr(balance))
    assert bought.status_code == 201, bought.text
    assert await factory.balance_micro(poor) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_size_below_the_minimum_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """`TRADE_TOO_SMALL` in each of the three modes, one micro-unit below its minimum,
    and the minimum itself is quoted."""
    as_user(holder)

    for size, sentence in (
        (buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1), "The smallest trade is 1 token."),
        (buy_shares(Outcome.YES, MIN_TRADE_SHARES - 1), "The smallest trade is 1 share."),
        (sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), "The smallest trade is 1 share."),
    ):
        details = await refused_alike(client, container, traded.id, size, 422, "TRADE_TOO_SMALL")
        assert details == {}
        told = await get_quote(client, traded.id, size)
        assert told.json()["message"] == sentence

    for size in (
        buy_for(Outcome.YES, MIN_TRADE_AMOUNT),
        buy_shares(Outcome.YES, MIN_TRADE_SHARES),
        sell_shares(Outcome.YES, MIN_TRADE_SHARES),
    ):
        assert (await get_quote(client, traded.id, size)).status_code == 200
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "status",
    [MarketStatus.HALTED, MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.VOID],
)
async def test_a_market_that_is_not_open_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    status: MarketStatus,
) -> None:
    """`MARKET_NOT_OPEN`, for somebody with the tokens to buy and for somebody with
    shares to sell: no trading means none."""
    market = await factory.market(status=status)
    trader = await factory.user(balance_micro=WALLET)
    sizes = [buy_for(Outcome.YES, 25 * MICRO)]
    if status in (MarketStatus.HALTED, MarketStatus.CLOSED):
        await factory.position(trader, market, Outcome.YES, shares_micro=20 * MICRO)
        sizes.append(sell_shares(Outcome.YES, 10 * MICRO))
    as_user(trader)

    for size in sizes:
        details = await refused_alike(client, container, market.id, size, 409, "MARKET_NOT_OPEN")
        assert details == {}
    await assert_books_balance(container.uow_factory)


async def test_an_open_market_gives_quotes_until_its_deadline_and_none_from_then_on(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    clock: FrozenClock,
) -> None:
    """A.4: nothing flips the column at `close_at`; the clock is asked with every
    quote, as it is with every trade."""
    market = await factory.market()
    as_user(await factory.user(balance_micro=WALLET))
    size = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)

    clock.set(market.close_at - timedelta(microseconds=1))
    assert (await get_quote(client, market.id, size)).status_code == 200

    clock.set(market.close_at)
    assert await refused_alike(client, container, market.id, size, 409, "MARKET_NOT_OPEN") == {}


async def test_the_proposer_of_a_market_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """A.4, self-dealing: 403 with its own sentence, for a buy and for a sell."""
    proposer = await factory.user(balance_micro=WALLET)
    own = await factory.market(created_by=proposer)
    # The factory applies no rule, so the proposer can be made to hold shares here.
    await factory.position(proposer, own, Outcome.YES, shares_micro=20 * MICRO)
    as_user(proposer)

    for size in (buy_for(Outcome.NO, 25 * MICRO), sell_shares(Outcome.YES, 10 * MICRO)):
        assert await refused_alike(client, container, own.id, size, 403, "FORBIDDEN") == {}
        refusal = await get_quote(client, own.id, size)
        assert refusal.json()["message"] == OWN_MARKET_MESSAGE

    # The page said so beforehand, and offered nothing.
    detail = MarketDetailResponse.model_validate(
        (await client.get(f"/api/markets/{own.id}")).json()
    )
    assert (detail.viewer.can_trade, detail.viewer.buy_slider) == (False, None)
    await assert_books_balance(container.uow_factory)


async def test_a_sell_of_more_than_the_asker_holds_gets_from_the_quote_what_the_trade_answers(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """`INSUFFICIENT_SHARES`, by the asker's own position: one micro-share more than
    they hold of YES, and any NO at all, of which the market has sold plenty to
    somebody else. All they hold is quoted."""
    as_user(holder)

    for size in (
        sell_shares(Outcome.YES, YES_SOLD + 1),
        sell_shares(Outcome.NO, MIN_TRADE_SHARES),
        sell_shares(Outcome.NO, NO_SOLD),
    ):
        details = await refused_alike(
            client, container, traded.id, size, 409, "INSUFFICIENT_SHARES"
        )
        assert details == {}

    everything_held = sell_shares(Outcome.YES, YES_SOLD)
    quoted = await get_quote(client, traded.id, everything_held)
    assert quoted.status_code == 200, quoted.text
    assert quoted.json() == shown(
        container.market_view, traded, lmsr_quote(traded, everything_held)
    ).model_dump(mode="json")
    await assert_books_balance(container.uow_factory)


async def test_a_quote_names_the_first_thing_that_is_wrong_as_its_trade_does(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """In the order of the steps of SPEC §8, without the guard a quote does not take:
    an unknown market, self-dealing, a market that does not trade, shares not held, a
    size too small, the stake limit, the balance."""
    poor = await factory.user(balance_micro=MIN_TRADE_AMOUNT)
    as_user(poor)
    market = await factory.market()
    own_halted = await factory.market(status=MarketStatus.HALTED, created_by=poor)
    hidden = await factory.market(status=MarketStatus.PROPOSED, created_by=poor)
    halted = await factory.market(status=MarketStatus.HALTED)
    too_small = MIN_TRADE_AMOUNT - 1

    ladder = [
        (hidden, buy_for(Outcome.YES, too_small), 404, "NOT_FOUND"),
        (own_halted, sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), 403, "FORBIDDEN"),
        (halted, sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), 409, "MARKET_NOT_OPEN"),
        (market, sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1), 409, "INSUFFICIENT_SHARES"),
        (market, buy_for(Outcome.YES, too_small), 422, "TRADE_TOO_SMALL"),
        (market, buy_for(Outcome.YES, 2 * STAKE_LIMIT), 409, "LIMIT_EXCEEDED"),
        (market, buy_for(Outcome.YES, STAKE_LIMIT), 409, "INSUFFICIENT_BALANCE"),
    ]
    assert len(ladder) < MAX_ORDERS, "or the rate limit would answer the trades first"

    for asked, size, status_code, code in ladder:
        await refused_alike(client, container, asked.id, size, status_code, code)

    assert await count_rows(container.uow_factory, Trade) == 0
    await assert_books_balance(container.uow_factory)


async def test_every_position_of_the_buy_slider_is_quoted_and_the_step_beyond_it_is_not(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """The slider of the market page, the quote and the trade are one rule. For
    somebody the stake limit stops and for somebody the balance stops: the first and
    the last position are quoted, one step past the last is refused for the reason
    that ended the slider there, and the last position is a buy that executes."""
    step = container.settings.pricing.stake_step_micro
    market = await factory.market()
    staked = await factory.user(balance_micro=WALLET)
    await factory.position(
        staked, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=60 * MICRO + MICRO // 4
    )
    short = await factory.user(balance_micro=17 * MICRO + MICRO // 2)

    for trader, stops_at in ((staked, "LIMIT_EXCEEDED"), (short, "INSUFFICIENT_BALANCE")):
        as_user(trader)
        detail = MarketDetailResponse.model_validate(
            (await client.get(f"/api/markets/{market.id}")).json()
        )
        slider = detail.viewer.buy_slider
        assert slider is not None
        assert slider.step * MICRO == step
        first, last = slider.min * MICRO, slider.max * MICRO
        assert first < last

        for position in (first, last):
            quoted = await get_quote(client, market.id, buy_for(Outcome.NO, position))
            assert quoted.status_code == 200, quoted.text
            assert quoted.json()["amount_cr"] == format_cr(position)
        await refused_alike(
            client, container, market.id, buy_for(Outcome.NO, last + step), 409, stops_at
        )
        bought = await post_trade(client, market.id, buy_for(Outcome.NO, last))
        assert bought.status_code == 201, bought.text

    await assert_books_balance(container.uow_factory)


async def test_what_can_buy_says_is_what_the_quote_and_the_trade_answer(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """`viewer.can_buy` of the market page, the quote and the trade are one rule. At the
    stake limit and with an empty account it is false, and the smallest buy is refused
    for the reason that made it so. With the smallest buy and half a credit more left
    it is true, and all of what is left is a buy that executes."""
    pricing = container.settings.pricing
    limit, smallest = pricing.max_stake_per_user_market_micro, pricing.min_trade_micro
    market = await factory.market()
    at_the_limit = await factory.user(balance_micro=WALLET)
    await factory.position(
        at_the_limit, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=limit
    )
    broke = await factory.user(balance_micro=0)
    left = smallest + MICRO // 2
    nearly = await factory.user(balance_micro=WALLET)
    await factory.position(
        nearly, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=limit - left
    )

    async def can_buy(trader: User) -> bool:
        as_user(trader)
        detail = MarketDetailResponse.model_validate(
            (await client.get(f"/api/markets/{market.id}")).json()
        )
        return detail.viewer.can_buy

    for trader, code in ((at_the_limit, "LIMIT_EXCEEDED"), (broke, "INSUFFICIENT_BALANCE")):
        assert not await can_buy(trader)
        await refused_alike(client, container, market.id, buy_for(Outcome.NO, smallest), 409, code)

    assert await can_buy(nearly)
    quoted = await get_quote(client, market.id, buy_for(Outcome.NO, left))
    assert quoted.status_code == 200, quoted.text
    bought = await post_trade(client, market.id, buy_for(Outcome.NO, left))
    assert bought.status_code == 201, bought.text
    assert not await can_buy(nearly)

    await assert_books_balance(container.uow_factory)


async def test_every_position_of_the_slider_of_each_outcome_is_quoted_and_the_step_beyond_is_not(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """`viewer.buy` of the market page, the quote and the trade are one rule, in shares
    and per outcome. For somebody the stake limit stops and for somebody the balance
    stops: the first and the last position of each slider are quoted and pay their
    shares, one step past the last is refused for the reason that ended the slider
    there, and the last position is a buy that executes."""
    step = container.settings.pricing.share_step_micro
    market = await factory.market()
    staked = await factory.user(balance_micro=WALLET)
    await factory.position(
        staked, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=60 * MICRO + MICRO // 4
    )
    short = await factory.user(balance_micro=17 * MICRO + MICRO // 2)

    for trader, stops_at in ((staked, "LIMIT_EXCEEDED"), (short, "INSUFFICIENT_BALANCE")):
        as_user(trader)
        detail = MarketDetailResponse.model_validate(
            (await client.get(f"/api/markets/{market.id}")).json()
        )
        assert [entry.outcome for entry in detail.viewer.buy] == [Outcome.YES, Outcome.NO]
        last = 0
        for entry in detail.viewer.buy:
            slider = entry.slider
            assert slider is not None
            assert slider.step * MICRO == step
            first, last = slider.min * MICRO, slider.max * MICRO
            assert first < last

            for position in (first, last):
                quoted = await get_quote(client, market.id, buy_shares(entry.outcome, position))
                assert quoted.status_code == 200, quoted.text
                assert quoted.json()["shares"] == format_cr(position)
                assert quoted.json()["payout_cr"] == format_cr(position)
            await refused_alike(
                client, container, market.id, buy_shares(entry.outcome, last + step), 409, stops_at
            )
        bought = await post_trade(client, market.id, buy_shares(Outcome.NO, last))
        assert bought.status_code == 201, bought.text
        assert bought.json()["shares"] == format_cr(last)

    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "hidden",
    [None, MarketStatus.PROPOSED, MarketStatus.REJECTED],
    ids=["unknown", "proposed", "rejected"],
)
async def test_a_market_that_is_unknown_proposed_or_rejected_has_no_quote(
    client: httpx.AsyncClient,
    factory: Factory,
    as_user: ActAs,
    hidden: MarketStatus | None,
) -> None:
    """Also for its proposer and for an admin: neither status trades."""
    proposer = await factory.user()
    market_id = (
        uuid7() if hidden is None else (await factory.market(status=hidden, created_by=proposer)).id
    )

    for who in (await factory.user(), proposer, await factory.admin()):
        as_user(who)
        refused = await get_quote(client, market_id, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))
        assert assert_error(refused, 404, "NOT_FOUND") == {}


@pytest.mark.redis_down
async def test_a_quote_needs_no_redis(
    client: httpx.AsyncClient,
    container: Container,
    holder: User,
    traded: Market,
    as_user: ActAs,
) -> None:
    """SPEC §9: Redis never decides a price."""
    as_user(holder)
    size = buy_shares(Outcome.NO, 30 * MICRO)

    response = await get_quote(client, traded.id, size)

    assert response.status_code == 200, response.text
    assert response.json() == shown(
        container.market_view, traded, lmsr_quote(traded, size)
    ).model_dump(mode="json")
