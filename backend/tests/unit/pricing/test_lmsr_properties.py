"""Rules of the LMSR module that must hold for every input (SPEC §4, §12 "Pricing").

Three kinds of market are generated:

* ordinary: b from 1 CR to the largest b SPEC §4 allows, |q|/b up to 50;
* lopsided: |q|/b far beyond 50, where one price is 0 or 1 to all 50 digits;
* extreme: any b and q that a BIGINT column can hold.

Rules about rounding and bounds run on all three. Rules that compare against a reference
formula run on ordinary markets, where that reference can be computed to the last digit.
"""

from decimal import Decimal, localcontext
from fractions import Fraction

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.core.enums import Outcome, TradeSide
from app.core.units import MICRO, ceil_micro, floor_micro
from app.pricing import lmsr
from app.pricing.lmsr import MarketState, TradeQuote

CR = MICRO
MAX_B = 1_442_695_040
"""b of the largest market budget, 1,000 CR (SPEC §4 "Choosing b")."""
BIGINT_MAX = 2**63 - 1
REFERENCE_PRECISION = 150

checks = settings(max_examples=200, deadline=None)

outcomes = st.sampled_from(Outcome)
bigints = st.integers(min_value=0, max_value=BIGINT_MAX)


@st.composite
def markets(draw: st.DrawFn, *, min_b: int, max_ratio: int) -> MarketState:
    b = draw(st.integers(min_value=min_b, max_value=MAX_B))
    q = st.integers(min_value=0, max_value=max_ratio * b)
    return MarketState(q_yes=draw(q), q_no=draw(q), b=b)


ordinary_markets = markets(min_b=CR, max_ratio=50)
lopsided_markets = markets(min_b=1, max_ratio=100_000)
extreme_markets = st.builds(
    MarketState, q_yes=bigints, q_no=bigints, b=st.integers(min_value=1, max_value=BIGINT_MAX)
)
all_markets = st.one_of(ordinary_markets, lopsided_markets, extreme_markets)

ordinary_amounts = st.integers(min_value=1, max_value=100 * CR)
all_amounts = st.one_of(ordinary_amounts, st.integers(min_value=1, max_value=BIGINT_MAX))


def mirrored(state: MarketState) -> MarketState:
    return MarketState(q_yes=state.q_no, q_no=state.q_yes, b=state.b)


def gap(state: MarketState) -> int:
    return abs(state.q_yes - state.q_no)


def sold(state: MarketState, outcome: Outcome) -> int:
    return state.q_yes if outcome is Outcome.YES else state.q_no


def buy(state: MarketState, outcome: Outcome, amount: int, by_budget: bool) -> TradeQuote:
    if by_budget:
        return lmsr.quote_buy_for_budget(state, outcome, amount)
    return lmsr.quote_buy_shares(state, outcome, amount)


def is_mirror_trade(quote: TradeQuote) -> bool:
    """Whether the trade swaps the two prices by moving twice the gap.

    Its amount is then a whole number of µCR, which a reference computed in digits cannot
    round reliably. The mirror trade has its own, exact test below.
    """
    return gap(quote.before) == gap(quote.after)


def spec_price(state: MarketState, outcome: Outcome) -> Decimal:
    """1 / (1 + e^((q_other - q_own)/b)), the price SPEC §4 uses in its closed forms."""
    behind = sold(state, outcome.opposite) - sold(state, outcome)
    return 1 / (1 + (Decimal(behind) / state.b).exp())


def expm1(x: Decimal) -> Decimal:
    return x.exp() - 1


def log1p(x: Decimal) -> Decimal:
    return (1 + x).ln()


@checks
@given(state=all_markets)
def test_yes_and_no_prices_sum_to_exactly_one(state: MarketState) -> None:
    p_yes = lmsr.price_yes(state)
    p_no = lmsr.price_yes(mirrored(state))

    assert 0 <= p_yes <= 1
    assert Fraction(p_yes) + Fraction(p_no) == 1
    assert lmsr.price_yes_micro(state) + lmsr.price_yes_micro(mirrored(state)) == MICRO


@checks
@given(b=st.integers(min_value=1, max_value=BIGINT_MAX), q=bigints)
def test_balanced_market_is_exactly_fifty_fifty(b: int, q: int) -> None:
    for state in (MarketState(q_yes=0, q_no=0, b=b), MarketState(q_yes=q, q_no=q, b=b)):
        assert lmsr.price_yes(state) == Decimal("0.5")
        assert lmsr.price_yes_micro(state) == MICRO // 2


@checks
@given(state=ordinary_markets, amount=ordinary_amounts, by_budget=st.booleans())
def test_buying_yes_strictly_raises_p_yes(state: MarketState, amount: int, by_budget: bool) -> None:
    quote = buy(state, Outcome.YES, amount, by_budget)

    assert lmsr.price_yes(quote.after) > lmsr.price_yes(state)
    assert quote.p_yes_after_micro >= quote.p_yes_before_micro


