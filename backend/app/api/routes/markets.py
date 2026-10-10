"""Reading markets and proposing one (SPEC §6, §10, Appendix A.5).

Every reader is a user, so a market always comes with the viewer's side of it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, MarketServiceDep
from app.core.enums import MarketStatus
from app.schemas.markets import MarketDetailResponse, MarketListResponse, ProposeMarketRequest

router = APIRouter(prefix="/markets", tags=["markets"])

StatusFilter = Annotated[
    list[MarketStatus] | None,
    Query(
        alias="status",
        description=(
            "Effective statuses to list. Repeat the parameter for several: "
            "`?status=OPEN&status=HALTED`. Left out: every status but PROPOSED and "
            "REJECTED, which are for admins only."
        ),
    ),
]


@router.get("", summary="List markets by effective status")
async def list_markets(
    viewer: CurrentUser, markets: MarketServiceDep, statuses: StatusFilter = None
) -> MarketListResponse:
    return await markets.list_markets(viewer, statuses)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Propose a market")
async def propose_market(
    body: ProposeMarketRequest, user: CurrentUser, markets: MarketServiceDep
) -> MarketDetailResponse:
    return await markets.propose(user, body)


@router.get("/{market_id}", summary="Details, rules and price, with the viewer's stake")
async def get_market(
    market_id: UUID, viewer: CurrentUser, markets: MarketServiceDep
) -> MarketDetailResponse:
    return await markets.detail(viewer, market_id)
