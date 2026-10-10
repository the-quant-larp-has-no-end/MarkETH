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
    format_tokens,
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


@pytest.mark.parametrize(
    "value",
    [
        "1.0000000000000000000000000000001",
        "999999999.9999990000000000000001",
        "0." + "0" * 40 + "1",
        "0." + "0" * 1_500_000 + "1",
        "1." + "0" * 1_500_000 + "1",
        "-0." + "0" * 1_100_000 + "5",
        "1e-1000100",
    ],
    ids=[
        "the-31st-place",
        "the-22nd-place-of-31-digits",
        "the-41st-place",
        "a-million-and-a-half-zeros",
        "a-digit-after-a-million-and-a-half-zeros",
        "below-zero",
        "smaller-than-ordinary-arithmetic-keeps",
    ],
)
def test_to_micro_rejects_a_digit_beyond_the_sixth_place_wherever_it_stands(value: str) -> None:
    """Arithmetic that keeps 28 digits would round each of these to an amount with six
    places, and the smallest ones to nothing. Converting is exact: each is refused."""
    with pytest.raises(PrecisionError, match="decimal places"):
        to_micro(value)


@pytest.mark.parametrize(
    ("value", "micro"),
    [
        ("1234567890123456789012345678.9", 1234567890123456789012345678900000),
        ("-1234567890123456789012345678.000001", -1234567890123456789012345678000001),
        ("0." + "0" * 5000, 0),
        ("0.250000" + "0" * 5000, 250_000),
        ("0" * 5000 + "12.5", 12_500_000),
    ],
    ids=[
        "29-digits",
        "34-digits-below-zero",
        "zero-with-5000-places",
        "trailing-zeros",
        "leading-zeros",
    ],
)
def test_to_micro_rounds_nothing_however_long_the_number(value: str, micro: int) -> None:
    assert to_micro(value) == micro


@given(
    st.integers(min_value=-(10**60), max_value=10**60),
    st.integers(min_value=7, max_value=80),
    st.integers(min_value=1, max_value=9),
)
def test_to_micro_is_exact_for_every_integer_of_micro_units_and_refuses_anything_between(
    micro: int, place: int, digit: int
) -> None:
    """Far beyond the 28 digits ordinary decimal arithmetic keeps. The two numbers are
    written out as text, so that nothing rounds them before they are converted."""
    amount = Decimal(f"{micro}E-{DECIMAL_PLACES}")
    one_digit_more = micro * 10 ** (place - DECIMAL_PLACES) + digit
    finer = Decimal(f"{one_digit_more}E-{place}")
    assert finer != amount

    assert to_micro(amount) == micro
    with pytest.raises(PrecisionError):
        to_micro(finer)


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
    ("micro", "said"),
    [
        (0, "0 tokens"),
        (1, "0.000001 tokens"),
        (500_000, "0.5 tokens"),
        (1_000_000, "1 token"),
        (1_000_001, "1.000001 tokens"),
        (2_000_000, "2 tokens"),
        (35_000_000, "35 tokens"),
        (999_000_000, "999 tokens"),
        (1_000_000_000, "1,000 tokens"),
        (7_000_500_000, "7,000.5 tokens"),
        (1_234_567_890_123, "1,234,567.890123 tokens"),
        (-1_500_000, "-1.5 tokens"),
        (-2_000_000_000, "-2,000 tokens"),
    ],
)
def test_format_tokens_says_an_amount_as_a_sentence_on_screen_does(micro: int, said: str) -> None:
    """The whole figure, grouped in threes before the point, and "token" for exactly one."""
    assert format_tokens(micro) == said


@given(st.integers(min_value=-BIGINT_MAX, max_value=BIGINT_MAX))
def test_format_tokens_is_the_amount_of_format_cr_and_names_no_credit(micro: int) -> None:
    said = format_tokens(micro)
    figure, word = said.split(" ")
    assert figure.replace(",", "") == format_cr(micro)
    assert word == ("token" if micro == MICRO else "tokens")
    whole = figure.removeprefix("-").partition(".")[0]
    assert all(len(group) == 3 for group in whole.split(",")[1:])
    assert 1 <= len(whole.split(",")[0]) <= 3


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
