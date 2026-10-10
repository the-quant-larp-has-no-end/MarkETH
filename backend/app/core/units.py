"""The single place where credits change representation (SPEC §2, §4).

There is one unit everywhere: the credit (CR), shown on screen as a token. It is stored
and computed as an integer number of micro-units (µCR). Shares use the same scale.

Naming rule used across the codebase:
    *_micro   int      µCR or µshares; what the database and the services use
    *_cr      Decimal  CR; what the API and the .env file speak

The scale is a constant, not a setting: changing it would silently re-value every row.
"""

from decimal import (
    MAX_EMAX,
    MAX_PREC,
    MIN_EMIN,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
)
from typing import Final

MICRO: Final[int] = 1_000_000
"""Micro-units per credit and per share."""

DECIMAL_PLACES: Final[int] = 6

_MICRO_DECIMAL: Final[Decimal] = Decimal(MICRO)
_QUANTUM: Final[Decimal] = Decimal(1).scaleb(-DECIMAL_PLACES)

_EXACT: Final[Context] = Context(prec=MAX_PREC, Emax=MAX_EMAX, Emin=MIN_EMIN)
"""Arithmetic that rounds nothing, however many digits a number has. The default
context keeps 28 digits and turns a number below 10^-1000026 into zero: with it, a
thirtieth decimal place would pass for none at all, and an amount with a million
zeros after the point for nothing."""


class PrecisionError(ValueError):
    """A CR amount carries more decimal places than a micro-unit can hold."""


def to_micro(value_cr: Decimal | int | str) -> int:
    """Convert an exact CR amount to µCR. Rejects anything finer than one micro-unit.

    The conversion is exact for a number of any length: nothing is rounded on the
    way, so a digit beyond the sixth place is refused wherever it stands, and what
    comes back is the amount and nothing near it. A caller that takes numbers from
    outside bounds their size before it asks: turning a number of a million digits
    into an integer takes seconds.
    """
    amount = value_cr if isinstance(value_cr, Decimal) else Decimal(value_cr)
    if not amount.is_finite():
        raise PrecisionError(f"not a finite amount: {value_cr!r}")
    scaled = _EXACT.multiply(amount, _MICRO_DECIMAL)
    if scaled != _EXACT.to_integral_value(scaled):
        raise PrecisionError(f"more than {DECIMAL_PLACES} decimal places: {value_cr!r}")
    return int(scaled)


def to_cr(value_micro: int) -> Decimal:
    """Convert µCR to an exact CR Decimal with six decimal places."""
    return (Decimal(value_micro) / _MICRO_DECIMAL).quantize(_QUANTUM)


def format_cr(value_micro: int) -> str:
    """Canonical API string for an amount: plain notation, trailing zeros trimmed."""
    text = format(to_cr(value_micro), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_tokens(value_micro: int) -> str:
    """An amount as a sentence on screen says it: "1 token", "35 tokens", "0.5 tokens",
    "7,000 tokens". The figure is the whole amount, its digits before the point grouped
    in threes, and the unit is called a token there (never a credit).

    For the message of an error that names a limit or a balance. An amount a client
    formats itself travels as `format_cr`.
    """
    text = format_cr(value_micro)
    sign = "-" if text.startswith("-") else ""
    whole, point, fraction = text.removeprefix("-").partition(".")
    word = "token" if text == "1" else "tokens"
    return f"{sign}{int(whole):,}{point}{fraction} {word}"


def ceil_micro(value: Decimal) -> int:
    """Round a fractional micro amount up. Used for costs: the trader pays the dust."""
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def floor_micro(value: Decimal) -> int:
    """Round a fractional micro amount down. Used for proceeds and shares bought."""
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def probability_to_micro(probability: Decimal) -> int:
    """Store a probability in [0, 1] as an integer number of millionths."""
    return int((probability * _MICRO_DECIMAL).to_integral_value(rounding=ROUND_HALF_EVEN))


def display_probability(probability_micro: int, decimals: int) -> float:
    """Round a stored probability for display.

    `decimals` is the number of decimals of the percentage, so 1 means 0.1 percentage
    points (SPEC §4). The result is a fraction in [0, 1]; its complement is computed by
    the caller from the rounded value so YES and NO always add up to exactly 100%.
    """
    places = decimals + 2
    quantum = Decimal(1).scaleb(-places)
    rounded = (Decimal(probability_micro) / _MICRO_DECIMAL).quantize(quantum, ROUND_HALF_EVEN)
    return float(rounded)


def complement(probability: float, decimals: int) -> float:
    """1 - p, rounded to the same display precision as `display_probability`."""
    return round(1.0 - probability, decimals + 2)
