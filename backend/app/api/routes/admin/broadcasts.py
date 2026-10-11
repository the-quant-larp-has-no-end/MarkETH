"""What an admin does about live streams: list the open ones, decide on a request, and
go live on a market without one, audit-logged (SPEC §6, §10, Appendix A.2)."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import AdminUser, BroadcastServiceDep
from app.schemas.broadcasts import (
    AdminBroadcastListResponse,
    MarketBroadcastResponse,
    RefuseBroadcastRequest,
)

router = APIRouter(tags=["admin-broadcasts"])


@router.get(
    "/broadcasts",
    summary="Every live stream that is requested, approved or on, earliest request first",
)
async def list_admin_broadcasts(
    admin: AdminUser, broadcasts: BroadcastServiceDep
) -> AdminBroadcastListResponse:
    return await broadcasts.admin_list(admin)


@router.post(
    "/broadcasts/{broadcast_id}/approve",
    summary="Approve a proposer's request for a live stream",
)
async def approve_broadcast(
    broadcast_id: UUID, admin: AdminUser, broadcasts: BroadcastServiceDep
) -> MarketBroadcastResponse:
    return await broadcasts.approve(admin, broadcast_id)


@router.post(
    "/broadcasts/{broadcast_id}/refuse",
    summary="Refuse a request for a live stream, or take an approval back",
)
async def refuse_broadcast(
    broadcast_id: UUID,
    body: RefuseBroadcastRequest,
    admin: AdminUser,
    broadcasts: BroadcastServiceDep,
) -> MarketBroadcastResponse:
    return await broadcasts.refuse(admin, broadcast_id, body)


@router.post(
    "/markets/{market_id}/broadcast",
    status_code=status.HTTP_201_CREATED,
    summary="Go live on a market as an admin, without a request",
)
async def start_broadcast_as_admin(
    market_id: UUID, admin: AdminUser, broadcasts: BroadcastServiceDep
) -> MarketBroadcastResponse:
    return await broadcasts.start_as_admin(admin, market_id)
