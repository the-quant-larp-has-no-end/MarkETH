"""Binary LMSR market maker: prices, costs and trade quotes (SPEC §4, Appendix A.9).

Pure functions on integer micro-units: no I/O, no clock, no settings. Quantities and
trade sizes are µshares, b and every amount are µCR. A winning share pays 1 CR, so both
use the same scale and q/b is a plain number.

The cost function, with the larger exponential factored out so that e is never raised
to a positive power and nothing can overflow:

    C(q) = b · ln(e^(q_yes/b) + e^(q_no/b)) = max(q_yes, q_no) + R(q_yes - q_no)
    R(g) = b · ln(1 + e^(-|g|/b))

R lies in (0, b · ln 2] and falls strictly as the gap |g| between the outcomes widens.

Every trade is a difference of C. With p the current price of the outcome traded, the
closed forms of SPEC §4 are

    cost of x shares        b · ln(1 + p · (e^(x/b) - 1))        = C(after) - C(before)
    proceeds of s shares    -b · log1p(p · expm1(-s/b))          = C(before) - C(after)
    shares for a budget c   b · [ln(expm1(c/b) + p) - ln p]      solves C(after) = C(before) + c

and this module evaluates exactly these, rearranged the way C is above: every exponential
that can grow is taken out of the logarithm, where it becomes whole micro-units handled
by integer arithmetic, and only what is left inside goes through `Decimal`. That keeps
the results accurate where the forms as written lose every digit (p within 10^-precision
of 0 or 1), and it makes the rounding exact, see `_Amount`.

Rounding always favours the market maker: costs up, proceeds down, shares bought for a
budget down. No float is used anywhere.

How many digits are enough. Rounding in one direction is only right while the noise of
the arithmetic stays far below the distance of a result from a whole micro-unit. The
budget form is the demanding one: with m = 1 - e^(-c/b), the error of m is magnified by
b/m, about b²/c, and for an outcome far behind the result creeps towards its limit in
steps of about 1/b, so whole runs of market states put it next to a whole number. The
noise, relative to that distance, is about b³ · 10^-precision / c. The quote functions
therefore refuse a b for which b³ · 10^-precision exceeds 10^-17 (`_require_digits`)
with `ValueError`, instead of quoting an amount that may be one micro-unit off:

    precision 50 (the default, SPEC §4)   b up to 10^11 µCR, a budget of about 69,000 CR
    precision 45                          the fewest digits that cover the largest b of
                                          SPEC §4, 1,442.695040 CR

With 28 to 32 digits, quotes at that b were shown to round against the market maker
and to trip the assertion in `quote_buy_for_budget`; the inputs are kept in
tests/unit/pricing/test_lmsr_adversarial.py. The bound of 10^-17 is a margin chosen
from those findings, not a proof: it rejects every precision that failed and leaves
45 digits, which round all of those inputs correctly, as the fewest for that b.

Arguments are checked, not coerced: a quantity, size or b that is not an `int`, or an
outcome that is not an `Outcome` member, raises `TypeError`. A string equal to "YES" is
not `Outcome.YES`.
"""

from dataclasses import dataclass, replace
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final, assert_never

from app.core.enums import Outcome, TradeSide
from app.core.units import ceil_micro, floor_micro, probability_to_micro

DEFAULT_PRECISION: Final[int] = 50
"""Significant digits of the arithmetic, the value of PRICING_DECIMAL_PRECISION (SPEC §4)."""

_EXPONENT_LIMIT: Final[int] = 999_999
"""Decimal exponent range. An e^(-|g|/b) below it becomes 0, which is its limit anyway."""

_GUARD_DIGITS: Final[int] = 17
"""Digits a quote keeps between its rounding noise and a whole micro-unit: b³ times
10^-precision may be at most 10^-17. See "How many digits are enough" above."""


@dataclass(frozen=True, slots=True)
class MarketState:
    """Shares sold of each outcome and the liquidity parameter of one market."""

    q_yes: int  # µshares
    q_no: int  # µshares
    b: int  # µCR


