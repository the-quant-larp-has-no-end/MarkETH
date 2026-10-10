"""The chart of a market and what the API pushes (SPEC §9, §10, Appendix A.4).

`GET /stream` is the event stream: one answer per tab that carries everything the app
pushes, as server-sent events. A dependency opens the stream at the hub before the answer
starts, so whatever refuses a stream is answered as JSON like everywhere else: the guards,
a query that is not valid, more topics than `LIVE_TOPICS_PER_STREAM`, a hub that holds
`LIVE_MAX_STREAMS` streams. The same dependency gives the stream's place back when the
answer ends or the browser goes away.

Its answer is `EventStream`, a plain streaming response over the bytes the hub encoded
once for everybody who receives an event. The route hands the frames over and does
nothing per event: nothing is encoded, buffered or timed here. Whether a stream is alive
is the hub's `beat`.

`GET /markets/{id}/stream` is the stream of one market. Its subscription is opened by a
dependency in the same way, so a market that does not exist or a Redis that is down is
answered with a JSON error instead of an empty stream. Its heartbeats are comment lines,
which `EventSource` ignores: `: keep-alive` from the service every
`SSE_HEARTBEAT_SECONDS`, and FastAPI's own `: ping` after 15 idle seconds.
"""

from collections.abc import AsyncIterable, AsyncIterator, Mapping
from functools import reduce
from operator import or_
from types import MappingProxyType
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import CurrentUser, HistoryServiceDep, LiveHubDep, LiveQuoteServiceDep
from app.schemas.live import (
    LIVE_EVENTS,
    HistoryResponse,
    LiveStreamQuery,
    QuoteEvent,
    StatusEvent,
)
from app.services.live_hub import LiveFrame
from app.services.live_quotes import Heartbeat, LiveEvent

router = APIRouter(tags=["live"])

STREAM_PATH: Final[str] = "/stream"
"""Where the event stream is, under the API's prefix."""

EVENT_STREAM_MEDIA_TYPE: Final[str] = "text/event-stream"

EVENT_STREAM_HEADERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        # An event is of its moment: nothing between the API and the tab keeps one.
        "Cache-Control": "no-cache",
        # nginx passes each event on as it is written (`proxy_pass_header`).
        "X-Accel-Buffering": "no",
    }
)

ANY_LIVE_EVENT: Final[Any] = reduce(or_, LIVE_EVENTS)
"""The union of the event models: what the `data:` of one event of the stream holds."""

HEARTBEAT_COMMENT: Final[str] = "keep-alive"
ERROR_SCHEMA_REF: Final[str] = "#/components/schemas/ErrorResponse"

# An error is answered before a stream starts, as JSON like everywhere else.
ERROR_BEFORE_THE_STREAM: Final[dict[str, Any]] = {
    "description": "Any error: `code` says which, `message` is for the user.",
    "content": {"application/json": {"schema": {"$ref": ERROR_SCHEMA_REF}}},
}


class EventStream(StreamingResponse):
    """The answer of the event stream: each frame as the hub encoded it, written as it
    comes, until the hub ends the stream or the reader goes away."""

    media_type = EVENT_STREAM_MEDIA_TYPE

    def __init__(self, frames: AsyncIterator[LiveFrame], status_code: int = 200) -> None:
        # The framework reads the status an operation is documented with from the
        # default of `status_code` in this signature.
        super().__init__(_as_written(frames), status_code, dict(EVENT_STREAM_HEADERS))


async def _as_written(frames: AsyncIterator[LiveFrame]) -> AsyncIterator[bytes]:
    async for frame in frames:
        yield frame.wire


async def _open_stream(
    user: CurrentUser, query: Annotated[LiveStreamQuery, Query()], hub: LiveHubDep
) -> AsyncIterator[AsyncIterator[LiveFrame]]:
    """The caller's stream at the hub, open for as long as the answer lasts.

    The hub refuses on entry, before a frame exists: that is still a JSON answer. A
    query that is not valid never gets here.
    """
    async with hub.open(user, query.tab, query.topics) as frames:
        yield frames


LiveFrames = Annotated[AsyncIterator[LiveFrame], Depends(_open_stream)]


async def _market_events(
    market_id: UUID, live: LiveQuoteServiceDep
) -> AsyncIterator[AsyncIterator[LiveEvent]]:
    async with live.subscribe(market_id) as events:
        yield events


MarketEvents = Annotated[AsyncIterator[LiveEvent], Depends(_market_events)]


@router.get(
    STREAM_PATH,
    response_class=EventStream,
    summary="Everything the app pushes (server-sent events)",
    responses={
        200: {
            "model": ANY_LIVE_EVENT,
            "description": (
                "An event stream. Each event is named by `event:` and carries the model "
                "of that name as JSON in `data:`. `ready` comes first, with a `retry:` "
                "line; `beat` says that the stream is alive."
            ),
        },
        "default": ERROR_BEFORE_THE_STREAM,
    },
)
async def stream_live(frames: LiveFrames) -> EventStream:
    return EventStream(frames)


@router.get(
    "/markets/{market_id}/history",
    summary="Chart data: the price after a market's trades, thinned to a bounded size",
)
async def get_market_history(market_id: UUID, history: HistoryServiceDep) -> HistoryResponse:
    return await history.history(market_id)


@router.get(
    "/markets/{market_id}/stream",
    response_class=EventSourceResponse,
    summary="Live quote and status events (server-sent events)",
    responses={
        200: {
            "model": QuoteEvent | StatusEvent,
            "description": (
                "An event stream. `event: quote` carries a QuoteEvent and `event: status` "
                "a StatusEvent as JSON in `data:`; comment lines keep the stream alive."
            ),
        },
        "default": ERROR_BEFORE_THE_STREAM,
    },
)
async def stream_market(events: MarketEvents) -> AsyncIterable[ServerSentEvent]:
    async for event in events:
        if isinstance(event, Heartbeat):
            yield ServerSentEvent(comment=HEARTBEAT_COMMENT)
        else:
            yield ServerSentEvent(event=event.type, data=event)
