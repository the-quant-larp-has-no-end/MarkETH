"""`/barista/*`: role BARISTA or ADMIN (SPEC §2, §10). The guard is on the whole prefix."""

from fastapi import APIRouter

from app.api.deps import BaristaUser, VoucherServiceDep
from app.schemas.barista import RedeemVoucherRequest, VoucherRedeemResponse

router = APIRouter(prefix="/barista", tags=["barista"])


@router.post("/vouchers/redeem", summary="Mark a scanned voucher as used")
async def redeem_voucher(
    body: RedeemVoucherRequest, barista: BaristaUser, vouchers: VoucherServiceDep
) -> VoucherRedeemResponse:
    return await vouchers.redeem(barista, body.code)
