"""Worked examples for the LMSR module (SPEC §4, §12 "Pricing" and "Stake limit").

Every number here can be checked by hand or against the table in SPEC §4 "Choosing b".
The rules that must hold for every input are in `test_lmsr_properties.py`.
"""

from collections.abc import Callable
from decimal import ROUND_UP, Decimal, getcontext, localcontext
from fractions import Fraction

import pytest

from app.core.enums import Outcome, TradeSide
from app.core.units import MICRO, display_probability, to_cr, to_micro
from app.pricing import lmsr
from app.pricing.lmsr import MarketState, TradeQuote

CR = MICRO
DEFAULT_B = 10 * CR
FRESH = MarketState(q_yes=0, q_no=0, b=DEFAULT_B)
BIGINT_MAX = 2**63 - 1
CENT = Decimal("0.01")

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


def test_fresh_market_is_exactly_fifty_fifty() -> None:
    assert lmsr.price_yes(FRESH) == Decimal("0.5")
    assert lmsr.price_yes_micro(FRESH) == 500_000


def test_cost_of_a_fresh_market_is_b_ln_2() -> None:
    assert Decimal("6931471.805599") < lmsr.cost(FRESH) < Decimal("6931471.805600")


@pytest.mark.parametrize(
    ("b_cr", "loss_bound_cr", "shares_for_one_cr", "price_after", "price_decimals"),
    [
        ("5", "3.47", "1.83", 0.591, 1),
        ("10", "6.93", "1.91", 0.548, 1),
        ("20", "13.86", "1.95", 0.524, 1),
        ("1442.695", "1000.00", "2.00", 0.5003, 2),
    ],
)
def test_choosing_b_table_of_spec_section_4_is_reproduced(
    b_cr: str,
    loss_bound_cr: str,
    shares_for_one_cr: str,
    price_after: float,
    price_decimals: int,
) -> None:
    b = to_micro(b_cr)

    quote = lmsr.quote_buy_for_budget(MarketState(q_yes=0, q_no=0, b=b), Outcome.YES, CR)

    assert to_cr(lmsr.max_loss_micro(b)).quantize(CENT) == Decimal(loss_bound_cr)
    assert to_cr(quote.shares_micro).quantize(CENT) == Decimal(shares_for_one_cr)
    assert display_probability(quote.p_yes_after_micro, price_decimals) == price_after


def test_one_credit_at_default_b_moves_the_price_from_50_to_about_55_percent() -> None:
    quote = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, CR)

    assert quote == TradeQuote(
        side=TradeSide.BUY,
        outcome=Outcome.YES,
        shares_micro=1_909_028,
        amount_micro=1_000_000,
        before=FRESH,
        after=MarketState(q_yes=1_909_028, q_no=0, b=DEFAULT_B),
        p_yes_before_micro=500_000,
        p_yes_after_micro=547_581,
    )


def test_buying_no_is_the_mirror_image_of_buying_yes() -> None:
    quote = lmsr.quote_buy_for_budget(FRESH, Outcome.NO, CR)

    assert quote.after == MarketState(q_yes=0, q_no=1_909_028, b=DEFAULT_B)
    assert (quote.shares_micro, quote.amount_micro) == (1_909_028, 1_000_000)
    assert quote.p_yes_after_micro == MICRO - 547_581


def test_selling_half_returns_the_exact_reverse_cost_rounded_down() -> None:
    bought = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, CR)
    half = bought.shares_micro // 2

    sold = lmsr.quote_sell_shares(bought.after, Outcome.YES, half)
    bought_back = lmsr.quote_buy_shares(sold.after, Outcome.YES, half)

    assert sold.side is TradeSide.SELL
    assert bought_back.after == bought.after
    assert (sold.amount_micro, bought_back.amount_micro) == (511_358, 511_359)


def test_stake_freed_by_a_sale_can_be_bought_again_without_exceeding_the_limit() -> None:
    limit = CR
    first = lmsr.quote_buy_for_budget(FRESH, Outcome.YES, limit)
    sold = lmsr.quote_sell_shares(first.after, Outcome.YES, first.shares_micro // 2)
    net_stake = first.amount_micro - sold.amount_micro

    second = lmsr.quote_buy_for_budget(sold.after, Outcome.YES, limit - net_stake)

    assert first.amount_micro <= limit
    assert second.shares_micro > 0
    assert net_stake + second.amount_micro <= limit


def test_b_from_budget_of_1000_credits_is_the_maximum_allowed_b() -> None:
    assert lmsr.b_from_budget_micro(1000 * CR) == 1_442_695_040


def test_max_loss_at_default_b_is_6_931472_credits() -> None:
    assert lmsr.max_loss_micro(10 * CR) == 6_931_472


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
