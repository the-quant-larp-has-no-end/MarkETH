"""`app.core.units`: the one place credits change representation (SPEC §2, §4, A.2)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.units import (
    DECIMAL_PLACES,
    MICRO,
    PrecisionError,
    ceil_micro,
    complement,
    display_probability,
    floor_micro,
    format_cr,
    probability_to_micro,
    to_cr,
    to_micro,
)

BIGINT_MAX = 2**63 - 1


def test_one_credit_is_a_million_micro_units() -> None:
    assert MICRO == 1_000_000
    assert DECIMAL_PLACES == 6
    assert to_micro(1) == MICRO


@pytest.mark.parametrize(
    ("value", "micro"),
    [
        ("0", 0),
        ("0.25", 250_000),
        ("0.000001", 1),
        ("1.500000", 1_500_000),
        ("1000", 1_000_000_000),
        ("-2.5", -2_500_000),
        (Decimal("0.1"), 100_000),
        (7, 7_000_000),
    ],
)
def test_to_micro_converts_exact_amounts(value: Decimal | int | str, micro: int) -> None:
    assert to_micro(value) == micro


@pytest.mark.parametrize("value", ["0.0000001", "1.2345678", Decimal("0.0000005"), "1e-7"])
def test_to_micro_rejects_more_than_six_decimal_places(value: Decimal | str) -> None:
    with pytest.raises(PrecisionError, match="decimal places"):
        to_micro(value)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_to_micro_rejects_amounts_that_are_not_finite(value: str) -> None:
    with pytest.raises(PrecisionError, match="finite"):
        to_micro(value)


def test_precision_error_is_a_value_error() -> None:
    assert issubclass(PrecisionError, ValueError)


def test_to_cr_always_carries_six_decimal_places() -> None:
    assert to_cr(250_000) == Decimal("0.25")
    assert str(to_cr(250_000)) == "0.250000"
    assert str(to_cr(0)) == "0.000000"
    assert str(to_cr(-1)) == "-0.000001"


@given(st.integers(min_value=-BIGINT_MAX, max_value=BIGINT_MAX))
def test_micro_to_cr_and_back_is_lossless_for_every_bigint(micro: int) -> None:
    assert to_micro(to_cr(micro)) == micro


@given(st.integers(min_value=-BIGINT_MAX, max_value=BIGINT_MAX))
def test_format_cr_round_trips_and_is_plain_notation(micro: int) -> None:
    text = format_cr(micro)
    assert to_micro(text) == micro
    assert "e" not in text.lower()
    assert not text.endswith(".")
    assert "." not in text or not text.endswith("0")


@pytest.mark.parametrize(
    ("micro", "text"),
    [
        (0, "0"),
        (1, "0.000001"),
        (250_000, "0.25"),
        (500_000, "0.5"),
        (1_000_000, "1"),
        (10_000_000, "10"),
        (6_931_472, "6.931472"),
        (-1_500_000, "-1.5"),
        (1_000_000_000_000_000, "1000000000"),
    ],
)
def test_format_cr_trims_trailing_zeros(micro: int, text: str) -> None:
    assert format_cr(micro) == text


@pytest.mark.parametrize(
    ("value", "up", "down"),
    [
        ("0", 0, 0),
        ("1", 1, 1),
        ("1.000000000000000000001", 2, 1),
        ("1.5", 2, 1),
        ("1.999999999999999999999", 2, 1),
        ("-1.5", -1, -2),
    ],
)
def test_costs_round_up_and_proceeds_round_down(value: str, up: int, down: int) -> None:
    """Rounding favours the market maker (golden rule 5)."""
    assert ceil_micro(Decimal(value)) == up
    assert floor_micro(Decimal(value)) == down


def test_probability_to_micro_rounds_half_to_even() -> None:
    assert probability_to_micro(Decimal("0.5")) == 500_000
    assert probability_to_micro(Decimal("0.5475005")) == 547_500
    assert probability_to_micro(Decimal("0.5475015")) == 547_502
    assert probability_to_micro(Decimal(0)) == 0
    assert probability_to_micro(Decimal(1)) == MICRO


@pytest.mark.parametrize(
    ("p_micro", "decimals", "shown"),
    [
        (500_000, 1, 0.5),
        (547_500, 1, 0.548),
        (547_499, 1, 0.547),
        (548_500, 1, 0.548),
        (547_500, 0, 0.55),
        (500_312, 2, 0.5003),
        (0, 1, 0.0),
        (MICRO, 1, 1.0),
        (999_999, 1, 1.0),
        (1, 1, 0.0),
    ],
)
def test_display_probability_rounds_to_the_displayed_precision(
    p_micro: int, decimals: int, shown: float
) -> None:
    """`decimals` counts the decimals of the percentage: 1 means 54.8% (SPEC §4)."""
    assert display_probability(p_micro, decimals) == shown


@given(st.integers(min_value=0, max_value=MICRO), st.integers(min_value=0, max_value=4))
def test_yes_and_no_always_add_up_to_exactly_one(p_micro: int, decimals: int) -> None:
    """NO is the complement of the rounded YES, so the two never show 100.1% (A.2)."""
    p_yes = display_probability(p_micro, decimals)
    p_no = complement(p_yes, decimals)
    assert 0.0 <= p_yes <= 1.0
    assert 0.0 <= p_no <= 1.0
    assert round(p_yes + p_no, decimals + 2) == 1.0
    assert Decimal(str(p_yes)) + Decimal(str(p_no)) == 1


def test_complement_has_no_float_dust() -> None:
    assert complement(0.548, 1) == 0.452
    assert complement(0.1, 1) == 0.9
    assert complement(1.0, 1) == 0.0
