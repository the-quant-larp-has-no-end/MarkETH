"""Chart history and what the API pushes (SPEC §9, §10, Appendix A.4).

`GET /markets/{id}/history` returns the chart, which is bounded in size however much
its market traded.

`GET /markets/{id}/stream` is a server-sent event stream of one market that carries two
kinds of event; the SSE `event:` field and the `type` field of the JSON in `data:` both
name the kind:

    event: quote            a trade moved the price          data: QuoteEvent
    event: status           the market's status changed      data: StatusEvent

It starts with one `quote` event holding the current state, and sends a comment line
(`: keep-alive`) when nothing happened for `SSE_HEARTBEAT_SECONDS`.

The event stream, `GET /stream`, pushes everything a tab follows in one answer.
`LiveStreamQuery` is its query string. Each event is one server-sent event: `event:` is
its name and `data:` its JSON body, a model of `LIVE_EVENTS` whose `type` repeats the
name and whose `ts` is the instant of what it reports:

    event: ready                 the stream is open           data: ReadyEvent
    event: beat                  the stream is alive          data: BeatEvent
    event: market.quote          a trade moved a price        data: MarketQuoteEvent
    event: market.status         a market's status changed    data: MarketStatusEvent
    event: leaderboard.changed   the ranking moved            data: LeaderboardChangedEvent
    event: me.changed            something of the caller's    data: MeChangedEvent

An event is a hint to redraw or to read again, never a source of truth: a client ignores
a price whose `version` is not newer than what it shows, and reads every
`live_poll_fallback_seconds` (from `/config`) while it holds no stream. No body says whom
it is for: that is decided where an event is built and never reaches a browser.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import Field

from app.core.enums import MarketStatus
from app.core.live import TOPIC_MAX_LENGTH, TOPIC_PATTERN, TOPICS_HARD_MAX, Topic, parse_topic
from app.schemas.common import CreditAmount, Probability, RequestModel, ResponseModel


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
    """`GET /markets/{id}/history`: the chart of a market, points in ascending `version`
    order.

    The first point is the opening and the last is the latest price. Where a market
    has more points than an answer holds, the ones in between are thinned evenly:
    versions rise from point to point and may skip.
    """

    points: list[HistoryPoint]
    source: HistorySource
    until: datetime
    """The right edge of the chart: the server's now while the market can still trade,
    otherwise the moment trading ended. No point lies after it. A chart is drawn up
    to here, and with no clock of the device."""


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


# --- The event stream --------------------------------------------------------------

TAB_PATTERN: Final[str] = r"^[A-Za-z0-9_-]{8,36}$"
"""The id a tab gives itself, as a regular expression: 8 to 36 letters, digits, hyphens
and underscores. A UUID fits it."""

LiveTab = Annotated[str, Field(pattern=TAB_PATTERN)]
"""The id of the tab that opens a stream."""

LiveTopic = Annotated[str, Field(max_length=TOPIC_MAX_LENGTH, pattern=TOPIC_PATTERN)]
"""One topic as a request names it: `markets`, `leaderboard`, `market:<market id>` or
`broadcast:<market id>` (`app.core.live`)."""


class LiveStreamQuery(RequestModel):
    """Query string of `GET /stream`: which tab asks, and what it follows.

    Naming a topic grants nothing. A stream that names a market its viewer is not
    inside of, or one that does not exist, opens like any other and receives nothing
    for that topic. Every stream also carries its viewer's own events, which no request
    names.
    """

    tab: LiveTab
    """The tab's own id, made once per page load: every stream of a page load names the
    same one. A stream that opens ends the one its caller holds under the same `tab`,
    so a stream a browser left behind gives its place back."""

    topic: list[LiveTopic] = Field(default_factory=list, max_length=TOPICS_HARD_MAX)
    """What the stream follows: the parameter is given once per topic. A topic named
    twice counts once. How many a stream may follow is `LIVE_TOPICS_PER_STREAM`."""

    @property
    def topics(self) -> tuple[Topic, ...]:
        """The topics the request names, each once, in the order they are first named."""
        return tuple(dict.fromkeys(parse_topic(named) for named in self.topic))


class ReadyEvent(ResponseModel):
    """`event: ready`. The stream is open: the last of what an opening stream gets. The
    stream carries nothing that happened before it, so a tab that hears it reads once
    what its topics cover."""

    type: Literal["ready"]
    ts: datetime


class BeatEvent(ResponseModel):
    """`event: beat`. Nothing happened and the stream is alive: sent every
    `live_heartbeat_seconds` (from `/config`)."""

    type: Literal["beat"]
    ts: datetime


class MarketQuoteEvent(ResponseModel):
    """`event: market.quote`. A trade moved the price of a market. It holds the numbers
    `GET /markets/{id}` reports for that `version`, and `ts` is the instant of the
    trade: the `t` of the chart's point."""

    type: Literal["market.quote"]
    market_id: UUID
    version: int
    p_yes: Probability
    p_no: Probability
    volume_cr: CreditAmount
    ts: datetime


class MarketStatusEvent(ResponseModel):
    """`event: market.status`. The status of a market changed: by an admin's action or
    by the circuit breaker."""

    type: Literal["market.status"]
    market_id: UUID
    status: MarketStatus
    """The effective status at `ts`."""

    trading_open: bool
    ts: datetime


class LeaderboardChangedEvent(ResponseModel):
    """`event: leaderboard.changed`. Something that moves the ranking happened; the
    leaderboard is read again."""

    type: Literal["leaderboard.changed"]
    ts: datetime


class MeChangedEvent(ResponseModel):
    """`event: me.changed`. Something of the caller's own changed: a balance, a position,
    a notice, the profile or the role. It carries no figure; what `GET /me` and the
    reads under it answer is read again."""

    type: Literal["me.changed"]
    ts: datetime


LIVE_EVENTS: Final[tuple[type[ResponseModel], ...]] = (
    ReadyEvent,
    BeatEvent,
    MarketQuoteEvent,
    MarketStatusEvent,
    LeaderboardChangedEvent,
    MeChangedEvent,
)
"""Every event model of the event stream. The name of an event is the `type` of its
model, and no two models share one."""
