"""Pricing a trade without executing it (SPEC §4, §10, Appendix A.4, A.9).

A trade is sized in one of three ways, the request modes of A.4: a buy for an amount, a
buy of shares, a sell of shares. `QuoteService.price` prices each with the LMSR function
made for it, on the quantities of the market row it is given; `to_response` shows the
result in credits, shares and display probabilities; `quote` is the two on the row as
Postgres has it now, for a market that trades.

Every expected figure is what `app.pricing.lmsr` gives for the same market, at the
precision of the settings, shown the way `MarketView` shows it.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app.container import Container
from app.core.enums import MarketStatus, MarketTier, Outcome, TradeSide
from app.core.errors import InsufficientSharesError, TradeTooSmallError
from app.core.units import MICRO, format_cr, to_cr
from app.models import Market, Trade
from app.pricing import lmsr
from app.schemas.common import REQUEST_INTEGER_DIGITS
from app.schemas.trading import QuoteResponse, TradeSize
from app.services.market_view import MarketView
from app.services.quotes import QuoteService
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
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

pytestmark = pytest.mark.integration

MIN_TRADE_AMOUNT: Final[int] = load_base_settings().pricing.min_trade_micro
"""MIN_TRADE_CR in µCR: the smallest trade that is sized by an amount."""

LARGEST_REQUEST: Final[int] = 10**REQUEST_INTEGER_DIGITS * MICRO - 1
"""The largest size or budget the request model lets through, in micro-units."""

YES_SOLD: Final[int] = 120 * MICRO
NO_SOLD: Final[int] = 45 * MICRO
"""What the traded market of these tests has sold of each outcome."""

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
    q_yes_micro: int, q_no_micro: int, *, b_micro: int = DEFAULT_B, version: int = 0
) -> Market:
    """A market row as far as pricing reads it: its quantities, its b and its version.
    It is in no database."""
    return Market(
        id=uuid7(),
        q_yes_micro=q_yes_micro,
        q_no_micro=q_no_micro,
        b_micro=b_micro,
        version=version,
    )


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


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is that error; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["code"] == code
    details: dict[str, Any] = body["details"]
    return details


@pytest.fixture
async def traded(factory: Factory) -> Market:
    """An OPEN market of the DEFAULT tier that sold `YES_SOLD` and `NO_SOLD`."""
    market = await factory.market()
    alice = await factory.user(balance_micro=YES_SOLD)
    bob = await factory.user(balance_micro=NO_SOLD)
    await factory.position(alice, market, Outcome.YES, shares_micro=YES_SOLD)
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

    with pytest.raises(TradeTooSmallError):
        container.quotes.price(market, buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1))

    at_the_minimum = container.quotes.price(market, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))
    assert at_the_minimum.amount_micro == MIN_TRADE_AMOUNT


@pytest.mark.parametrize("sizing", [buy_shares, sell_shares], ids=["buy", "sell"])
def test_a_trade_sized_by_shares_is_at_least_min_trade_shares(
    container: Container, sizing: Sizing
) -> None:
    market = market_at(YES_SOLD, NO_SOLD)

    with pytest.raises(TradeTooSmallError):
        container.quotes.price(market, sizing(Outcome.YES, MIN_TRADE_SHARES - 1))

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
    with pytest.raises(TradeTooSmallError):
        quotes.price(market, buy_for(Outcome.YES, two_credits - 1))
    for sizing in (buy_shares, sell_shares):
        assert quotes.price(market, sizing(Outcome.YES, five_shares)).shares_micro == five_shares
        with pytest.raises(TradeTooSmallError):
            quotes.price(market, sizing(Outcome.YES, five_shares - 1))


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


# --- quote: GET /markets/{id}/quote -----------------------------------------------------------


@pytest.mark.parametrize(("sizing", "size_micro"), SIZINGS)
async def test_the_quote_of_each_request_mode_is_what_lmsr_gives_for_the_market_as_it_stands(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    traded: Market,
    as_user: ActAs,
    sizing: Sizing,
    size_micro: int,
) -> None:
    as_user(await factory.user())
    size = sizing(Outcome.YES, size_micro)

    response = await get_quote(client, traded.id, size)

    assert response.status_code == 200, response.text
    expected = shown(container.market_view, traded, lmsr_quote(traded, size))
    assert QuoteResponse.model_validate(response.json()) == expected
    assert response.json() == expected.model_dump(mode="json")
    assert response.json()["quote_version"] == traded.version == 2


async def test_a_quote_is_about_nobody_in_particular(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    traded: Market,
    as_user: ActAs,
) -> None:
    """No balance, no stake limit, no holding and no self-dealing rule is checked: the
    one who asks may have nothing, and may even be the proposer."""
    penniless = await factory.user()
    own_market = await factory.market(created_by=penniless)
    as_user(penniless)
    view = container.market_view

    for market, size in (
        (traded, buy_for(Outcome.YES, 2 * STAKE_LIMIT)),
        (traded, sell_shares(Outcome.NO, NO_SOLD)),
        (own_market, buy_for(Outcome.YES, MIN_TRADE_AMOUNT)),
    ):
        response = await get_quote(client, market.id, size)

        assert response.status_code == 200, response.text
        assert response.json() == shown(view, market, lmsr_quote(market, size)).model_dump(
            mode="json"
        )


async def test_a_quote_comes_from_the_quantities_of_this_moment(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    traded: Market,
    as_user: ActAs,
) -> None:
    """Not cached: the quote after a trade is priced on what that trade left."""
    as_user(await factory.user())
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


async def test_a_quote_writes_nothing(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    traded: Market,
    as_user: ActAs,
) -> None:
    as_user(await factory.user())

    for sizing, size_micro in ((buy_for, 25 * MICRO), (sell_shares, 20 * MICRO)):
        assert (
            await get_quote(client, traded.id, sizing(Outcome.YES, size_micro))
        ).status_code == 200

    async with container.uow_factory() as uow:
        stored = await uow.markets.get(traded.id)
    assert stored is not None
    assert (stored.q_yes_micro, stored.q_no_micro, stored.version) == (YES_SOLD, NO_SOLD, 2)
    assert await count_rows(container.uow_factory, Trade) == 2, "the two of the fixture"
    await assert_books_balance(container.uow_factory)


async def test_a_quote_below_the_smallest_trade_is_trade_too_small(
    client: httpx.AsyncClient, factory: Factory, traded: Market, as_user: ActAs
) -> None:
    as_user(await factory.user())

    for size in (
        buy_for(Outcome.YES, MIN_TRADE_AMOUNT - 1),
        buy_shares(Outcome.YES, MIN_TRADE_SHARES - 1),
        sell_shares(Outcome.YES, MIN_TRADE_SHARES - 1),
    ):
        details = assert_error(await get_quote(client, traded.id, size), 422, "TRADE_TOO_SMALL")
        assert details == {}


async def test_a_quote_for_more_shares_than_the_market_has_sold_is_insufficient_shares(
    client: httpx.AsyncClient, factory: Factory, traded: Market, as_user: ActAs
) -> None:
    as_user(await factory.user())

    refused = await get_quote(client, traded.id, sell_shares(Outcome.NO, NO_SOLD + 1))

    assert assert_error(refused, 409, "INSUFFICIENT_SHARES") == {}


@pytest.mark.parametrize(
    "status",
    [MarketStatus.HALTED, MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.VOID],
)
async def test_a_market_that_does_not_trade_gives_no_quote(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs, status: MarketStatus
) -> None:
    market = await factory.market(status=status)
    as_user(await factory.user())

    refused = await get_quote(client, market.id, buy_for(Outcome.YES, MIN_TRADE_AMOUNT))

    assert assert_error(refused, 409, "MARKET_NOT_OPEN") == {}


async def test_an_open_market_gives_quotes_until_its_deadline_and_none_from_then_on(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs, clock: FrozenClock
) -> None:
    """A.4: nothing flips the column at `close_at`; the clock is asked with every quote."""
    market = await factory.market()
    as_user(await factory.user())
    size = buy_for(Outcome.YES, MIN_TRADE_AMOUNT)

    clock.set(market.close_at - timedelta(microseconds=1))
    assert (await get_quote(client, market.id, size)).status_code == 200

    clock.set(market.close_at)
    assert_error(await get_quote(client, market.id, size), 409, "MARKET_NOT_OPEN")


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
    factory: Factory,
    traded: Market,
    as_user: ActAs,
) -> None:
    """SPEC §9: Redis never decides a price."""
    as_user(await factory.user())
    size = buy_shares(Outcome.NO, 30 * MICRO)

    response = await get_quote(client, traded.id, size)

    assert response.status_code == 200, response.text
    assert response.json() == shown(
        container.market_view, traded, lmsr_quote(traded, size)
    ).model_dump(mode="json")
