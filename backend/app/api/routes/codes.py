"""Redeeming a code (SPEC §2, §10)."""

from fastapi import APIRouter

from app.api.deps import CodeServiceDep, CurrentUser
from app.schemas.codes import RedeemCodeRequest, RedeemCodeResponse

router = APIRouter(prefix="/codes", tags=["codes"])


@router.post("/redeem", summary="Redeem a code for its value")
async def redeem_code(
    body: RedeemCodeRequest, user: CurrentUser, codes: CodeServiceDep
) -> RedeemCodeResponse:
    return await codes.redeem(user, body.code)
