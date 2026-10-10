"""Code batches (SPEC §2, §10, Appendix A.6)."""

from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, StrictInt

from app.schemas.codes import CODE_INPUT_MAX_LENGTH
from app.schemas.common import CreditAmount, PositiveCreditAmount, RequestModel, ResponseModel

LABEL_MAX_LENGTH: Final[int] = 100

MAX_CODES_PER_BATCH: Final[int] = 10_000
"""Bounds one request and one response. The monthly issuance cap is the real limit."""

CodeInput = Annotated[str, Field(min_length=1, max_length=CODE_INPUT_MAX_LENGTH)]


class CreateCodeBatchRequest(RequestModel):
    """`POST /admin/code-batches`."""

    label: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    count: StrictInt = Field(ge=1, le=MAX_CODES_PER_BATCH)
    value_cr: PositiveCreditAmount | None = None
    """Value of each code. Left out, it is `CODE_DEFAULT_VALUE_CR`."""

    expires_at: AwareDatetime
    one_per_user: StrictBool
    """Whether a user may redeem only one code of this batch."""


class CodeBatchItem(ResponseModel):
    """A batch and what became of its codes."""

    id: UUID
    label: str
    value_cr: CreditAmount
    count: int
    total_value_cr: CreditAmount
    """`count` times `value_cr`."""

    expires_at: datetime
    expired: bool
    one_per_user: bool
    created_at: datetime
    revoked_at: datetime | None
    """Set when the whole batch was revoked."""

    active_count: int
    redeemed_count: int
    revoked_count: int


class CreatedCode(ResponseModel):
    code: str
    """As printed on the card: `XXXX-XXXX-XXXX`."""

    redeem_url: str
    """The card's QR payload: `{PUBLIC_BASE_URL}/wallet?code=XXXX-XXXX-XXXX` (A.6). The
    code travels in the query, which nginx keeps out of its access log."""


class CodeBatchCreatedResponse(ResponseModel):
    """The new batch and, this once, its plain codes. The server keeps only hashes."""

    batch: CodeBatchItem
    codes: list[CreatedCode]


class CodeBatchListResponse(ResponseModel):
    """`GET /admin/code-batches`, newest first."""

    items: list[CodeBatchItem]


class RevokeCodesRequest(RequestModel):
    """`POST /admin/code-batches/{id}/revoke` (A.6)."""

    codes: list[CodeInput] | None = Field(
        default=None, min_length=1, max_length=MAX_CODES_PER_BATCH
    )
    """Plain codes of this batch to revoke. Left out (or null), every unredeemed code
    of the batch is revoked."""


class RevokeCodesResponse(ResponseModel):
    batch: CodeBatchItem
    revoked_count: int
    """Codes this call revoked. Redeemed codes are never affected."""
