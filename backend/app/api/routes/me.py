"""`/me` and its lists: what a user reads about themselves, and the one thing they
mark there, a notice as seen (SPEC §10, A.2)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import AccountServiceDep, CurrentUser, NoticeServiceDep
from app.schemas.common import PageQuery
from app.schemas.me import (
    LedgerListResponse,
    MeResponse,
    NoticeItem,
    PortfolioResponse,
    ProposalListResponse,
    TradeListResponse,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", summary="Profile, balance, next monthly grant and unseen notices")
async def get_me(user: CurrentUser, account: AccountServiceDep) -> MeResponse:
    return await account.me(user)


@router.get("/positions", summary="Portfolio: positions per market with their value")
async def list_my_positions(user: CurrentUser, account: AccountServiceDep) -> PortfolioResponse:
    return await account.portfolio(user)


@router.get("/trades", summary="Own trades, newest first")
async def list_my_trades(
    user: CurrentUser, account: AccountServiceDep, page: Annotated[PageQuery, Query()]
) -> TradeListResponse:
    return await account.trades(user, page)


@router.get("/ledger", summary="Every movement on the balance, newest first")
async def list_my_ledger(
    user: CurrentUser, account: AccountServiceDep, page: Annotated[PageQuery, Query()]
) -> LedgerListResponse:
    return await account.ledger(user, page)


@router.get("/proposals", summary="Markets the user proposed and their status")
async def list_my_proposals(user: CurrentUser, account: AccountServiceDep) -> ProposalListResponse:
    return await account.proposals(user)


@router.post("/notices/{notice_id}/seen", summary="Mark a notice as seen")
async def mark_notice_seen(
    notice_id: UUID, user: CurrentUser, notices: NoticeServiceDep
) -> NoticeItem:
    return await notices.mark_seen(user, notice_id)
