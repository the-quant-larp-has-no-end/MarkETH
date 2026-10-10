"""`/me` and its lists: what a user reads about themselves (SPEC §10, A.2)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AccountServiceDep, CurrentUser
from app.schemas.common import PageQuery
from app.schemas.me import (
    LedgerListResponse,
    MeResponse,
    PortfolioResponse,
    ProposalListResponse,
    TradeListResponse,
)

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", summary="Profile, balance and next monthly grant")
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
