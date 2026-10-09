"""Adversarial checks of the LMSR module (SPEC §4, §12 "Pricing", Appendix A.4, A.9).

Written in review against a reference that was derived from SPEC §4 alone, not from the
implementation. Three groups:

* Rules the other two files leave open: what a trader keeps after any sequence of trades
  that ends without shares, split trades, the closed forms of SPEC §4 beyond |q|/b = 50,
  and what a buy by budget charges.
* Knife-edge inputs, found by search, on which the amount lies within 10^-18 of a whole
  micro-unit. They are rounded correctly by every precision the module accepts for that
  b, from 45 digits up, and by the smallest `Settings` accepts. With 28 to 32 digits
  they were not; such a precision is now refused for that b.
* Arguments of the wrong type, which were priced instead of rejected: any outcome that
  was not the `Outcome.YES` object traded NO, and a `Decimal` size gave fractional
  micro-units.
"""

from collections.abc import Callable
from decimal import Context, Decimal
from typing import Protocol

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.core.config import PricingSettings
from app.core.enums import Outcome, ResolutionOutcome
from app.core.units import MICRO, ceil_micro, floor_micro
from app.pricing import lmsr
from app.pricing.lmsr import MarketState, TradeQuote

CR = MICRO
MAX_B = 1_442_695_040
"""b of the largest market budget, 1,000 CR (SPEC §4 "Choosing b")."""

checks = settings(max_examples=200, deadline=None)


class QuoteFunction(Protocol):
    """The shape shared by the three quote functions of Appendix A.9."""

    def __call__(
        self, state: MarketState, outcome: Outcome, size: int, /, *, precision: int = ...
    ) -> TradeQuote: ...


outcomes = st.sampled_from(Outcome)
sizes = st.one_of(
    st.integers(min_value=1, max_value=3),
    st.integers(min_value=1, max_value=CR),
    st.integers(min_value=1, max_value=1000 * CR),
)
allowed_b = st.one_of(
    st.sampled_from((CR, 5 * CR, 10 * CR, 20 * CR, MAX_B)),
    st.integers(min_value=CR, max_value=MAX_B),
)


@st.composite
def markets(draw: st.DrawFn, *, max_ratio: int) -> MarketState:
    b = draw(allowed_b)
    q = st.integers(min_value=0, max_value=max_ratio * b)
    return MarketState(q_yes=draw(q), q_no=draw(q), b=b)


@st.composite
def decided_markets(draw: st.DrawFn) -> MarketState:
    """One outcome leads by 50 to 300 times b: past where the other files use a reference."""
    b = draw(allowed_b)
    trailing = draw(st.integers(min_value=0, max_value=50 * b))
    leading = trailing + draw(st.integers(min_value=50 * b, max_value=300 * b))
    if draw(st.booleans()):
        return MarketState(q_yes=leading, q_no=trailing, b=b)
    return MarketState(q_yes=trailing, q_no=leading, b=b)


def sold(state: MarketState, outcome: Outcome) -> int:
    return state.q_yes if outcome is Outcome.YES else state.q_no


def gap(state: MarketState) -> int:
    return abs(state.q_yes - state.q_no)


def minimum_precision() -> int:
    """The lower bound `Settings` puts on PRICING_DECIMAL_PRECISION."""
    for constraint in PricingSettings.model_fields["decimal_precision"].metadata:
        bound = getattr(constraint, "ge", None)
        if isinstance(bound, int):
            return bound
    raise AssertionError("PRICING_DECIMAL_PRECISION has no lower bound")


# --- Sequences of trades ------------------------------------------------------------


@checks
@given(state=markets(max_ratio=60), data=st.data())
def test_trader_who_ends_without_shares_never_ends_with_more_credits(
    state: MarketState, data: st.DataObject
) -> None:
    steps = data.draw(
        st.lists(
            st.tuples(st.sampled_from(("budget", "shares", "sell")), outcomes, sizes),
            min_size=1,
            max_size=30,
        )
    )
    held = {Outcome.YES: 0, Outcome.NO: 0}
    credits = 0
    current = state

    for action, outcome, size in steps:
        if action == "sell":
            if held[outcome] == 0:
                continue
            quote = lmsr.quote_sell_shares(current, outcome, min(size, held[outcome]))
            credits += quote.amount_micro
            held[outcome] -= quote.shares_micro
        else:
            if action == "budget":
                quote = lmsr.quote_buy_for_budget(current, outcome, size)
            else:
                quote = lmsr.quote_buy_shares(current, outcome, size)
            credits -= quote.amount_micro
            held[outcome] += quote.shares_micro
        current = quote.after

    for outcome in data.draw(st.permutations(list(Outcome))):
        for _ in range(data.draw(st.integers(min_value=0, max_value=3))):
            if held[outcome] > 1:
                piece = data.draw(st.integers(min_value=1, max_value=held[outcome] - 1))
                quote = lmsr.quote_sell_shares(current, outcome, piece)
                credits += quote.amount_micro
                held[outcome] -= piece
                current = quote.after
        if held[outcome] > 0:
            quote = lmsr.quote_sell_shares(current, outcome, held[outcome])
            credits += quote.amount_micro
            current = quote.after

    assert current == state
    assert credits <= 0


