"""The `quote.updated` outbox payload (SPEC §8 step 7, §9, A.4).

Reading a payload back fails with `KeyError` or `ValueError` and with nothing else: those
two are what the relay skips a row for, so anything else would block the outbox on a row
that can never be delivered.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid7

import pytest

from app.integrations.scylla.quote_history import QuotePoint
from app.services.market_view import EVEN_ODDS_MICRO
from app.services.outbox_events import quote_point_from_payload, quote_updated_payload
from tests.support.clock import TEST_NOW

TRADE_POINT = QuotePoint(
    market_id=uuid7(),
    ts=TEST_NOW + timedelta(microseconds=123_456),
    version=7,
    p_yes_micro=547_512,
    q_yes_micro=1_910_000,
    q_no_micro=250_000,
    trade_id=uuid7(),
)

OPENING_POINT = QuotePoint(
    market_id=uuid7(),
    ts=TEST_NOW,
    version=0,
    p_yes_micro=EVEN_ODDS_MICRO,
    q_yes_micro=0,
    q_no_micro=0,
    trade_id=None,
)


def test_the_payload_has_exactly_the_fields_of_appendix_a4() -> None:
    payload = quote_updated_payload(TRADE_POINT)
    assert set(payload) == {
        "market_id",
        "ts",
        "version",
        "p_yes_micro",
        "q_yes_micro",
        "q_no_micro",
        "trade_id",
    }
    assert payload["market_id"] == str(TRADE_POINT.market_id)
    assert payload["trade_id"] == str(TRADE_POINT.trade_id)
    assert payload["version"] == 7
    assert payload["p_yes_micro"] == 547_512


@pytest.mark.parametrize("point", [TRADE_POINT, OPENING_POINT], ids=["trade", "opening"])
def test_a_point_survives_the_trip_through_jsonb(point: QuotePoint) -> None:
    stored = json.loads(json.dumps(quote_updated_payload(point)))
    assert quote_point_from_payload(stored) == point


def test_the_opening_point_has_no_trade() -> None:
    assert quote_updated_payload(OPENING_POINT)["trade_id"] is None


def test_the_instant_is_kept_whatever_its_timezone() -> None:
    zurich = timezone(timedelta(hours=2))
    local = QuotePoint(
        market_id=TRADE_POINT.market_id,
        ts=datetime(2026, 10, 9, 14, 0, tzinfo=zurich),
        version=1,
        p_yes_micro=500_000,
        q_yes_micro=0,
        q_no_micro=0,
        trade_id=None,
    )
    restored = quote_point_from_payload(quote_updated_payload(local))
    assert restored.ts == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert restored.ts.tzinfo is not None


def test_a_naive_timestamp_is_refused() -> None:
    naive = QuotePoint(
        market_id=TRADE_POINT.market_id,
        ts=datetime(2026, 10, 9, 12, 0),  # noqa: DTZ001  (the point of the test)
        version=1,
        p_yes_micro=500_000,
        q_yes_micro=0,
        q_no_micro=0,
        trade_id=None,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        quote_updated_payload(naive)


FIELDS = ("market_id", "ts", "version", "p_yes_micro", "q_yes_micro", "q_no_micro", "trade_id")

NOT_WHAT_IS_WRITTEN: dict[str, list[Any]] = {
    "market_id": [7, None, True, ["0199c1d4"], {"id": 1}, "", "not-a-uuid", "0199c1d4-7a55"],
    "ts": [
        1_760_011_200,
        1_760_011_200.5,
        None,
        ["2026-10-09T12:00:00+00:00"],
        "",
        "yesterday",
        "2026-13-45T99:00:00+00:00",
    ],
    "version": ["7", 7.0, 7.5, None, True, [7], {"n": 7}, 2**63, -(2**63) - 1],
    "p_yes_micro": ["547512", 0.547512, None, False],
    "q_yes_micro": ["1910000", 1_910_000.0, None, 2**64],
    "q_no_micro": ["0", 0.0, None, [0]],
    "trade_id": [7, 0, True, ["x"], "", "not-a-uuid"],
}
"""Per field, values a payload this module wrote never holds there."""


@pytest.mark.parametrize("missing", FIELDS)
def test_a_payload_with_a_missing_field_is_refused(missing: str) -> None:
    payload = quote_updated_payload(TRADE_POINT)
    del payload[missing]
    with pytest.raises(KeyError):
        quote_point_from_payload(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [(field, value) for field, values in NOT_WHAT_IS_WRITTEN.items() for value in values],
)
def test_a_payload_whose_field_holds_something_else_is_refused_with_a_value_error(
    field: str, value: Any
) -> None:
    """Never a `TypeError` or an `AttributeError`: the relay skips a row for a
    `ValueError` and would retry one of those for ever."""
    payload = quote_updated_payload(TRADE_POINT) | {field: value}

    with pytest.raises(ValueError, match=r".") as refused:
        quote_point_from_payload(payload)

    assert type(refused.value) is ValueError


@pytest.mark.parametrize(
    "naive", ["2026-10-09T12:00:00", "2026-10-09 12:00:00.123456", "2026-10-09"]
)
def test_a_payload_whose_instant_has_no_timezone_is_refused(naive: str) -> None:
    """The history store refuses such an instant when it writes; the parse must do it
    first, where the relay can tell that the row will never be delivered."""
    payload = quote_updated_payload(TRADE_POINT) | {"ts": naive}

    with pytest.raises(ValueError, match="timezone-aware"):
        quote_point_from_payload(payload)


def test_nothing_is_coerced_into_a_point() -> None:
    """A number written as text is a stranger, not a number: every field of a parsed
    point is exactly what the payload held."""
    stored = json.loads(json.dumps(quote_updated_payload(TRADE_POINT)))

    point = quote_point_from_payload(stored)

    for field in ("version", "p_yes_micro", "q_yes_micro", "q_no_micro"):
        assert type(getattr(point, field)) is int
        assert getattr(point, field) == stored[field]
    for field in ("version", "p_yes_micro", "q_yes_micro", "q_no_micro"):
        with pytest.raises(ValueError, match=f"{field} must be an integer"):
            quote_point_from_payload(stored | {field: str(stored[field])})


def test_the_largest_numbers_a_bigint_holds_are_accepted() -> None:
    largest, smallest = 2**63 - 1, -(2**63)
    payload = quote_updated_payload(TRADE_POINT) | {"q_yes_micro": largest, "q_no_micro": smallest}

    point = quote_point_from_payload(payload)

    assert (point.q_yes_micro, point.q_no_micro) == (largest, smallest)
