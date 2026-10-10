"""Worked examples for the LMSR module (SPEC §4, §12 "Pricing" and "Stake limit").

They are set in the economy of `.env`: a market of the DEFAULT tier, and a buy for the
whole of MAX_STAKE_PER_USER_MARKET. Every number here can be checked by hand or against
the table in SPEC §4 "Choosing b". The rules that must hold for every input are in
`test_lmsr_properties.py`.
"""

from collections.abc import Callable
from decimal import ROUND_UP, Decimal, getcontext, localcontext
from fractions import Fraction

import pytest

from app.core.config import Settings
from app.core.enums import MarketTier, Outcome, TradeSide
from app.core.units import MICRO, ceil_micro, probability_to_micro
from app.pricing import lmsr
from app.pricing.lmsr import MarketState, TradeQuote
from tests.support.economy import MARKET_B, MAX_B, MAX_MARKET_BUDGET

CR = MICRO
DEFAULT_B = 1000 * CR
"""b of the DEFAULT tier, LMSR_TIER_DEFAULT_B."""
FULL_STAKE = 100 * CR
"""MAX_STAKE_PER_USER_MARKET: the most a user may have at stake in one market."""
FRESH = MarketState(q_yes=0, q_no=0, b=DEFAULT_B)
BIGINT_MAX = 2**63 - 1
REFERENCE_PRECISION = 100

STATE_FUNCTIONS: dict[str, Callable[[MarketState], object]] = {
    "price_yes": lmsr.price_yes,
    "price_yes_micro": lmsr.price_yes_micro,
    "cost": lmsr.cost,
    "quote_buy_for_budget": lambda state: lmsr.quote_buy_for_budget(state, Outcome.YES, CR),
    "quote_buy_shares": lambda state: lmsr.quote_buy_shares(state, Outcome.YES, CR),
    "quote_sell_shares": lambda state: lmsr.quote_sell_shares(state, Outcome.YES, CR),
}

AMOUNT_FUNCTIONS: dict[str, Callable[[int], object]] = {
    "quote_buy_for_budget": lambda amount: lmsr.quote_buy_for_budget(FRESH, Outcome.NO, amount),
    "quote_buy_shares": lambda amount: lmsr.quote_buy_shares(FRESH, Outcome.NO, amount),
    "quote_sell_shares": lambda amount: lmsr.quote_sell_shares(FRESH, Outcome.NO, amount),
    "max_loss_micro": lmsr.max_loss_micro,
    "b_from_budget_micro": lmsr.b_from_budget_micro,
}


def test_the_examples_are_set_in_the_economy_of_the_settings(base_settings: Settings) -> None:
    assert base_settings.pricing.tier_b_micro(MarketTier.DEFAULT) == DEFAULT_B
    assert base_settings.pricing.max_stake_per_user_market_micro == FULL_STAKE


def test_fresh_market_is_exactly_fifty_fifty() -> None:
    assert lmsr.price_yes(FRESH) == Decimal("0.5")
    assert lmsr.price_yes_micro(FRESH) == 500_000


def test_cost_of_a_fresh_market_is_b_ln_2() -> None:
    assert Decimal("693147180.559945") < lmsr.cost(FRESH) < Decimal("693147180.559946")