@dataclass(frozen=True, slots=True)
class TradeQuote:
    """What one trade does to a market and what the trader pays or receives for it."""

    side: TradeSide
    outcome: Outcome
    shares_micro: int
    amount_micro: int  # cost of a buy (rounded up) or proceeds of a sell (rounded down)
    before: MarketState
    after: MarketState
    p_yes_before_micro: int
    p_yes_after_micro: int


def price_yes(state: MarketState, *, precision: int = DEFAULT_PRECISION) -> Decimal:
    """Price of YES in [0, 1]: 1 / (1 + e^((q_no - q_yes)/b)). NO costs `1 - price_yes`."""
    _require_market(state)
    with localcontext(_context(precision)):
        return _price_yes(state)


def price_yes_micro(state: MarketState, *, precision: int = DEFAULT_PRECISION) -> int:
    """Price of YES in millionths, the form in which trades and quotes store it."""
    _require_market(state)
    with localcontext(_context(precision)):
        return probability_to_micro(_price_yes(state))


def cost(state: MarketState, *, precision: int = DEFAULT_PRECISION) -> Decimal:
    """The LMSR cost function C(q) in µCR."""
    _require_market(state)
    with localcontext(_context(precision)):
        return max(state.q_yes, state.q_no) + _remainder(state.q_yes - state.q_no, state.b)


def quote_buy_for_budget(
    state: MarketState,
    outcome: Outcome,
    budget_micro: int,
    *,
    precision: int = DEFAULT_PRECISION,
) -> TradeQuote:
    """Buy as many shares of `outcome` as `budget_micro` pays for.

    Shares round down; the cost is then recomputed from the rounded shares, rounded up
    and asserted not to exceed the budget (SPEC §4). A share costs less than 1 CR, which
    has two consequences a caller may rely on:

    * `amount_micro == budget_micro`, always. The part of a µshare that was cut off is
      worth less than 1 µCR, so the cost of the whole shares rounds back up to the
      budget. A budget buy is never cheaper than its budget.
    * `shares_micro >= budget_micro`. A quote of zero shares cannot come out of valid
      inputs.
    """
    _require_tradable(state, outcome, precision)
    _require_positive("budget_micro", budget_micro)
    with localcontext(_context(precision)):
        shares_micro = _shares_for_budget(state, outcome, budget_micro).floor()
        after = _with_shares_added(state, outcome, shares_micro)
        cost_micro = _cost_increase(state, after).ceil()
        if cost_micro > budget_micro:
            raise AssertionError(
                f"{shares_micro} µshares cost {cost_micro} µCR, above the budget of "
                f"{budget_micro} µCR"
            )
        return _quote(TradeSide.BUY, outcome, shares_micro, cost_micro, state, after)


def quote_buy_shares(
    state: MarketState,
    outcome: Outcome,
    shares_micro: int,
    *,
    precision: int = DEFAULT_PRECISION,
) -> TradeQuote:
    """Buy exactly `shares_micro` of `outcome`; the cost is C(after) - C(before), rounded up.

    A buy always costs at least 1 µCR.
    """
    _require_tradable(state, outcome, precision)
    _require_positive("shares_micro", shares_micro)
    with localcontext(_context(precision)):
        after = _with_shares_added(state, outcome, shares_micro)
        cost_micro = _cost_increase(state, after).ceil()
        return _quote(TradeSide.BUY, outcome, shares_micro, cost_micro, state, after)


def quote_sell_shares(
    state: MarketState,
    outcome: Outcome,
    shares_micro: int,
    *,
    precision: int = DEFAULT_PRECISION,
) -> TradeQuote:
    """Sell `shares_micro` of `outcome`; the proceeds are C(before) - C(after), rounded down.

    Whether the seller holds that many shares is the caller's check (SPEC §4: no short
    positions). The proceeds are never negative and always below 1 CR per share. They
    can be 0: shares of an outcome priced below one millionth of a credit per share
    sold round down to nothing (Appendix A.4 says what a trade does then).
    """
    _require_tradable(state, outcome, precision)
    _require_positive("shares_micro", shares_micro)
    with localcontext(_context(precision)):
        after = _with_shares_added(state, outcome, -shares_micro)
        proceeds_micro = _cost_increase(after, state).floor()
        return _quote(TradeSide.SELL, outcome, shares_micro, proceeds_micro, state, after)


