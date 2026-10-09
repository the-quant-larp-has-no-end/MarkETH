"""Reading markets and proposing one (SPEC §6, §10, Appendix A.5).

Reading is public; a session only adds the viewer's own position. Proposing needs one.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, MarketServiceDep, OptionalUser
from app.core.enums import MarketStatus
from app.schemas.markets import MarketDetailResponse, MarketListResponse, ProposeMarketRequest

router = APIRouter(prefix="/markets", tags=["markets"])

StatusFilter = Annotated[
    MarketStatus | None,
    Query(
        alias="status",
        description=(
            "Effective status to list. Left out: every public status. PROPOSED and "
            "REJECTED are for admins only."
        ),
    ),
]


@router.get("", summary="List markets by effective status")
async def list_markets(
    viewer: OptionalUser, markets: MarketServiceDep, status_filter: StatusFilter = None
) -> MarketListResponse:
    return await markets.list_markets(viewer, status_filter)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Propose a market")
async def propose_market(
    body: ProposeMarketRequest, user: CurrentUser, markets: MarketServiceDep
) -> MarketDetailResponse:
    return await markets.propose(user, body)


@router.get("/{market_id}", summary="Details, rules and price; with a session, the viewer's stake")
async def get_market(
    market_id: UUID, viewer: OptionalUser, markets: MarketServiceDep
) -> MarketDetailResponse:
    return await markets.detail(viewer, market_id)