def test_each_row_of_the_choosing_b_table_follows_from_the_cost_function() -> None:
    """SPEC §4 "Choosing b" has a row for each fixed tier and one for the largest b a
    market can have. A row states the loss bound b · ln 2, the shares a full stake buys
    in a fresh market, and the price after that buy.

    No figure is typed in here. The rows are the b values of the settings, and what a
    row states is computed from the cost and the price function with twice the digits,
    which the quote functions then have to agree with.
    """
    rows: list[tuple[int, int, int, int]] = []
    for b in sorted(MARKET_B):
        fresh = MarketState(q_yes=0, q_no=0, b=b)
        quote = lmsr.quote_buy_for_budget(fresh, Outcome.YES, FULL_STAKE)
        one_share_more = MarketState(q_yes=quote.shares_micro + 1, q_no=0, b=b)

        with localcontext() as reference:
            reference.prec = 2 * REFERENCE_PRECISION
            start = lmsr.cost(fresh, precision=REFERENCE_PRECISION)
            paid = lmsr.cost(quote.after, precision=REFERENCE_PRECISION) - start
            for_more = lmsr.cost(one_share_more, precision=REFERENCE_PRECISION) - start
            price_after = lmsr.price_yes(quote.after, precision=REFERENCE_PRECISION)

            # The loss bound is what a fresh market has already cost, rounded up.
            assert lmsr.max_loss_micro(b) == ceil_micro(start)
            # A full stake buys the most shares it pays for: one more costs too much.
            assert paid <= FULL_STAKE < for_more
            assert quote.p_yes_after_micro == probability_to_micro(price_after)
        rows.append((b, lmsr.max_loss_micro(b), quote.shares_micro, quote.p_yes_after_micro))

    # Down the table b grows. With it the loss bound grows, a stake buys more shares,
    # and it moves the price less.
    loss_bounds = [loss_bound for _, loss_bound, _, _ in rows]
    shares_for_a_stake = [shares for _, _, shares, _ in rows]
    prices_after = [price for _, _, _, price in rows]
    assert loss_bounds == sorted(loss_bounds)
    assert shares_for_a_stake == sorted(shares_for_a_stake)
    assert prices_after == sorted(prices_after, reverse=True)
    # In a fresh market a share costs between half a credit and a whole one, and a buy
    # takes the price above the 50% it started at.
    assert all(FULL_STAKE < shares < 2 * FULL_STAKE for shares in shares_for_a_stake)
    assert all(MICRO // 2 < price < MICRO for price in prices_after)
    # The last row is the largest market: its loss bound is the whole of the budget.
    assert rows[-1][:2] == (MAX_B, MAX_MARKET_BUDGET)


def test_a_full_stake_at_default_b_moves_the_price_from_50_to_about_55_percent() -> None:
    quote = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, FULL_STAKE)

    assert quote == TradeQuote(
        side=TradeSide.BUY,
        outcome=Outcome.YES,
        shares_micro=190_902_828,
        amount_micro=100_000_000,
        before=FRESH,
        after=MarketState(q_yes=190_902_828, q_no=0, b=DEFAULT_B),
        p_yes_before_micro=500_000,
        p_yes_after_micro=547_581,
    )


def test_buying_no_is_the_mirror_image_of_buying_yes() -> None:
    quote = lmsr.quote_buy_for_budget(FRESH, Outcome.NO, FULL_STAKE)

    assert quote.after == MarketState(q_yes=0, q_no=190_902_828, b=DEFAULT_B)
    assert (quote.shares_micro, quote.amount_micro) == (190_902_828, 100_000_000)
    assert quote.p_yes_after_micro == MICRO - 547_581


def test_selling_half_returns_the_exact_reverse_cost_rounded_down() -> None:
    bought = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, FULL_STAKE)
    half = bought.shares_micro // 2

    sold = lmsr.quote_sell_shares(bought.after, Outcome.YES, half)
    bought_back = lmsr.quote_buy_shares(sold.after, Outcome.YES, half)

    assert sold.side is TradeSide.SELL
    assert bought_back.after == bought.after
    assert (sold.amount_micro, bought_back.amount_micro) == (51_135_853, 51_135_854)


def test_stake_freed_by_a_sale_can_be_bought_again_without_exceeding_the_limit() -> None:
    limit = FULL_STAKE
    first = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, limit)
    sold = lmsr.quote_sell_shares(first.after, Outcome.YES, first.shares_micro // 2)
    net_stake = first.amount_micro - sold.amount_micro

    second = lmsr.quote_buy_for_budget(sold.after, Outcome.YES, limit - net_stake)

    assert first.amount_micro <= limit
    assert second.shares_micro > 0
    assert net_stake + second.amount_micro <= limit


def test_b_from_the_largest_market_budget_is_the_largest_b_a_market_can_have() -> None:
    """MAX_MARKET_BUDGET is 5,000 CR, and 5,000 / ln 2 is 7,213.475204..."""
    assert MAX_MARKET_BUDGET == 5000 * CR
    assert lmsr.b_from_budget_micro(MAX_MARKET_BUDGET) == MAX_B == 7_213_475_204


def test_max_loss_at_default_b_is_693_147181_credits() -> None:
    assert lmsr.max_loss_micro(DEFAULT_B) == 693_147_181


@pytest.mark.parametrize("ratio", [50, 115, 500, 10**6, 10**12])
def test_q_over_b_of_50_and_far_beyond_neither_overflows_nor_loses_the_complement(
    ratio: int,
) -> None:
    yes_ahead = MarketState(q_yes=ratio * CR, q_no=0, b=CR)
    no_ahead = MarketState(q_yes=0, q_no=ratio * CR, b=CR)

    p_high, p_low = lmsr.price_yes(yes_ahead), lmsr.price_yes(no_ahead)

    assert Fraction(p_high) + Fraction(p_low) == 1
    assert 0 <= p_low < Decimal("1e-21") < Decimal("0.5") < p_high <= 1
    assert lmsr.price_yes_micro(yes_ahead) + lmsr.price_yes_micro(no_ahead) == MICRO
    assert ratio * CR <= lmsr.cost(yes_ahead) <= ratio * CR + lmsr.max_loss_micro(CR)
    for outcome in Outcome:
        assert lmsr.quote_buy_for_budget(yes_ahead, outcome, CR).amount_micro <= CR
        assert 1 <= lmsr.quote_buy_shares(yes_ahead, outcome, CR).amount_micro <= CR
        assert 0 <= lmsr.quote_sell_shares(yes_ahead, outcome, CR).amount_micro < CR


def test_price_at_q_over_b_of_50_keeps_its_distance_from_one() -> None:
    p_yes = lmsr.price_yes(MarketState(q_yes=50 * DEFAULT_B, q_no=0, b=DEFAULT_B))

    assert Decimal("1.9287e-22") < 1 - p_yes < Decimal("1.9288e-22")


def test_largest_bigint_quantities_are_priced() -> None:
    decided = MarketState(q_yes=BIGINT_MAX, q_no=0, b=1)

    favourite = lmsr.quote_buy_shares(decided, Outcome.YES, CR)
    underdog = lmsr.quote_buy_shares(decided, Outcome.NO, CR)

    assert lmsr.price_yes(decided) == 1
    assert lmsr.cost(decided) == BIGINT_MAX
    assert (favourite.amount_micro, underdog.amount_micro) == (CR, 1)
    assert lmsr.quote_sell_shares(decided, Outcome.YES, CR).amount_micro == CR - 1
    assert lmsr.quote_sell_shares(decided, Outcome.NO, CR).amount_micro == 0


def test_selling_more_than_was_ever_bought_is_left_to_the_caller() -> None:
    quote = lmsr.quote_sell_shares(FRESH, Outcome.YES, CR)

    assert quote.after.q_yes == -CR
    assert 0 < quote.amount_micro < CR


def test_result_does_not_depend_on_the_callers_decimal_context() -> None:
    expected_quote = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, CR)
    expected_price = lmsr.price_yes(expected_quote.after)

    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_UP
        quote = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, CR)
        price = lmsr.price_yes(quote.after)
        untouched = (getcontext().prec, getcontext().rounding) == (3, ROUND_UP)

    assert quote == expected_quote
    assert price == expected_price
    assert untouched