def max_loss_micro(b_micro: int, *, precision: int = DEFAULT_PRECISION) -> int:
    """Worst-case loss of the market maker in one market: b · ln 2, rounded up."""
    _require_positive("b_micro", b_micro)
    with localcontext(_context(precision)):
        return ceil_micro(b_micro * Decimal(2).ln())


def b_from_budget_micro(budget_micro: int, *, precision: int = DEFAULT_PRECISION) -> int:
    """The largest b whose worst-case loss stays within the budget: budget / ln 2, rounded down."""
    _require_positive("budget_micro", budget_micro)
    with localcontext(_context(precision)):
        return floor_micro(budget_micro / Decimal(2).ln())


@dataclass(frozen=True, slots=True)
class _Amount:
    """A real number of micro-units as `whole + rest`, to be rounded in one direction.

    `whole` is exact. `rest` is a `Decimal` with rounding noise in its last digits, and
    `sign` is its true sign (-1, 0 or 1), which the caller knows from comparing integers.

    The sign is what makes the rounding exact. A buy that mirrors the market (twice the
    gap, of the trailing outcome) costs exactly the gap, a whole number of micro-units.
    In a market that is all but decided, an amount is a whole number plus or minus less
    than the precision can show. In both cases `rest` is zero up to noise, and without
    the sign that noise would decide whether a micro-unit is charged or not.
    """

    whole: int
    rest: Decimal
    sign: int

    def ceil(self) -> int:
        if self.sign == 0:
            return self.whole
        rounded = ceil_micro(self.rest)
        return self.whole + (max(rounded, 1) if self.sign > 0 else min(rounded, 0))

    def floor(self) -> int:
        if self.sign == 0:
            return self.whole
        rounded = floor_micro(self.rest)
        return self.whole + (max(rounded, 0) if self.sign > 0 else min(rounded, -1))


def _context(precision: int) -> Context:
    """Arithmetic settings that depend on `precision` alone, never on the caller's context.

    Trapping `FloatOperation` turns a float that slips into a calculation into an error.
    """
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=-_EXPONENT_LIMIT,
        Emax=_EXPONENT_LIMIT,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, FloatOperation],
    )


def _require_int(name: str, value: object) -> None:
    """Micro-units are whole. A bool is an int to Python and is refused as well."""
    if type(value) is not int:
        raise TypeError(f"{name} must be an int of micro-units, got {type(value).__name__}")


def _require_market(state: MarketState) -> None:
    _require_int("b", state.b)
    _require_int("q_yes", state.q_yes)
    _require_int("q_no", state.q_no)
    if state.b <= 0:
        raise ValueError(f"b must be positive, got {state.b}")
    if state.q_yes < 0 or state.q_no < 0:
        raise ValueError(f"q must not be negative, got q_yes={state.q_yes}, q_no={state.q_no}")


