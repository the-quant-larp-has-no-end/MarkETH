"""Scanning a voucher: what a barista sends and gets back (SPEC §2, §10)."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.codes import CODE_INPUT_MAX_LENGTH
from app.schemas.common import CreditAmount, RequestModel, ResponseModel


class RedeemVoucherRequest(RequestModel):
    """`POST /barista/vouchers/redeem`: the code of the voucher, as scanned or typed.

    The code travels in the body and never in a path or a query: an address is what
    proxies and browsers write down.
    """

    code: str = Field(min_length=1, max_length=CODE_INPUT_MAX_LENGTH)
    """Dashes, spaces and case do not matter; O is read as 0, I and L as 1."""


class VoucherRedeemResponse(ResponseModel):
    """`POST /barista/vouchers/redeem`: the voucher is now used up."""

    voucher_id: UUID
    product_id: str
    product_name: str
    """What to hand over: the product the voucher was issued for."""

    value_cr: CreditAmount
    redeemed_at: datetime