@checks
@given(
    state=markets(max_ratio=60),
    outcome=outcomes,
    parts=st.lists(sizes, min_size=2, max_size=6),
)
def test_splitting_a_buy_is_never_cheaper_and_splitting_a_sale_never_pays_more(
    state: MarketState, outcome: Outcome, parts: list[int]
) -> None:
    total = sum(parts)
    bought_at_once = lmsr.quote_buy_shares(state, outcome, total)
    sold_at_once = lmsr.quote_sell_shares(bought_at_once.after, outcome, total)

    current, paid = state, 0
    for part in parts:
        quote = lmsr.quote_buy_shares(current, outcome, part)
        paid += quote.amount_micro
        current = quote.after
    top = current

    received = 0
    for part in parts:
        quote = lmsr.quote_sell_shares(current, outcome, part)
        received += quote.amount_micro
        current = quote.after

    assert (top, current) == (bought_at_once.after, state)
    # Each leg rounds against the trader by less than one micro-unit.
    assert bought_at_once.amount_micro <= paid < bought_at_once.amount_micro + len(parts)
    assert sold_at_once.amount_micro - len(parts) < received <= sold_at_once.amount_micro
    assert received <= bought_at_once.amount_micro


@checks
@given(state=markets(max_ratio=100_000), outcome=outcomes, budget=sizes)
def test_buy_by_budget_charges_exactly_the_budget(
    state: MarketState, outcome: Outcome, budget: int
) -> None:
    """A share costs less than 1 CR, so cutting x down to whole µshares saves less than
    1 µCR and the cost rounds back up to the budget (Appendix A.4: "the user pays the
    cost, not the budget" names the same amount). A quote below the budget would mean the
    trader was given fewer shares than the budget buys.
    """
    quote = lmsr.quote_buy_for_budget(state, outcome, budget)

    assert quote.amount_micro == budget
    assert quote.shares_micro >= budget


# --- The closed forms of SPEC §4 where one price is all but 0 or 1 ------------------------


def reference_context(state: MarketState, amount: int) -> Context:
    """Enough digits to see e^(-|q|/b) next to 1 with a hundred digits to spare."""
    ratio = (gap(state) + amount) // state.b + 1
    return Context(prec=200 + ratio)


def spec_price(state: MarketState, outcome: Outcome, digits: Context) -> Decimal:
    """1 / (1 + e^((q_other - q_own)/b)), evaluated as SPEC §4 writes it."""
    behind = sold(state, outcome.opposite) - sold(state, outcome)
    odds = digits.exp(digits.divide(Decimal(behind), Decimal(state.b)))
    return digits.divide(Decimal(1), digits.add(Decimal(1), odds))


def expm1(x: Decimal, digits: Context) -> Decimal:
    return digits.subtract(digits.exp(x), Decimal(1))


@settings(max_examples=100, deadline=None)
@given(
    state=decided_markets(),
    outcome=outcomes,
    amount=st.integers(min_value=1, max_value=100 * CR),
    data=st.data(),
)
def test_closed_forms_of_spec_section_4_hold_far_beyond_q_over_b_of_50(
    state: MarketState, outcome: Outcome, amount: int, data: st.DataObject
) -> None:
    b = Decimal(state.b)
    one = Decimal(1)

    bought = lmsr.quote_buy_shares(state, outcome, amount)
    by_budget = lmsr.quote_buy_for_budget(state, outcome, amount)
    assume(gap(bought.after) != gap(state))  # the mirror trade is exact, tested elsewhere
    assume(gap(by_budget.after) != gap(state))

    digits = reference_context(state, amount)
    p = spec_price(state, outcome, digits)
    grown = expm1(digits.divide(Decimal(amount), b), digits)
    cost = digits.multiply(b, digits.ln(digits.add(one, digits.multiply(p, grown))))
    shares = digits.multiply(b, digits.subtract(digits.ln(digits.add(grown, p)), digits.ln(p)))

    assert bought.amount_micro == ceil_micro(cost)
    assert by_budget.shares_micro == floor_micro(shares)

    assume(sold(state, outcome) > 0)
    given_up = data.draw(st.integers(min_value=1, max_value=min(amount, sold(state, outcome))))
    resold = lmsr.quote_sell_shares(state, outcome, given_up)
    assume(gap(resold.after) != gap(state))
    shrunk = expm1(digits.divide(Decimal(-given_up), b), digits)
    # Not a unary minus: that would round to the 28 digits of the ambient context.
    proceeds = digits.multiply(-b, digits.ln(digits.add(one, digits.multiply(p, shrunk))))

    assert resold.amount_micro == floor_micro(proceeds)


