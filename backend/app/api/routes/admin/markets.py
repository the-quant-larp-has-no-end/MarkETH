"""The seven admin actions on a market, all audit-logged (SPEC §6, §10, Appendix A.5)."""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import AdminUser, MarketLifecycleServiceDep
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.markets import MarketDetailResponse

router = APIRouter(prefix="/markets", tags=["admin-markets"])


@router.post("/{market_id}/approve", summary="Approve a proposal and open the market")
async def approve_market(
    market_id: UUID,
    body: ApproveMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.approve(admin, market_id, body)


@router.post("/{market_id}/reject", summary="Reject a proposal")
async def reject_market(
    market_id: UUID,
    body: RejectMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.reject(admin, market_id, body)


@router.post("/{market_id}/halt", summary="Suspend trading in an open market")
async def halt_market(
    market_id: UUID,
    body: HaltMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.halt(admin, market_id, body)


@router.post("/{market_id}/resume", summary="Resume trading in a halted market")
async def resume_market(
    market_id: UUID,
    body: ResumeMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.resume(admin, market_id, body)


@router.post("/{market_id}/close", summary="Close a market to trading for good")
async def close_market(
    market_id: UUID,
    body: CloseMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.close(admin, market_id, body)


@router.post("/{market_id}/resolve", summary="Resolve a closed market to YES or NO")
async def resolve_market(
    market_id: UUID,
    body: ResolveMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.resolve(admin, market_id, body)


@router.post("/{market_id}/void", summary="Void a market and refund every net stake")
async def void_market(
    market_id: UUID,
    body: VoidMarketRequest,
    admin: AdminUser,
    lifecycle: MarketLifecycleServiceDep,
) -> MarketDetailResponse:
    return await lifecycle.void(admin, market_id, body)