@checks
@given(state=ordinary_markets, amount=ordinary_amounts, by_budget=st.booleans())
def test_buying_no_strictly_lowers_p_yes(state: MarketState, amount: int, by_budget: bool) -> None:
    quote = buy(state, Outcome.NO, amount, by_budget)

    assert lmsr.price_yes(quote.after) < lmsr.price_yes(state)
    assert quote.p_yes_after_micro <= quote.p_yes_before_micro


@checks
@given(state=all_markets, outcome=outcomes, amount=all_amounts, by_budget=st.booleans())
def test_quote_records_the_transition_it_prices(
    state: MarketState, outcome: Outcome, amount: int, by_budget: bool
) -> None:
    bought = buy(state, outcome, amount, by_budget)
    resold = lmsr.quote_sell_shares(bought.after, outcome, bought.shares_micro)

    assert (bought.side, bought.outcome, bought.before) == (TradeSide.BUY, outcome, state)
    assert sold(bought.after, outcome) == sold(state, outcome) + bought.shares_micro
    assert sold(bought.after, outcome.opposite) == sold(state, outcome.opposite)
    assert bought.after.b == state.b
    assert bought.p_yes_before_micro == lmsr.price_yes_micro(state)
    assert bought.p_yes_after_micro == lmsr.price_yes_micro(bought.after)
    assert (resold.side, resold.outcome) == (TradeSide.SELL, outcome)
    assert (resold.before, resold.after) == (bought.after, state)
    assert (resold.p_yes_before_micro, resold.p_yes_after_micro) == (
        bought.p_yes_after_micro,
        bought.p_yes_before_micro,
    )


@checks
@given(state=ordinary_markets, outcome=outcomes, shares=ordinary_amounts)
def test_cost_of_a_share_buy_is_the_difference_in_c_rounded_up(
    state: MarketState, outcome: Outcome, shares: int
) -> None:
    quote = lmsr.quote_buy_shares(state, outcome, shares)
    assume(not is_mirror_trade(quote))

    with localcontext() as reference:
        reference.prec = 2 * REFERENCE_PRECISION
        growth = lmsr.cost(quote.after, precision=REFERENCE_PRECISION) - lmsr.cost(
            state, precision=REFERENCE_PRECISION
        )

    assert quote.amount_micro == ceil_micro(growth)


@checks
@given(state=ordinary_markets, outcome=outcomes, shares=ordinary_amounts)
def test_cost_of_a_share_buy_follows_the_closed_form_of_spec_section_4(
    state: MarketState, outcome: Outcome, shares: int
) -> None:
    quote = lmsr.quote_buy_shares(state, outcome, shares)
    assume(not is_mirror_trade(quote))

    with localcontext() as reference:
        reference.prec = REFERENCE_PRECISION
        p = spec_price(state, outcome)
        expected = state.b * (1 + p * expm1(Decimal(shares) / state.b)).ln()

    assert quote.amount_micro == ceil_micro(expected)


@checks
@given(state=ordinary_markets, outcome=outcomes, budget=ordinary_amounts)
def test_shares_for_a_budget_follow_the_closed_form_of_spec_section_4(
    state: MarketState, outcome: Outcome, budget: int
) -> None:
    quote = lmsr.quote_buy_for_budget(state, outcome, budget)
    assume(not is_mirror_trade(quote))

    with localcontext() as reference:
        reference.prec = REFERENCE_PRECISION
        p = spec_price(state, outcome)
        expected = state.b * ((expm1(Decimal(budget) / state.b) + p).ln() - p.ln())

    assert quote.shares_micro == floor_micro(expected)


@checks
@given(state=ordinary_markets, outcome=outcomes, data=st.data())
def test_proceeds_of_a_sale_follow_the_closed_form_of_spec_section_4(
    state: MarketState, outcome: Outcome, data: st.DataObject
) -> None:
    assume(sold(state, outcome) > 0)
    shares = data.draw(st.integers(min_value=1, max_value=sold(state, outcome)))
    quote = lmsr.quote_sell_shares(state, outcome, shares)
    assume(not is_mirror_trade(quote))

    with localcontext() as reference:
        reference.prec = REFERENCE_PRECISION
        p = spec_price(state, outcome)
        expected = -state.b * log1p(p * expm1(-Decimal(shares) / state.b))

    assert quote.amount_micro == floor_micro(expected)


