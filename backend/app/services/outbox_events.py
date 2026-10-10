"""The `quote.updated` outbox event and its JSON payload (SPEC §8 step 7, §9, A.4).

A trade and a market approval each queue one event in their own transaction; the relay
copies it to ScyllaDB as a point of the price history. Writers and the relay share the
`QuotePoint` of the history store, and the two functions here are the only code that
knows how one is laid out in `outbox.payload`:

    {market_id, ts, version, p_yes_micro, q_yes_micro, q_no_micro, trade_id}

To queue an event:

    await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, quote_updated_payload(point), now)

A trade passes the market's new `version`, the YES price after the trade and the trade's
id. Approval passes version 0, `EVEN_ODDS_MICRO`, zero quantities and no trade id, so
every chart has a starting point.

Reading a payload back fails in two ways and no others. A payload that lacks a field
raises `KeyError`; one whose field holds something else than what is written here raises
`ValueError`. The relay counts on that: a row that raises either can never be delivered
and is skipped, while every other error is worth trying again.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from app.integrations.scylla.quote_history import QuotePoint

_BIGINT_MAX: Final[int] = 2**63 - 1
"""The largest number the history's columns hold; the smallest is one below its negative."""


def quote_updated_payload(point: QuotePoint) -> dict[str, Any]:
    """The JSONB payload of a `quote.updated` event. `point.ts` must be timezone-aware."""
    if point.ts.tzinfo is None:
        raise ValueError("quote timestamps must be timezone-aware")
    return {
        "market_id": str(point.market_id),
        "ts": point.ts.isoformat(),
        "version": point.version,
        "p_yes_micro": point.p_yes_micro,
        "q_yes_micro": point.q_yes_micro,
        "q_no_micro": point.q_no_micro,
        "trade_id": None if point.trade_id is None else str(point.trade_id),
    }


def quote_point_from_payload(payload: Mapping[str, Any]) -> QuotePoint:
    """The inverse of `quote_updated_payload`.

    Raises `KeyError` for a payload without one of the seven fields, and `ValueError`
    for one whose field is not what `quote_updated_payload` writes there: a value of
    another type, text that is no UUID or no instant, an instant without a timezone, or
    a number no BIGINT holds. Nothing is coerced, so a number written as text is
    refused like any other stranger.
    """
    trade_id = payload["trade_id"]
    ts = datetime.fromisoformat(_text(payload, "ts"))
    if ts.tzinfo is None:
        raise ValueError("quote timestamps must be timezone-aware")
    return QuotePoint(
        market_id=UUID(_text(payload, "market_id")),
        ts=ts,
        version=_integer(payload, "version"),
        p_yes_micro=_integer(payload, "p_yes_micro"),
        q_yes_micro=_integer(payload, "q_yes_micro"),
        q_no_micro=_integer(payload, "q_no_micro"),
        trade_id=None if trade_id is None else UUID(_text(payload, "trade_id")),
    )


def _text(payload: Mapping[str, Any], field: str) -> str:
    value = payload[field]
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text, got {type(value).__name__}")
    return value


def _integer(payload: Mapping[str, Any], field: str) -> int:
    value = payload[field]
    # A bool is an int to Python, and JSON `true` is not a number.
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer, got {type(value).__name__}")
    if not -_BIGINT_MAX - 1 <= value <= _BIGINT_MAX:
        raise ValueError(f"{field} must fit a BIGINT, got {value}")
    return value
