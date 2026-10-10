"""`lmsr.is_quotable`: which b a market may be opened with (SPEC §4, §5, Appendix A.5).

The quote functions refuse a b their digits cannot round reliably. A market keeps its b
for as long as it is OPEN, so whoever opens one asks this function first. It has to say
no to exactly the b the quote functions refuse, and yes to every b the settings can give
a market.
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.config import Settings
from app.core.enums import Outcome
from app.core.units import MICRO
from app.pricing import lmsr
from app.pricing.lmsr import MarketState
from app.services.public_config import FIXED_TIERS

CR = MICRO

QUOTE_FUNCTIONS = (lmsr.quote_buy_for_budget, lmsr.quote_buy_shares, lmsr.quote_sell_shares)

LARGEST_B_AT_THE_DEFAULT_PRECISION = 10**11
"""100,000 CR: the largest b whose cube times 10^17 stays within 10^50."""

checks = settings(max_examples=200, deadline=None)


def refusals(b_micro: int, precision: int) -> list[str | None]:
    """Per quote function, why it would not price a market with this b; None if it did."""
    state = MarketState(q_yes=CR, q_no=0, b=b_micro)
    reasons: list[str | None] = []
    for function in QUOTE_FUNCTIONS:
        try:
            function(state, Outcome.YES, CR, precision=precision)
        except ValueError as refused:
            reasons.append(str(refused))
        else:
            reasons.append(None)
    return reasons


def is_quoted(b_micro: int, precision: int) -> bool:
    """Whether the quote functions price a market with this b. All three must agree,
    and a refusal must be the one about the digits."""
    reasons = refusals(b_micro, precision)
    refused = [reason for reason in reasons if reason is not None]
    assert len(refused) in (0, len(QUOTE_FUNCTIONS)), reasons
    assert all("cannot be quoted" in reason for reason in refused), reasons
    return not refused


@pytest.mark.parametrize(
    ("precision", "largest_b"),
    [(45, 2_154_434_690), (50, LARGEST_B_AT_THE_DEFAULT_PRECISION), (53, 10**12)],
    ids=["45 digits", "the default", "53 digits"],
)
def test_is_quotable_draws_the_line_exactly_where_the_quote_functions_do(
    precision: int, largest_b: int
) -> None:
    assert lmsr.is_quotable(largest_b, precision=precision)
    assert is_quoted(largest_b, precision)
    assert not lmsr.is_quotable(largest_b + 1, precision=precision)
    assert not is_quoted(largest_b + 1, precision)


@checks
@given(
    b_micro=st.one_of(
        st.integers(min_value=CR, max_value=10**13),
        st.integers(min_value=-1000, max_value=1000).map(
            lambda offset: LARGEST_B_AT_THE_DEFAULT_PRECISION + offset
        ),
    ),
    precision=st.integers(min_value=40, max_value=60),
)
def test_is_quotable_says_what_the_quote_functions_do_for_any_b_and_precision(
    b_micro: int, precision: int
) -> None:
    assert lmsr.is_quotable(b_micro, precision=precision) == is_quoted(b_micro, precision)


def test_without_a_precision_the_question_is_about_the_default_one() -> None:
    assert lmsr.DEFAULT_PRECISION == 50
    assert lmsr.is_quotable(LARGEST_B_AT_THE_DEFAULT_PRECISION)
    assert not lmsr.is_quotable(LARGEST_B_AT_THE_DEFAULT_PRECISION + 1)


def test_more_digits_cover_a_larger_b() -> None:
    beyond = LARGEST_B_AT_THE_DEFAULT_PRECISION + 1

    assert not lmsr.is_quotable(beyond, precision=50)
    assert lmsr.is_quotable(beyond, precision=51)


@pytest.mark.parametrize("b_micro", [0, -1, -(10**11)])
def test_a_b_that_is_not_positive_is_no_b_at_all(b_micro: int) -> None:
    with pytest.raises(ValueError, match="b_micro must be positive"):
        lmsr.is_quotable(b_micro)


@pytest.mark.parametrize("b_micro", [True, 1000.0, "1000", None, Decimal(1000)])
def test_a_b_that_is_not_an_int_of_micro_units_is_rejected_not_coerced(b_micro: object) -> None:
    with pytest.raises(TypeError, match="b_micro must be an int"):
        lmsr.is_quotable(b_micro)  # type: ignore[arg-type]


def test_every_b_the_settings_can_give_a_market_is_quotable(base_settings: Settings) -> None:
    """A fixed tier brings its b; a CUSTOM market gets the b of a budget of at most
    MAX_MARKET_BUDGET. Each must be one the quote functions take, at the digits the
    application prices with."""
    pricing = base_settings.pricing
    precision = pricing.decimal_precision
    largest_budget = base_settings.risk.max_market_budget_micro
    largest_b = lmsr.b_from_budget_micro(largest_budget, precision=precision)

    for tier in FIXED_TIERS:
        b_micro = pricing.tier_b_micro(tier)
        assert lmsr.is_quotable(b_micro, precision=precision), tier
        assert b_micro <= largest_b, tier
        assert lmsr.max_loss_micro(b_micro, precision=precision) <= largest_budget, tier
    assert lmsr.is_quotable(largest_b, precision=precision)
    assert is_quoted(largest_b, precision)


def test_a_budget_beyond_what_the_digits_cover_gives_a_b_that_is_not_quotable() -> None:
    """At 50 digits the line runs between a budget of 69,314 CR and one of 69,315 CR:
    MAX_MARKET_BUDGET must stay on this side of it, and approval checks each market."""
    covered = lmsr.b_from_budget_micro(69_314 * CR)
    beyond = lmsr.b_from_budget_micro(69_315 * CR)

    assert covered <= LARGEST_B_AT_THE_DEFAULT_PRECISION < beyond
    assert lmsr.is_quotable(covered)
    assert not lmsr.is_quotable(beyond)