# --- Knife edges: amounts within 10^-18 of a whole micro-unit ---------------------------
#
# A budget c spent on an outcome that trails by g buys x = c + g + b · ln(e^(-g/b) + m)
# shares, m = 1 - e^(-c/b). Far behind, x creeps towards its limit by about 1/b per
# µshare of g, so whole runs of g put x, and the cost of the shares around it, next to a
# whole number. The error of a `precision`-digit m is magnified by b/m, about b²/c, which
# is 2 · 10^18 / c at the largest b. Every state below is inside SPEC §4: b at most
# 1,442.695040 CR and |q|/b below 50. The expected values come from 400 digits.

KNIFE_EDGES = [
    pytest.param(
        lmsr.quote_buy_for_budget,
        MarketState(q_yes=0, q_no=61_579_147_687, b=1_442_694_002),
        1,
        (31_153_051_410, 1),
        id="budget of 1 µCR, shares cost 1 - 2.7e-19",
    ),
    pytest.param(
        lmsr.quote_buy_for_budget,
        MarketState(q_yes=0, q_no=61_070_925_371, b=1_442_609_484),
        10_001,
        (43_933_769_700, 10_001),
        id="budget just above MIN_TRADE_CR, x = whole + 3.2e-14",
    ),
    pytest.param(
        lmsr.quote_buy_for_budget,
        MarketState(q_yes=0, q_no=60_780_960_637, b=1_442_677_920),
        CR,
        (50_287_051_670, CR),
        id="budget of 1 CR, x = whole - 6.6e-17",
    ),
    pytest.param(
        lmsr.quote_buy_shares,
        MarketState(q_yes=0, q_no=61_811_230_801, b=1_442_694_001),
        31_385_134_546,
        (31_385_134_546, 2),
        id="buy costs 1 + 3.1e-20, rounds up to 2",
    ),
    pytest.param(
        lmsr.quote_sell_shares,
        MarketState(q_yes=31_153_051_410, q_no=61_579_147_687, b=1_442_694_002),
        31_153_051_410,
        (31_153_051_410, 0),
        id="sale returns 1 - 2.7e-19, rounds down to 0",
    ),
]


@pytest.mark.parametrize(("function", "state", "size", "exact"), KNIFE_EDGES)
def test_knife_edge_amounts_round_for_the_market_maker_at_the_default_precision(
    function: QuoteFunction, state: MarketState, size: int, exact: tuple[int, int]
) -> None:
    quote = function(state, Outcome.YES, size)

    assert (quote.shares_micro, quote.amount_micro) == exact


@pytest.mark.parametrize(("function", "state", "size", "exact"), KNIFE_EDGES)
def test_smallest_precision_settings_accept_rounds_knife_edge_amounts_the_same_way(
    function: QuoteFunction, state: MarketState, size: int, exact: tuple[int, int]
) -> None:
    quote = function(state, Outcome.YES, size, precision=minimum_precision())

    assert (quote.shares_micro, quote.amount_micro) == exact


def test_settings_accept_no_precision_below_the_one_spec_section_4_names() -> None:
    assert minimum_precision() == lmsr.DEFAULT_PRECISION == 50


@pytest.mark.parametrize("precision", [45, 47, 60, 80])
@pytest.mark.parametrize(("function", "state", "size", "exact"), KNIFE_EDGES)
def test_every_precision_the_module_accepts_rounds_knife_edge_amounts_the_same_way(
    function: QuoteFunction,
    state: MarketState,
    size: int,
    exact: tuple[int, int],
    precision: int,
) -> None:
    quote = function(state, Outcome.YES, size, precision=precision)

    assert (quote.shares_micro, quote.amount_micro) == exact


@pytest.mark.parametrize("precision", [28, 30, 32])
@pytest.mark.parametrize(("function", "state", "size", "exact"), KNIFE_EDGES)
def test_a_precision_that_rounded_knife_edge_amounts_wrongly_is_refused(
    function: QuoteFunction,
    state: MarketState,
    size: int,
    exact: tuple[int, int],
    precision: int,
) -> None:
    """These digits gave 1 µCR too little for a buy, 1 µCR too much for a sale, one
    µshare too many for a budget, or an AssertionError. Now they give none of it."""
    with pytest.raises(ValueError, match="cannot be quoted"):
        function(state, Outcome.YES, size, precision=precision)


