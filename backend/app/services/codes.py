"""Codes: how VIS hands out credits (SPEC §2, Appendix A.6, A.8).

A code is `CODE_LENGTH` characters of Crockford Base32, printed as
`XXXX-XXXX-XXXX`. Only its SHA-256 is stored, so the plain codes exist in exactly one
place: the response of the request that created them. Codes are given away, never sold
(golden rule 9).
"""

from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from app.core.clock import Clock, start_of_month
from app.core.config import Settings
from app.core.enums import AccountKind, CodeStatus, JournalKind
from app.core.errors import (
    CodeInvalidError,
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.security import format_code, generate_code, hash_code, normalize_code
from app.core.units import to_cr, to_micro
from app.integrations.redis.client import RedisUnavailableError
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.models.code import Code, CodeBatch
from app.models.ledger import Account
from app.repositories.code import CodeBatchCounts
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.code_batches import (
    CodeBatchCreatedResponse,
    CodeBatchItem,
    CodeBatchListResponse,
    CreateCodeBatchRequest,
    CreatedCode,
    RevokeCodesRequest,
    RevokeCodesResponse,
)
from app.schemas.codes import RedeemCodeResponse
from app.schemas.common import Limit
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.ledger import LedgerRef, LedgerService, PostingLine, code_redemption_key

ISSUANCE_LOCK: Final[str] = "code-issuance"
"""Name of the advisory lock that makes batch creations check the monthly cap one at a
time (A.6)."""

OWN_BATCH_MESSAGE: Final[str] = "You created this batch, so you cannot redeem its codes."
"""Message of the `ForbiddenError` that `redeem` answers the creator of a batch with
who redeems one of its codes (A.6)."""

ONE_PER_USER_MESSAGE: Final[str] = "You already redeemed a code of this batch."
"""Message of the `LimitExceededError` that `redeem` answers a user with who redeems a
second code of a batch that allows one per user (A.6)."""

_NUL: Final[str] = "\x00"
"""The one character Postgres refuses in a text, and so in the label of a batch."""


class CodeService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        ledger: LedgerService,
        audit: AuditService,
        rate_limiter: RateLimiter,
    ) -> None:
        codes = settings.codes
        self._uow_factory = uow_factory
        self._codes = codes
        self._timezone = settings.app.timezone
        self._public_base_url = settings.app.public_base_url
        self._clock = clock
        self._ledger = ledger
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._redeem_limit_per_user = RateLimit(
            "code-redeem-user",
            codes.redeem_limit_per_user,
            codes.redeem_limit_per_user_window_seconds,
        )

    async def create_batch(
        self, actor: AuthenticatedUser, request: CreateCodeBatchRequest
    ) -> CodeBatchCreatedResponse:
        """Create a batch of codes and return them, once (`POST /admin/code-batches`).

        1. The value is `request.value_cr`, or `CODE_DEFAULT_VALUE_CR` when it
           is left out. Outside `CODE_MIN_VALUE_CR` to
           `CODE_MAX_VALUE_CR`, or `expires_at <= now`: `ValidationFailedError`
           (422) naming the field in `details.field`.
        2. One transaction. `uow.advisory_lock(ISSUANCE_LOCK)`. If
           `codes.issued_value_since(start_of_month(now, APP_TIMEZONE)) + count * value
           > MAX_CODE_ISSUANCE_PER_MONTH`: `LimitExceededError` (409) with
           `details={"limit": Limit.MAX_CODE_ISSUANCE_PER_MONTH, "issued_cr":
           to_cr(...), "requested_cr": to_cr(...), "cap_cr": to_cr(...)}`.
        3. Insert the batch (`created_by=actor.id`, `created_at=now`). Generate `count`
           distinct codes with `generate_code(CODE_LENGTH)` and insert one
           `Code(batch_id, code_hash=hash_code(code), status=ACTIVE)` each
           (`codes.add_all`).
        4. Audit `CODE_BATCH_CREATE`, target `AuditService.target(
           AuditTarget.CODE_BATCH, batch.id)`, `after = {"label", "count",
           "value_micro", "expires_at", "one_per_user"}`. Never a code.

        Returns the batch with all codes active, and per code `format_code(code)` and
        `redeem_url = f"{PUBLIC_BASE_URL}/wallet?code={format_code(code)}"`. The plain
        codes are never stored and never logged; the browser builds the CSV and the
        QR sheet from this response (A.6).

        `now` is read once, before the transaction: it decides whether `expires_at`
        has passed, which month the cap is counted for, and it is the batch's
        `created_at`. `expires_at` is stored and answered in UTC, whatever offset the
        request wrote it with, so this answer and a later listing spell it alike. An
        instant at the very edge of the calendar that has no date in UTC (the last day
        of the year 9999 at an offset west of Greenwich) is refused like one that has
        passed: 422 on `expires_at`.

        A label with a NUL character in it is refused before all of that, with 422 on
        `label`: Postgres stores no text that holds one, so the batch could not be
        written.

        A fresh code whose hash a code of an earlier batch already has violates
        `uq_codes_code_hash`. That `IntegrityError` is not translated: `CODE_LENGTH`
        characters of 5 bits each make it rare enough to answer 500, and the
        transaction it ends has written nothing, so asking again works.
        """
        if _NUL in request.label:
            raise ValidationFailedError(details={"field": "label"})
        now = self._clock.now()
        if request.value_cr is None:
            value_micro = self._codes.default_value_micro
        else:
            value_micro = to_micro(request.value_cr)
        if not self._codes.min_value_micro <= value_micro <= self._codes.max_value_micro:
            raise ValidationFailedError(details={"field": "value_cr"})
        expires_at = _in_utc(request.expires_at)
        if expires_at is None or expires_at <= now:
            raise ValidationFailedError(details={"field": "expires_at"})

        requested_micro = request.count * value_micro
        cap_micro = self._codes.max_issuance_per_month_micro
        async with self._uow_factory() as uow:
            await uow.advisory_lock(ISSUANCE_LOCK)
            issued_micro = await uow.codes.issued_value_since(start_of_month(now, self._timezone))
            if issued_micro + requested_micro > cap_micro:
                raise LimitExceededError(
                    details={
                        "limit": Limit.MAX_CODE_ISSUANCE_PER_MONTH,
                        "issued_cr": to_cr(issued_micro),
                        "requested_cr": to_cr(requested_micro),
                        "cap_cr": to_cr(cap_micro),
                    }
                )
            batch = await uow.code_batches.add(
                CodeBatch(
                    label=request.label,
                    value_micro=value_micro,
                    count=request.count,
                    expires_at=expires_at,
                    one_per_user=request.one_per_user,
                    created_by=actor.id,
                    created_at=now,
                )
            )
            plain_codes = self._distinct_codes(batch.count)
            await uow.codes.add_all(
                Code(batch_id=batch.id, code_hash=hash_code(code), status=CodeStatus.ACTIVE)
                for code in plain_codes
            )
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.CODE_BATCH_CREATE,
                target=AuditService.target(AuditTarget.CODE_BATCH, batch.id),
                before=None,
                after={
                    "label": batch.label,
                    "count": batch.count,
                    "value_micro": batch.value_micro,
                    "expires_at": batch.expires_at.isoformat(),
                    "one_per_user": batch.one_per_user,
                },
            )

        counts = CodeBatchCounts(batch=batch, active=batch.count, redeemed=0, revoked=0)
        return CodeBatchCreatedResponse(
            batch=_batch_item(counts, now),
            codes=[
                CreatedCode(
                    code=format_code(code),
                    redeem_url=f"{self._public_base_url}/wallet?code={format_code(code)}",
                )
                for code in plain_codes
            ],
        )

    async def list_batches(self) -> CodeBatchListResponse:
        """Every batch with its code counts, newest first (`GET /admin/code-batches`).

        From `code_batches.list_with_counts()`. `total_value_cr = count * value`,
        `expired = expires_at <= now`.
        """
        async with self._uow_factory() as uow:
            batches = await uow.code_batches.list_with_counts()
        now = self._clock.now()
        return CodeBatchListResponse(items=[_batch_item(counts, now) for counts in batches])

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
        afterwards (`code_batches.get_with_counts(batch_id)`, read after the
        revocation in the same transaction). Redeemed codes are never affected
        (SPEC §2).

        What cannot be a code is whatever `normalize_code` refuses and whatever is not
        `CODE_LENGTH` characters long once normalised. A list in which nothing is left
        revokes nothing and asks the database nothing.
        """
        async with self._uow_factory() as uow:
            batch = await uow.code_batches.get(batch_id, for_update=True)
            if batch is None:
                raise NotFoundError
            now = self._clock.now()
            whole_batch = request.codes is None
            if request.codes is None:
                revoked_count = await uow.codes.revoke(batch_id, None)
                if batch.revoked_at is None:
                    batch.revoked_at = now
            else:
                code_hashes = self._hashes_of(request.codes)
                revoked_count = await uow.codes.revoke(batch_id, code_hashes) if code_hashes else 0
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.CODE_BATCH_REVOKE,
                target=AuditService.target(AuditTarget.CODE_BATCH, batch.id),
                before=None,
                after={"revoked_count": revoked_count, "whole_batch": whole_batch},
            )
            counts = await uow.code_batches.get_with_counts(batch_id)
            if counts is None:
                raise LookupError(f"code batch {batch_id} vanished while its row was locked")

        return RevokeCodesResponse(batch=_batch_item(counts, now), revoked_count=revoked_count)

    async def redeem(self, user: AuthenticatedUser, code: str) -> RedeemCodeResponse:
        """Credit a code's value to the user (`POST /codes/redeem`, SPEC §2).

        1. The rate limit, fail closed, counted for every attempt: hit
           `code-redeem-user` with subject `str(user.id)`, `CODE_REDEEM_LIMIT_PER_USER`
           attempts per window. A rejected hit raises
           `RateLimitedError(retry_after_seconds=...)` (429); `RedisUnavailableError`
           becomes `ServiceUnavailableError` (503). It is the one rule, and it is
           per user: the portal signs every caller in, so nobody can make up an
           identity to get more attempts, and people at one event share one address.
        2. `normalize_code(code)` (uppercase, no dashes, O as 0, I and L as 1). None,
           or a length other than `CODE_LENGTH`: `CodeInvalidError` (400).
           `code` is the code alone. The client cuts it out of a scanned card's URL
           (`…/wallet?code=…`, A.6); a URL sent whole is not a code and ends here.
        3. One transaction. Lock the code row (`codes.get_by_hash(hash_code(...),
           for_update=True)`); no such code raises `CodeInvalidError`. Then lock the
           user's account; this order is A.6's. Load the batch
           (`code_batches.get(code.batch_id)`).
        4. `batch.created_by == user.id` raises `ForbiddenError(OWN_BATCH_MESSAGE)`
           (403), whatever state the code is in. Whoever made a batch holds every one
           of its plain codes, so its creator may hand them out and may not cash them
           in. Nothing is hidden by saying so: the creator knows the batch is theirs.
        5. `CodeInvalidError`, the same generic error as for a code that does not
           exist, when the code is not ACTIVE (redeemed or revoked) or its batch has
           `expires_at <= now`. Codes cannot be probed.
        6. Under the account lock: a `one_per_user` batch of which
           `codes.user_has_redeemed_in_batch(user.id, batch.id)` raises
           `LimitExceededError` (409, `ONE_PER_USER_MESSAGE`) with
           `details={"limit": Limit.ONE_CODE_PER_USER}`.
        7. Set `status=REDEEMED`, `redeemed_by=user.id` and `redeemed_at=now`
           together. Post `CODE_REDEMPTION`: `vis:issuance` -v, user +v, with v the
           batch's `value_micro`, `LedgerRef.CODE`, the code row's id and
           `code_redemption_key(code.id)`. Set the row's `entry_id`.

        The checks run in the order of the steps, so an answer names the first thing
        that is wrong: 429 or 503, 400 for what is no code, 403 for the creator, 400
        for a code that is used up, revoked or expired, 409 for a second code of a
        one-per-user batch.

        Returns the value and the balance after it. The code itself is never logged.
        """
        try:
            decision = await self._rate_limiter.hit(self._redeem_limit_per_user, str(user.id))
        except RedisUnavailableError as exc:
            raise ServiceUnavailableError from exc
        if not decision.allowed:
            raise RateLimitedError(retry_after_seconds=decision.retry_after_seconds)

        normalized = normalize_code(code)
        if normalized is None or len(normalized) != self._codes.length:
            raise CodeInvalidError

        async with self._uow_factory() as uow:
            row = await uow.codes.get_by_hash(hash_code(normalized), for_update=True)
            if row is None:
                raise CodeInvalidError
            account = await _account_of(uow, user.id, for_update=True)
            batch = await uow.code_batches.get(row.batch_id)
            if batch is None:
                raise LookupError(f"code {row.id} belongs to no batch")
            if batch.created_by == user.id:
                raise ForbiddenError(OWN_BATCH_MESSAGE)

            now = self._clock.now()
            if row.status is not CodeStatus.ACTIVE or batch.expires_at <= now:
                raise CodeInvalidError
            if batch.one_per_user and await uow.codes.user_has_redeemed_in_batch(user.id, batch.id):
                raise LimitExceededError(
                    ONE_PER_USER_MESSAGE, details={"limit": Limit.ONE_CODE_PER_USER}
                )

            row.status = CodeStatus.REDEEMED
            row.redeemed_by = user.id
            row.redeemed_at = now
            issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
            entry = await self._ledger.post(
                uow,
                kind=JournalKind.CODE_REDEMPTION,
                ref_type=LedgerRef.CODE,
                ref_id=row.id,
                idempotency_key=code_redemption_key(row.id),
                lines=[
                    PostingLine(issuance.id, -batch.value_micro),
                    PostingLine(account.id, batch.value_micro),
                ],
                at=now,
            )
            row.entry_id = entry.id
            # `post` read the account again: this is the balance after the code.
            answer = RedeemCodeResponse(
                value_cr=to_cr(batch.value_micro), balance_cr=to_cr(account.balance_micro)
            )

        return answer

    def _distinct_codes(self, count: int) -> list[str]:
        """`count` fresh codes, no two alike."""
        codes: set[str] = set()
        while len(codes) < count:
            codes.add(generate_code(self._codes.length))
        return list(codes)

    def _hashes_of(self, typed: list[str]) -> list[str]:
        """The hashes of what, among `typed`, can be a code: each once."""
        hashes: set[str] = set()
        for text in typed:
            normalized = normalize_code(text)
            if normalized is not None and len(normalized) == self._codes.length:
                hashes.add(hash_code(normalized))
        return sorted(hashes)


def _in_utc(instant: datetime) -> datetime | None:
    """`instant` in UTC, or None when it lies so close to the first or the last year a
    date can have that it has no date in UTC."""
    try:
        return instant.astimezone(UTC)
    except OverflowError:
        return None


def _batch_item(counts: CodeBatchCounts, now: datetime) -> CodeBatchItem:
    """A batch as the admin screen shows it, with what became of its codes."""
    batch = counts.batch
    return CodeBatchItem(
        id=batch.id,
        label=batch.label,
        value_cr=to_cr(batch.value_micro),
        count=batch.count,
        total_value_cr=to_cr(batch.count * batch.value_micro),
        expires_at=batch.expires_at,
        expired=batch.expires_at <= now,
        one_per_user=batch.one_per_user,
        created_at=batch.created_at,
        revoked_at=batch.revoked_at,
        active_count=counts.active,
        redeemed_count=counts.redeemed,
        revoked_count=counts.revoked,
    )


async def _account_of(uow: UnitOfWork, user_id: UUID, *, for_update: bool = False) -> Account:
    """The ledger account of a user. One that is missing is a broken database, not an
    answer to anybody: a user and their account are created together and neither is
    ever deleted."""
    account = await uow.accounts.get_user_account(user_id, for_update=for_update)
    if account is None:
        raise LookupError(f"user {user_id} has no ledger account")
    return account
