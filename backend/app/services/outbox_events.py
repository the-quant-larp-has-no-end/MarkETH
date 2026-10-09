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
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from app.integrations.scylla.quote_history import QuotePoint


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
    """The inverse of `quote_updated_payload`. Raises `KeyError` or `ValueError` for a
    payload this module did not write."""
    trade_id = payload["trade_id"]
    return QuotePoint(
        market_id=UUID(payload["market_id"]),
        ts=datetime.fromisoformat(payload["ts"]),
        version=int(payload["version"]),
        p_yes_micro=int(payload["p_yes_micro"]),
        q_yes_micro=int(payload["q_yes_micro"]),
        q_no_micro=int(payload["q_no_micro"]),
        trade_id=None if trade_id is None else UUID(trade_id),
    )
