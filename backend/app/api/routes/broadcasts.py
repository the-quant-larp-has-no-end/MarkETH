"""A market's live video: its broadcast (SPEC §6, §10, Appendix A.2).

The proposer of a market asks for a live stream and goes live once an admin approved;
whoever is inside the market watches. A market the caller is not inside of, and one
that was never opened, is answered 404 like an id no market has, and so is a broadcast
of such a market.

Two operations stand apart. `POST /broadcasts/{id}/chunks` takes no JSON: its body is
one piece of the studio's recording, read from the request as it arrives and only once
the service has said that this caller may send it. `GET /broadcasts/{id}/access` is
asked by the proxy for every viewer before it serves a playlist or a segment, which are
no operations of this API; it answers 200 or 403 and never 404.
"""

from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from starlette.requests import ClientDisconnect

from app.api.deps import BroadcastServiceDep, CurrentUser, SettingsDep
from app.core.errors import ConflictError, ServiceUnavailableError
from app.schemas.broadcasts import (
    BroadcastAccessResponse,
    BroadcastChunkQuery,
    BroadcastChunkResponse,
    LiveBroadcastListResponse,
    MarketBroadcastResponse,
    RequestBroadcastRequest,
)

router = APIRouter(tags=["broadcasts"])

CHUNK_MEDIA_TYPE: Final[str] = "application/octet-stream"

CHUNK_BODY: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "description": (
            "One piece of the studio's recording, as the recorder hands it over: bytes, "
            "at most `broadcast.chunk_max_bytes` of `GET /config`."
        ),
        "content": {CHUNK_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    }
}
"""How the document describes the body of a piece. The route declares no body of its
own, so that the framework reads none before the route does."""


@router.get(
    "/markets/{market_id}/broadcast",
    summary="The live stream of a market, and what the caller may do about it",
)
async def get_market_broadcast(
    market_id: UUID, viewer: CurrentUser, broadcasts: BroadcastServiceDep
) -> MarketBroadcastResponse:
    return await broadcasts.for_market(viewer, market_id)


@router.post(
    "/markets/{market_id}/broadcast/request",
    status_code=status.HTTP_201_CREATED,
    summary="Ask for a live stream of a market, as its proposer",
)
async def request_broadcast(
    market_id: UUID,
    body: RequestBroadcastRequest,
    user: CurrentUser,
    broadcasts: BroadcastServiceDep,
) -> MarketBroadcastResponse:
    # The body states the acceptance of the usage policy, and its model takes nothing
    # but `true`: a request that gets here has accepted it.
    return await broadcasts.request(user, market_id)


@router.get(
    "/broadcasts/live",
    summary="The live streams that are on air in markets the caller is inside of",
)
async def list_live_broadcasts(
    viewer: CurrentUser, broadcasts: BroadcastServiceDep
) -> LiveBroadcastListResponse:
    return await broadcasts.live(viewer)


@router.post(
    "/broadcasts/{broadcast_id}/start",
    summary="Go live, as the broadcaster of an approved live stream",
)
async def start_broadcast(
    broadcast_id: UUID, user: CurrentUser, broadcasts: BroadcastServiceDep
) -> MarketBroadcastResponse:
    return await broadcasts.start(user, broadcast_id)


@router.post(
    "/broadcasts/{broadcast_id}/chunks",
    summary="Send one piece of a live stream's recording",
    openapi_extra=CHUNK_BODY,
)
async def upload_broadcast_chunk(
    broadcast_id: UUID,
    query: Annotated[BroadcastChunkQuery, Query()],
    request: Request,
    user: CurrentUser,
    broadcasts: BroadcastServiceDep,
    settings: SettingsDep,
) -> BroadcastChunkResponse:
    # The caller is judged before a byte of the body is read. A caller who may not
    # send is answered at once, and what they sent is not read. A broadcast that is
    # not live is answered to its broadcaster once the body was read and dropped, so
    # that the studio receives that answer and not a broken connection.
    most = settings.broadcast.chunk_max_bytes
    try:
        try:
            await broadcasts.admit_upload(user, broadcast_id)
        except ConflictError:
            await _body(request, most)
            raise
        data = await _body(request, most)
    except ClientDisconnect:
        # The studio's connection broke while it sent. Nothing of a body that did not
        # arrive whole reaches the media box, and the studio sends the piece again.
        # Nobody reads this answer.
        raise ServiceUnavailableError from None
    return await broadcasts.take_chunk(user, broadcast_id, query.part, query.seq, data)


@router.post(
    "/broadcasts/{broadcast_id}/end",
    summary="End a live stream, as its broadcaster or as an admin",
)
async def end_broadcast(
    broadcast_id: UUID, actor: CurrentUser, broadcasts: BroadcastServiceDep
) -> MarketBroadcastResponse:
    return await broadcasts.end(actor, broadcast_id)


@router.get(
    "/broadcasts/{broadcast_id}/access",
    summary="Whether the caller may play a live stream: asked by the proxy, not by a screen",
)
async def admit_broadcast_viewer(
    broadcast_id: UUID, viewer: CurrentUser, broadcasts: BroadcastServiceDep
) -> BroadcastAccessResponse:
    return await broadcasts.admit(viewer, broadcast_id)


async def _body(request: Request, most: int) -> bytes:
    """What `request` carries, read from its stream: at most `most + 1` bytes of it.

    One byte more than a piece may have is enough to say that the body is too large:
    the reading stops with the piece of the stream that crosses that line, however
    much follows it.
    """
    held = bytearray()
    async for piece in request.stream():
        held += piece
        if len(held) > most:
            break
    return bytes(held[: most + 1])
