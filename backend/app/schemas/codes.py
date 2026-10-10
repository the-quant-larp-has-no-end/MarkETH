"""Redeeming a code (SPEC §2, §10)."""

from typing import Final

from pydantic import Field

from app.schemas.common import CreditAmount, RequestModel, ResponseModel

CODE_INPUT_MAX_LENGTH: Final[int] = 64
"""Room for a code with dashes and stray whitespace; the service normalises it. Not
for a URL: the client sends the code alone."""


class RedeemCodeRequest(RequestModel):
    """`POST /codes/redeem`: the code alone, as typed or as scanned.

    A code card's QR holds a URL, `{PUBLIC_BASE_URL}/wallet?code=XXXX-XXXX-XXXX` (A.6).
    The client cuts the code out of the query, in the wallet page a phone camera opens
    and in the in-app scanner alike, and sends only that. A whole URL is not a code: it
    is answered with `CODE_INVALID` and counts as an attempt.
    """

    code: str = Field(min_length=1, max_length=CODE_INPUT_MAX_LENGTH)
    """Dashes, spaces and case do not matter; O is read as 0, I and L as 1."""


class RedeemCodeResponse(ResponseModel):
    value_cr: CreditAmount
    """What the code was worth."""

    balance_cr: CreditAmount
    """The user's balance after the redemption."""
