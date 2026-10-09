"""Redeeming a voucher code (SPEC §2, §10)."""

from fastapi import APIRouter

from app.api.deps import ClientIp, CurrentUser, VoucherCodeServiceDep
from app.schemas.vouchers import RedeemCodeRequest, RedeemCodeResponse

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


@router.post("/redeem-code", summary="Redeem a voucher code for its value")
async def redeem_code(
    body: RedeemCodeRequest, user: CurrentUser, codes: VoucherCodeServiceDep, ip: ClientIp
) -> RedeemCodeResponse:
    return await codes.redeem(user, body.code, ip)
