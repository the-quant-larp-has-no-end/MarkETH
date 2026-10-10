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
    """`POST /barista/vouchers/redeem`: the voucher is used up.

    The scan that redeems a voucher answers with `repeat` false. The same barista
    scanning that voucher again within `VOUCHER_RESCAN_SECONDS` gets the same answer
    with `repeat` true: the answer to the first scan may not have arrived. Nothing
    is booked for a repeat.
    """

    voucher_id: UUID
    product_id: str
    product_name: str
    """What to hand over: the product the voucher was issued for."""

    value_cr: CreditAmount
    redeemed_at: datetime
    """When the voucher was redeemed: the instant of the first scan, also in a repeat."""

    repeat: bool
    """False for the scan that redeemed the voucher. True when this barista redeemed
    it a moment ago and scanned it again: hand the product over once."""
