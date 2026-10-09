"""Voucher codes: how VIS hands out credits (SPEC §2, Appendix A.6, A.8).

A code is `VOUCHER_CODE_LENGTH` characters of Crockford Base32, printed as
`XXXX-XXXX-XXXX`. Only its SHA-256 is stored, so the plain codes exist in exactly one
place: the response of the request that created them. Codes are given away, never sold
(golden rule 9).
"""

from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.schemas.admin.code_batches import (
    CodeBatchCreatedResponse,
    CodeBatchListResponse,
    CreateCodeBatchRequest,
    RevokeCodesRequest,
    RevokeCodesResponse,
)
from app.schemas.vouchers import RedeemCodeResponse
from app.services import UowFactory
from app.services.audit import AuditService
from app.services.ledger import LedgerService

ISSUANCE_LOCK: Final[str] = "code-issuance"
"""Name of the advisory lock that makes batch creations check the monthly cap one at a
time (A.6)."""


class VoucherCodeService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        ledger: LedgerService,
        audit: AuditService,
        rate_limiter: RateLimiter,
    ) -> None:
        vouchers = settings.vouchers
        self._uow_factory = uow_factory
        self._vouchers = vouchers
        self._timezone = settings.app.timezone
        self._public_base_url = settings.app.public_base_url
        self._clock = clock
        self._ledger = ledger
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._redeem_limit_per_user = RateLimit(
            "code-redeem-user",
            vouchers.redeem_limit_per_user,
            vouchers.redeem_limit_per_user_window_seconds,
        )
        self._redeem_limit_per_ip = RateLimit(
            "code-redeem-ip",
            vouchers.redeem_limit_per_ip,
            vouchers.redeem_limit_per_ip_window_seconds,
        )

    async def create_batch(
        self, actor: AuthenticatedUser, request: CreateCodeBatchRequest
    ) -> CodeBatchCreatedResponse:
        """Create a batch of codes and return them, once (`POST /admin/code-batches`).

        1. The value is `request.value_cr`, or `VOUCHER_CODE_DEFAULT_VALUE_CR` when it
           is left out. Outside `VOUCHER_CODE_MIN_VALUE_CR` to
           `VOUCHER_CODE_MAX_VALUE_CR`, or `expires_at <= now`: `ValidationFailedError`
           (422) naming the field in `details.field`.
        2. One transaction. `uow.advisory_lock(ISSUANCE_LOCK)`. If
           `codes.issued_value_since(start_of_month(now, APP_TIMEZONE)) + count * value
           > MAX_CODE_ISSUANCE_PER_MONTH`: `LimitExceededError` (409) with
           `details={"issued_cr", "requested_cr", "cap_cr"}`.
        3. Insert the batch (`created_by=actor.id`, `created_at=now`). Generate `count`
           distinct codes with `generate_code(VOUCHER_CODE_LENGTH)` and insert one
           `VoucherCode(batch_id, code_hash=hash_code(code), status=ACTIVE)` each
           (`codes.add_all`).
        4. Audit `CODE_BATCH_CREATE`, target `AuditService.target(
           AuditTarget.CODE_BATCH, batch.id)`, `after = {"label", "count",
           "value_micro", "expires_at", "one_per_user"}`. Never a code.

        Returns the batch with all codes active, and per code `format_code(code)` and
        `redeem_url = f"{PUBLIC_BASE_URL}/wallet#code={format_code(code)}"`. The plain
        codes are never stored and never logged; the browser builds the CSV and the
        QR sheet from this response (A.6).
        """
        raise NotImplementedError

    async def list_batches(self) -> CodeBatchListResponse:
        """Every batch with its code counts, newest first (`GET /admin/code-batches`).

        From `code_batches.list_with_counts()`. `total_value_cr = count * value`,
        `expired = expires_at <= now`.
        """
        raise NotImplementedError

    async def revoke(
        self, actor: AuthenticatedUser, batch_id: UUID, request: RevokeCodesRequest
    ) -> RevokeCodesResponse:
        """Revoke unredeemed codes (`POST /admin/code-batches/{id}/revoke`, A.6).

        One transaction. Lock the batch (`code_batches.get(batch_id,
        for_update=True)`); unknown raises `NotFoundError` (404).
        * `request.codes` is None: `codes.revoke(batch_id, None)` revokes every code
          that is still ACTIVE, and `revoked_at = now` is stamped on the batch unless
          it already has one.
        * Otherwise: normalise each code (`normalize_code`), drop what cannot be a
          code, and `codes.revoke(batch_id, <their hash_code values>)`. Codes of other
          batches, redeemed codes and unknown codes are not touched and not an error.

        Audit `CODE_BATCH_REVOKE` with `after = {"revoked_count", "whole_batch"}`.
        Returns how many codes this call revoked and the batch with its counts
        afterwards. Redeemed codes are never affected (SPEC §2).
        """
        raise NotImplementedError

    async def redeem(
        self, user: AuthenticatedUser, code: str, client_ip: str
    ) -> RedeemCodeResponse:
        """Credit a code's value to the user (`POST /vouchers/redeem-code`, SPEC §2).

        1. Rate limits, fail closed, counted for every attempt: hit `code-redeem-user`
           with subject `str(user.id)`, then `code-redeem-ip` with subject `client_ip`.
           A rejected hit raises `RateLimitedError(retry_after_seconds=...)` (429);
           `RedisUnavailableError` becomes `ServiceUnavailableError` (503).
        2. `normalize_code(code)` (uppercase, no dashes, O as 0, I and L as 1). None,
           or a length other than `VOUCHER_CODE_LENGTH`: `CodeInvalidError` (400).
        3. One transaction. Lock the code row (`codes.get_by_hash(hash_code(...),
           for_update=True)`), then the user's account; this order is A.6's. Load the
           batch.
        4. `CodeInvalidError`, the same generic error for all of them, when the code
           is unknown, not ACTIVE (redeemed or revoked), or its batch has
           `expires_at <= now`. Codes cannot be probed.
        5. Under the account lock: a `one_per_user` batch of which
           `codes.user_has_redeemed_in_batch(user.id, batch.id)` raises
           `LimitExceededError` (409, "You already redeemed a code of this batch.").
        6. Set `status=REDEEMED`, `redeemed_by=user.id` and `redeemed_at=now`
           together. Post `CODE_REDEMPTION`: `vis:issuance` -v, user +v, with v the
           batch's `value_micro`, `LedgerRef.VOUCHER_CODE`, the code row's id and
           `code_redemption_key(code.id)`. Set the row's `entry_id`.

        Returns the value and the balance after it. The code itself is never logged.
        """
        raise NotImplementedError
