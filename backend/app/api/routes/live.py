"""The chart and the live stream of one market (SPEC §9, §10, Appendix A.4).

The stream is server-sent events. Its subscription is opened by a dependency, before
the response starts, so a market that does not exist or a Redis that is down is still
answered with a JSON error instead of an empty stream. The dependency closes the
subscription when the stream ends or the browser goes away.

Heartbeats are comment lines, which `EventSource` ignores: `: keep-alive` from the
service every `SSE_HEARTBEAT_SECONDS`, and FastAPI's own `: ping` after 15 idle seconds.
"""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Annotated, Final
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.api.deps import HistoryServiceDep, LiveQuoteServiceDep
from app.schemas.live import HistoryResponse, QuoteEvent, StatusEvent
from app.services.live_quotes import Heartbeat, LiveEvent

router = APIRouter(prefix="/markets", tags=["live"])

HEARTBEAT_COMMENT: Final[str] = "keep-alive"
ERROR_SCHEMA_REF: Final[str] = "#/components/schemas/ErrorResponse"


async def _market_events(
    market_id: UUID, live: LiveQuoteServiceDep
) -> AsyncIterator[AsyncIterator[LiveEvent]]:
    async with live.subscribe(market_id) as events:
        yield events


MarketEvents = Annotated[AsyncIterator[LiveEvent], Depends(_market_events)]


@router.get("/{market_id}/history", summary="Chart data: the price after every trade")
async def get_market_history(market_id: UUID, history: HistoryServiceDep) -> HistoryResponse:
    return await history.history(market_id)


@router.get(
    "/{market_id}/stream",
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
        # An error is answered before the stream starts, as JSON like everywhere else.
        "default": {
            "description": "Any error: `code` says which, `message` is for the user.",
            "content": {"application/json": {"schema": {"$ref": ERROR_SCHEMA_REF}}},
        },
    },
)
async def stream_market(events: MarketEvents) -> AsyncIterable[ServerSentEvent]:
    async for event in events:
        if isinstance(event, Heartbeat):
            yield ServerSentEvent(comment=HEARTBEAT_COMMENT)
        else:
            yield ServerSentEvent(event=event.type, data=event)