def _require_positive(name: str, value: int) -> None:
    _require_int(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


def _require_digits(b: int, precision: int) -> None:
    """Refuse a b that `precision` digits cannot round reliably (see the module docstring)."""
    if b**3 * 10**_GUARD_DIGITS > 10**precision:
        raise ValueError(
            f"b = {b} µCR cannot be quoted with {precision} digits: "
            f"b³ must stay below 10^{precision - _GUARD_DIGITS}"
        )


def _require_tradable(state: MarketState, outcome: Outcome, precision: int) -> None:
    """What every quote function checks before it computes anything."""
    _require_market(state)
    if not isinstance(outcome, Outcome):
        raise TypeError(f"outcome must be an Outcome member, got {outcome!r}")
    _require_digits(state.b, precision)


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _remainder(gap: int, b: int) -> Decimal:
    """R(g) = b · ln(1 + e^(-|g|/b)), the part of C(q) above max(q_yes, q_no)."""
    return b * (1 + (-Decimal(abs(gap)) / b).exp()).ln()


def _price_yes(state: MarketState) -> Decimal:
    """Price the leading outcome directly and the trailing one as its complement.

    The leader's price is at least 1/2, so it has at most `precision` decimal places and
    subtracting it from 1 is exact: YES and NO add up to exactly 1 however large |q|/b is.
    """
    gap = Decimal(abs(state.q_yes - state.q_no)) / state.b
    leader = 1 / (1 + (-gap).exp())
    return leader if state.q_yes >= state.q_no else 1 - leader


def _with_shares_added(state: MarketState, outcome: Outcome, shares_micro: int) -> MarketState:
    match outcome:
        case Outcome.YES:
            return replace(state, q_yes=state.q_yes + shares_micro)
        case Outcome.NO:
            return replace(state, q_no=state.q_no + shares_micro)
    assert_never(outcome)


def _lead(state: MarketState, outcome: Outcome) -> int:
    """By how many shares `outcome` is ahead of the other one; negative when it trails."""
    match outcome:
        case Outcome.YES:
            return state.q_yes - state.q_no
        case Outcome.NO:
            return state.q_no - state.q_yes
    assert_never(outcome)


def _cost_increase(start: MarketState, end: MarketState) -> _Amount:
    """C(end) - C(start) for two states of one market.

    The whole part is the growth of max(q_yes, q_no). The rest is R(end) - R(start), and
    because R falls as the gap widens, it is positive exactly when the gap narrowed.
    """
    start_gap = abs(start.q_yes - start.q_no)
    end_gap = abs(end.q_yes - end.q_no)
    return _Amount(
        whole=max(end.q_yes, end.q_no) - max(start.q_yes, start.q_no),
        rest=_remainder(end_gap, end.b) - _remainder(start_gap, start.b),
        sign=_sign(start_gap - end_gap),
    )


def _shares_for_budget(state: MarketState, outcome: Outcome, budget_micro: int) -> _Amount:
    """The x that a budget c buys of `outcome`, which leads the other outcome by g shares.

    SPEC §4 gives x = b · [ln(expm1(c/b) + p) - ln p] with p = 1 / (1 + e^(-g/b)). Taking
    e^(c/b) out of the logarithm leaves

        x = c + b · ln(1 + e^(-g/b) · (1 - e^(-c/b)))

    and for a trailing outcome (g < 0), taking e^(|g|/b) out as well,

        x = c + |g| + b · ln(e^(-|g|/b) + 1 - e^(-c/b)).

    The first logarithm is always positive. The second is positive exactly when c > |g|
    and zero when c = |g|: that budget buys 2 · |g| shares and mirrors the market.
    """
    lead = _lead(state, outcome)
    trailing_odds = (-Decimal(abs(lead)) / state.b).exp()
    move = 1 - (-Decimal(budget_micro) / state.b).exp()
    if lead >= 0:
        rest = state.b * (1 + trailing_odds * move).ln()
        return _Amount(whole=budget_micro, rest=rest, sign=1)
    rest = state.b * (trailing_odds + move).ln()
    return _Amount(whole=budget_micro - lead, rest=rest, sign=_sign(budget_micro + lead))


def _quote(
    side: TradeSide,
    outcome: Outcome,
    shares_micro: int,
    amount_micro: int,
    before: MarketState,
    after: MarketState,
) -> TradeQuote:
    return TradeQuote(
        side=side,
        outcome=outcome,
        shares_micro=shares_micro,
        amount_micro=amount_micro,
        before=before,
        after=after,
        p_yes_before_micro=probability_to_micro(_price_yes(before)),
        p_yes_after_micro=probability_to_micro(_price_yes(after)),
    )