def test_a_budget_buy_beyond_the_supported_b_is_refused_instead_of_tripping_its_assertion() -> None:
    """Found in review: at b = 2 · 10^16 µCR the default precision bought shares that cost
    2 µCR for a budget of 1 µCR and raised AssertionError."""
    state = MarketState(q_yes=0, q_no=1_535_941_632_561_927_199, b=20_000_000_000_000_003)

    with pytest.raises(ValueError, match="cannot be quoted"):
        lmsr.quote_buy_for_budget(state, Outcome.YES, 1)


# --- Arguments of the wrong type ---------------------------------------------------------


ALL_QUOTE_FUNCTIONS = pytest.mark.parametrize(
    "function",
    [lmsr.quote_buy_for_budget, lmsr.quote_buy_shares, lmsr.quote_sell_shares],
    ids=["by budget", "by shares", "sell"],
)


@pytest.mark.parametrize(
    "outcome",
    ["YES", "NO", "yes", ResolutionOutcome.YES, ResolutionOutcome.VOID, None, 1],
    ids=["str YES", "str NO", "lowercase", "other enum", "void", "None", "int"],
)
@ALL_QUOTE_FUNCTIONS
def test_an_outcome_that_is_not_an_outcome_member_is_an_error(
    function: QuoteFunction, outcome: object
) -> None:
    """`"YES" == Outcome.YES`, but it is not that member. It used to be traded as NO
    while the quote said YES."""
    state = MarketState(q_yes=5 * CR, q_no=5 * CR, b=10 * CR)

    with pytest.raises(TypeError, match="Outcome member"):
        function(state, outcome, CR)  # type: ignore[arg-type]


@pytest.mark.parametrize("size", [Decimal("1.5"), Decimal(2), True, "1000000"])
@ALL_QUOTE_FUNCTIONS
def test_size_that_is_not_an_integer_is_an_error(function: QuoteFunction, size: object) -> None:
    """A float was trapped, a Decimal was not: Decimal('1.5') µshares were priced and the
    quote carried Decimal('1.5') as shares, amount and q."""
    state = MarketState(q_yes=5 * CR, q_no=0, b=10 * CR)

    with pytest.raises(TypeError, match="must be an int"):
        function(state, Outcome.YES, size)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "state",
    [
        MarketState(q_yes=Decimal(5), q_no=0, b=10 * CR),  # type: ignore[arg-type]
        MarketState(q_yes=0, q_no=Decimal("0.5"), b=10 * CR),  # type: ignore[arg-type]
        MarketState(q_yes=0, q_no=0, b=Decimal(10 * CR)),  # type: ignore[arg-type]
        MarketState(q_yes=0, q_no=0, b=True),
    ],
    ids=["q_yes", "q_no", "b", "bool b"],
)
def test_a_market_state_that_is_not_made_of_integers_is_an_error(state: MarketState) -> None:
    for call in (
        lambda: lmsr.price_yes(state),
        lambda: lmsr.price_yes_micro(state),
        lambda: lmsr.cost(state),
        lambda: lmsr.quote_buy_shares(state, Outcome.YES, CR),
    ):
        with pytest.raises(TypeError, match="must be an int"):
            call()


@pytest.mark.parametrize("function", [lmsr.max_loss_micro, lmsr.b_from_budget_micro])
def test_b_and_budget_conversions_take_integers_only(
    function: Callable[[int], int],
) -> None:
    with pytest.raises(TypeError, match="must be an int"):
        function(Decimal("10000000"))  # type: ignore[arg-type]


# --- A sale that brings in nothing (Appendix A.4) ---------------------------------------


def test_a_sale_of_all_but_worthless_shares_returns_zero_proceeds() -> None:
    """Below a price of 10^-4 a sale of 0.01 shares rounds down to 0 µCR: about 43 full
    stakes on the other side at the tight tier. "Selling is always allowed" (SPEC §4),
    so this is a quote like any other, and the trade books no journal entry for it."""
    lopsided = MarketState(q_yes=2 * CR, q_no=150 * CR, b=10 * CR)

    for shares in (10_000, 2 * CR):
        quote = lmsr.quote_sell_shares(lopsided, Outcome.YES, shares)

        assert quote.amount_micro == 0
        assert quote.shares_micro == shares
        assert quote.after.q_yes == 2 * CR - shares
        assert quote.p_yes_before_micro == 0


@checks
@given(state=markets(max_ratio=100_000), outcome=outcomes, shares=sizes)
def test_a_buy_always_costs_at_least_one_micro_credit(
    state: MarketState, outcome: Outcome, shares: int
) -> None:
    """Costs round up, so only a sale can come to nothing."""
    assert lmsr.quote_buy_shares(state, outcome, shares).amount_micro >= 1
