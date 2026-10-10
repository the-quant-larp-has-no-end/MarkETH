"""Chart history and live updates of one market (SPEC §9, §10, Appendix A.4).

`GET /markets/{id}/history` returns the chart. `GET /markets/{id}/stream` is a
server-sent event stream that carries two kinds of event; the SSE `event:` field and the
`type` field of the JSON in `data:` both name the kind:

    event: quote            a trade moved the price          data: QuoteEvent
    event: status           the market's status changed      data: StatusEvent

The stream starts with one `quote` event holding the current state, and sends a comment
line (`: keep-alive`) when nothing happened for `SSE_HEARTBEAT_SECONDS`. Events are a
hint to refetch or redraw, never a source of truth: a client ignores an event whose
`version` is not newer than what it already shows, and falls back to polling every
`live_poll_fallback_seconds` (from `/config`) when the stream ends or answers 503.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.core.enums import MarketStatus
from app.schemas.common import CreditAmount, Probability, ResponseModel


class HistorySource(StrEnum):
    """Where the points of a chart came from (SPEC §9)."""

    SCYLLA = "scylla"
    POSTGRES = "postgres"


class HistoryPoint(ResponseModel):
    """The price right after one trade. Version 0 is the 50% a market opens at."""

    t: datetime
    p_yes: Probability
    version: int


class HistoryResponse(ResponseModel):
    """`GET /markets/{id}/history`: points in ascending `version` order."""

    points: list[HistoryPoint]
    source: HistorySource
    until: datetime
    """The right edge of the chart: the server's now while the market can still trade,
    otherwise the moment trading ended. A chart is drawn up to here, and with no
    clock of the device."""


class QuoteEvent(ResponseModel):
    """`event: quote`. The market's price after a trade, or its current state at connect."""

    type: Literal["quote"]
    market_id: UUID
    version: int
    p_yes: Probability
    p_no: Probability
    volume_cr: CreditAmount
    ts: datetime


class StatusEvent(ResponseModel):
    """`event: status`. An admin action changed the market; refetch its detail."""

    type: Literal["status"]
    market_id: UUID
    status: MarketStatus
    """The effective status at the time of the event."""

    trading_open: bool
    ts: datetime
