"""The `quote.updated` outbox payload (SPEC §8 step 7, §9, A.4)."""

import json
from datetime import UTC, datetime, timedelta, timezone
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


@pytest.mark.parametrize("missing", ["market_id", "ts", "version", "trade_id"])
def test_a_payload_with_a_missing_field_is_refused(missing: str) -> None:
    payload = quote_updated_payload(TRADE_POINT)
    del payload[missing]
    with pytest.raises(KeyError):
        quote_point_from_payload(payload)
