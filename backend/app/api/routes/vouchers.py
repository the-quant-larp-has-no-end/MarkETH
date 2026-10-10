"""Vouchers of the calling user and what they can be for (SPEC §2, §10, Appendix A.2, A.6)."""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, VoucherServiceDep
from app.schemas.vouchers import (
    IssueVoucherRequest,
    VoucherCodeResponse,
    VoucherIssuedResponse,
    VoucherListResponse,
    VoucherProductListResponse,
)

router = APIRouter(tags=["vouchers"])


@router.get("/vouchers/products", summary="What a voucher can be for, and what the caller can have")
async def list_voucher_products(
    user: CurrentUser, vouchers: VoucherServiceDep
) -> VoucherProductListResponse:
    return await vouchers.products(user)


@router.post(
    "/vouchers",
    status_code=status.HTTP_201_CREATED,
    summary="Turn credits into a voucher for a product",
)
async def issue_voucher(
    body: IssueVoucherRequest, user: CurrentUser, vouchers: VoucherServiceDep
) -> VoucherIssuedResponse:
    return await vouchers.issue(user, body)


@router.post(
    "/vouchers/{voucher_id}/code",
    summary="Give an own voucher a fresh code; the one it had stops working",
)
async def replace_voucher_code(
    voucher_id: UUID, user: CurrentUser, vouchers: VoucherServiceDep
) -> VoucherCodeResponse:
    return await vouchers.replace_code(user, voucher_id)


@router.get("/me/vouchers", summary="Own vouchers, pending and past")
async def list_my_vouchers(user: CurrentUser, vouchers: VoucherServiceDep) -> VoucherListResponse:
    return await vouchers.list_for_user(user)