@checks
@given(
    b=st.integers(min_value=1, max_value=BIGINT_MAX),
    trailing=st.integers(min_value=0, max_value=BIGINT_MAX // 4),
    lead=st.integers(min_value=1, max_value=BIGINT_MAX // 4),
    outcome=outcomes,
)
def test_buying_twice_the_gap_of_the_trailing_outcome_costs_exactly_the_gap(
    b: int, trailing: int, lead: int, outcome: Outcome
) -> None:
    state = MarketState(q_yes=trailing, q_no=trailing + lead, b=b)
    if outcome is Outcome.NO:
        state = mirrored(state)

    by_shares = lmsr.quote_buy_shares(state, outcome, 2 * lead)
    by_budget = lmsr.quote_buy_for_budget(state, outcome, lead)
    resold = lmsr.quote_sell_shares(by_shares.after, outcome, 2 * lead)

    assert sold(by_shares.after, outcome) - sold(by_shares.after, outcome.opposite) == lead
    assert by_shares.amount_micro == lead
    assert by_budget == by_shares
    assert resold.amount_micro == lead
    assert Fraction(lmsr.price_yes(by_shares.after)) == 1 - Fraction(lmsr.price_yes(state))


@checks
@given(state=all_markets, outcome=outcomes, budget=all_amounts)
def test_shares_bought_for_a_budget_never_cost_more_than_the_budget(
    state: MarketState, outcome: Outcome, budget: int
) -> None:
    quote = lmsr.quote_buy_for_budget(state, outcome, budget)
    same_shares = lmsr.quote_buy_shares(state, outcome, quote.shares_micro)
    one_more = lmsr.quote_buy_shares(state, outcome, quote.shares_micro + 1)

    assert 1 <= quote.amount_micro <= budget
    assert quote.shares_micro >= budget
    assert quote == same_shares
    assert one_more.amount_micro > budget


@checks
@given(state=all_markets, outcome=outcomes, shares=all_amounts)
def test_share_never_costs_more_than_one_credit_and_never_sells_for_less_than_nothing(
    state: MarketState, outcome: Outcome, shares: int
) -> None:
    cost = lmsr.quote_buy_shares(state, outcome, shares).amount_micro
    proceeds = lmsr.quote_sell_shares(state, outcome, shares).amount_micro

    assert 1 <= cost <= shares
    assert 0 <= proceeds < shares
    assert proceeds <= cost


@checks
@given(state=all_markets, outcome=outcomes, amount=all_amounts, by_budget=st.booleans())
def test_buy_then_sell_never_yields_a_profit(
    state: MarketState, outcome: Outcome, amount: int, by_budget: bool
) -> None:
    bought = buy(state, outcome, amount, by_budget)
    resold = lmsr.quote_sell_shares(bought.after, outcome, bought.shares_micro)

    assert resold.after == state
    assert 0 <= bought.amount_micro - resold.amount_micro <= 1


@checks
@given(state=all_markets, outcome=outcomes, data=st.data())
def test_sell_then_buy_back_never_yields_a_profit(
    state: MarketState, outcome: Outcome, data: st.DataObject
) -> None:
    assume(sold(state, outcome) > 0)
    shares = data.draw(st.integers(min_value=1, max_value=sold(state, outcome)))

    given_up = lmsr.quote_sell_shares(state, outcome, shares)
    bought_back = lmsr.quote_buy_shares(given_up.after, outcome, shares)

    assert bought_back.after == state
    assert 0 <= bought_back.amount_micro - given_up.amount_micro <= 1


@checks
@given(b=st.integers(min_value=1, max_value=MAX_B), data=st.data())
def test_random_trading_never_costs_the_market_maker_more_than_b_ln_2(
    b: int, data: st.DataObject
) -> None:
    steps = data.draw(
        st.lists(
            st.tuples(
                st.sampled_from(("budget", "shares", "sell")),
                outcomes,
                st.integers(min_value=1, max_value=40 * b),
            ),
            max_size=25,
        )
    )
    fresh = MarketState(q_yes=0, q_no=0, b=b)
    bound = lmsr.max_loss_micro(b)
    state = fresh
    received = 0

    for action, outcome, size in steps:
        if action == "sell":
            if sold(state, outcome) == 0:
                continue
            quote = lmsr.quote_sell_shares(state, outcome, min(size, sold(state, outcome)))
            received -= quote.amount_micro
        else:
            quote = buy(state, outcome, size, by_budget=action == "budget")
            received += quote.amount_micro
        assert quote.before == state
        state = quote.after

        assert min(state.q_yes, state.q_no) >= 0
        for payout_owed in (state.q_yes, state.q_no):
            assert payout_owed - received <= bound
        assert received >= Fraction(lmsr.cost(state)) - Fraction(lmsr.cost(fresh))


@checks
@given(state=ordinary_markets, outcome=outcomes, amount=ordinary_amounts)
def test_smallest_allowed_precision_gives_the_same_quotes(
    state: MarketState, outcome: Outcome, amount: int
) -> None:
    assert lmsr.quote_buy_for_budget(
        state, outcome, amount, precision=28
    ) == lmsr.quote_buy_for_budget(state, outcome, amount)
    assert lmsr.quote_buy_shares(state, outcome, amount, precision=28) == lmsr.quote_buy_shares(
        state, outcome, amount
    )
    assert lmsr.quote_sell_shares(state, outcome, amount, precision=28) == lmsr.quote_sell_shares(
        state, outcome, amount
    )


@checks
@given(budget=st.integers(min_value=1, max_value=1000 * CR))
def test_b_from_a_budget_is_the_largest_b_whose_loss_bound_fits_the_budget(budget: int) -> None:
    b = lmsr.b_from_budget_micro(budget)

    assert lmsr.max_loss_micro(b) <= budget < lmsr.max_loss_micro(b + 1)