def test_precision_is_a_keyword_that_sets_the_digits_of_the_result() -> None:
    state = MarketState(q_yes=3 * CR, q_no=CR, b=DEFAULT_B)

    assert len(lmsr.price_yes(state, precision=28).as_tuple().digits) == 28
    assert len(lmsr.price_yes(state).as_tuple().digits) == 50
    assert len(lmsr.cost(state, precision=80).as_tuple().digits) == 80


@pytest.mark.parametrize("function", STATE_FUNCTIONS.values(), ids=STATE_FUNCTIONS.keys())
@pytest.mark.parametrize("b", [0, -1, -DEFAULT_B])
def test_non_positive_b_is_rejected(function: Callable[[MarketState], object], b: int) -> None:
    with pytest.raises(ValueError, match="b must be positive"):
        function(MarketState(q_yes=0, q_no=0, b=b))


@pytest.mark.parametrize("function", STATE_FUNCTIONS.values(), ids=STATE_FUNCTIONS.keys())
@pytest.mark.parametrize(("q_yes", "q_no"), [(-1, 0), (0, -1), (-CR, -CR)])
def test_negative_q_is_rejected(
    function: Callable[[MarketState], object], q_yes: int, q_no: int
) -> None:
    with pytest.raises(ValueError, match="q must not be negative"):
        function(MarketState(q_yes=q_yes, q_no=q_no, b=DEFAULT_B))


@pytest.mark.parametrize("function", AMOUNT_FUNCTIONS.values(), ids=AMOUNT_FUNCTIONS.keys())
@pytest.mark.parametrize("amount", [0, -1, -CR])
def test_non_positive_amount_is_rejected(function: Callable[[int], object], amount: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        function(amount)


def test_non_positive_precision_is_rejected() -> None:
    with pytest.raises(ValueError, match="prec"):
        lmsr.price_yes(FRESH, precision=0)


@pytest.mark.parametrize(
    "call",
    [
        lambda: lmsr.price_yes(MarketState(q_yes=0.5, q_no=0, b=DEFAULT_B)),  # type: ignore[arg-type]
        lambda: lmsr.cost(MarketState(q_yes=CR, q_no=0, b=1e7)),  # type: ignore[arg-type]
        lambda: lmsr.quote_buy_for_budget(FRESH, Outcome.YES, 0.25),  # type: ignore[arg-type]
        lambda: lmsr.quote_buy_shares(FRESH, Outcome.YES, 1.5),  # type: ignore[arg-type]
        lambda: lmsr.quote_sell_shares(FRESH, Outcome.YES, 1.5),  # type: ignore[arg-type]
    ],
    ids=["q", "b", "budget", "shares bought", "shares sold"],
)
def test_a_float_is_an_error_and_never_silently_rounded(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()
