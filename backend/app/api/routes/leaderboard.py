"""The leaderboard (SPEC §10, Appendix A.2)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, LeaderboardServiceDep
from app.schemas.leaderboard import LeaderboardQuery, LeaderboardResponse

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", summary="Rank users by credits, win rate, trades or payout ratio")
async def get_leaderboard(
    viewer: CurrentUser,
    leaderboard: LeaderboardServiceDep,
    query: Annotated[LeaderboardQuery, Query()],
) -> LeaderboardResponse:
    return await leaderboard.ranking(viewer, query)
