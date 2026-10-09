"""Redeeming a voucher code (SPEC §2, §10)."""

from typing import Final

from pydantic import Field

from app.schemas.common import CreditAmount, RequestModel, ResponseModel

CODE_INPUT_MAX_LENGTH: Final[int] = 64
"""Room for a code with dashes and stray whitespace; the service normalises it."""


class RedeemCodeRequest(RequestModel):
    """`POST /vouchers/redeem-code`: the code as typed or scanned."""

    code: str = Field(min_length=1, max_length=CODE_INPUT_MAX_LENGTH)


class RedeemCodeResponse(ResponseModel):
    value_cr: CreditAmount
    """What the code was worth."""

    balance_cr: CreditAmount
    """The user's balance after the redemption."""
