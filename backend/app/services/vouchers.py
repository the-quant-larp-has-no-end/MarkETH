"""Vouchers: what credits buy (SPEC §2, Appendix A.6, A.8).

A voucher is for one product of `VOUCHER_PRODUCTS`. Issuing it moves the product's
price from the user to `vis:redemption_pending`. A barista's scan moves it on to
`vis:redeemed`; if nobody scans it within `VOUCHER_EXPIRY_DAYS`, a refund moves it back
to the user.

The row keeps what the product was when the voucher was issued: `product_id`,
`product_name` and `value_micro`. Every later entry moves the row's `value_micro` and
every answer names the row's product, whatever `VOUCHER_PRODUCTS` says by then: a price
can change and a product can leave the list without touching a voucher that exists.

Only the hash of a voucher's code is stored. The code itself is in the answer that
issued the voucher and nowhere else; its owner can have it replaced (`replace_code`),
which is also how a code that never reached the phone is recovered. A voucher's code
crosses the API in a request or response body, never in a path or a query.

A barista's scan redeems a voucher once. The barista who redeemed it is shown that
redemption again when they scan it once more within `VOUCHER_RESCAN_SECONDS`, because
the answer to the first scan may not have reached their screen; nothing is booked for
it, and to anybody else the voucher is used up.

Lock order in every method: the voucher owner's account first, then the voucher row.
Every user has an account, because the profile transaction creates the two together.

A method that writes reads the clock once, when it holds its locks: a request that
waited for a lock decides with the time at which it got it.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import AccountKind, JournalKind, VoucherStatus
from app.core.errors import (
    CodeInvalidError,
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.security import format_code, generate_code, hash_code, normalize_code
from app.core.units import to_cr
from app.models.ledger import Account
from app.models.voucher import Voucher
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.barista import VoucherRedeemResponse
from app.schemas.vouchers import (
    IssueVoucherRequest,
    VoucherCodeResponse,
    VoucherIssuedResponse,
    VoucherItem,
    VoucherListResponse,
    VoucherProductItem,
    VoucherProductListResponse,
)
from app.services import UowFactory
from app.services.ledger import (
    LedgerRef,
    LedgerService,
    PostingLine,
    voucher_issue_key,
    voucher_redeem_key,
    voucher_refund_key,
)

UNKNOWN_PRODUCT_MESSAGE: Final[str] = "Choose one of the products on offer."
"""Message of the `ValidationFailedError` that `issue` answers for a `product_id` that
`VOUCHER_PRODUCTS` does not list."""


class VoucherService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        ledger: LedgerService,
    ) -> None:
        self._uow_factory = uow_factory
        self._vouchers = settings.vouchers
        self._clock = clock
        self._ledger = ledger

    async def products(self, user: AuthenticatedUser) -> VoucherProductListResponse:
        """What a voucher can be for, and which of those products the user can have
        right now (`GET /vouchers/products`).

        One read transaction, no lock: the user's account
        (`accounts.get_user_account(user.id)`).

        One item per product of `settings.vouchers.products`, in that order, which is
        the order of `VOUCHER_PRODUCTS`: `id`, `name`, `price_cr = to_cr(price_micro)`
        and `affordable = account.balance_micro >= price_micro`. The frontend compares
        nothing (golden rule 6): `affordable` is the comparison.
        """
        async with self._uow_factory() as uow:
            balance_micro = (await _account_of(uow, user.id)).balance_micro
        return VoucherProductListResponse(
            items=[
                VoucherProductItem(
                    id=product.id,
                    name=product.name,
                    price_cr=to_cr(product.price_micro),
                    affordable=balance_micro >= product.price_micro,
                )
                for product in self._vouchers.products
            ]
        )

    async def issue(
        self, user: AuthenticatedUser, request: IssueVoucherRequest
    ) -> VoucherIssuedResponse:
        """Turn the price of a product into a voucher for it (`POST /vouchers`).

        Before any transaction: `product = settings.vouchers.product(
        request.product_id)`. None raises `ValidationFailedError` (422,
        `UNKNOWN_PRODUCT_MESSAGE`) with `details={"field": "product_id"}`.

        One transaction:
        1. Lock the user's account (`accounts.get_user_account(user.id,
           for_update=True)`). A balance below `product.price_micro` raises
           `InsufficientBalanceError` (409) with `details={"balance_cr": to_cr(...),
           "required_cr": to_cr(...)}`.
        2. `code = generate_code(VOUCHER_CODE_LENGTH)`; insert `Voucher(user_id,
           product_id=product.id, product_name=product.name, code_hash=hash_code(code),
           status=ACTIVE, value_micro=product.price_micro, created_at=now,
           expires_at=now + VOUCHER_EXPIRY_DAYS)`. This is the snapshot: id, name and
           price are copied into the row here and read from the row ever after.
        3. Post `VOUCHER_ISSUE`: user -v, `vis:redemption_pending` +v, with v the
           row's `value_micro`, `LedgerRef.VOUCHER`, the voucher's id and
           `voucher_issue_key(voucher.id)`.

        Returns the voucher with its product, the balance after it, and the code as
        `format_code(code)`. The plain code is never stored and never logged.

        The request carries no idempotency key, so sending it twice issues two
        vouchers. A client whose request went unanswered reads `GET /me/vouchers`
        first; a voucher that is there has its code replaced with `replace_code`
        instead of being issued again.

        A fresh code whose hash another voucher already has violates
        `uq_vouchers_code_hash`. That `IntegrityError` is not translated: a code is
        `VOUCHER_CODE_LENGTH` characters of 5 bits each, which makes it rare enough to
        answer 500, and the transaction it ends has written nothing, so asking again
        works.
        """
        product = self._vouchers.product(request.product_id)
        if product is None:
            raise ValidationFailedError(UNKNOWN_PRODUCT_MESSAGE, details={"field": "product_id"})

        code = generate_code(self._vouchers.code_length)
        async with self._uow_factory() as uow:
            account = await _account_of(uow, user.id, for_update=True)
            if account.balance_micro < product.price_micro:
                raise InsufficientBalanceError(
                    details={
                        "balance_cr": to_cr(account.balance_micro),
                        "required_cr": to_cr(product.price_micro),
                    }
                )
            now = self._clock.now()
            voucher = await uow.vouchers.add(
                Voucher(
                    user_id=user.id,
                    product_id=product.id,
                    product_name=product.name,
                    code_hash=hash_code(code),
                    status=VoucherStatus.ACTIVE,
                    value_micro=product.price_micro,
                    created_at=now,
                    expires_at=now + self._vouchers.ttl,
                )
            )
            pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
            await self._ledger.post(
                uow,
                kind=JournalKind.VOUCHER_ISSUE,
                ref_type=LedgerRef.VOUCHER,
                ref_id=voucher.id,
                idempotency_key=voucher_issue_key(voucher.id),
                lines=[
                    PostingLine(account.id, -voucher.value_micro),
                    PostingLine(pending.id, voucher.value_micro),
                ],
                at=now,
            )
            # `post` read the account again: this is the balance after the voucher.
            balance_micro = account.balance_micro

        return VoucherIssuedResponse(
            id=voucher.id,
            product_id=voucher.product_id,
            product_name=voucher.product_name,
            status=voucher.status,
            refunded=_is_refunded(voucher),
            value_cr=to_cr(voucher.value_micro),
            created_at=voucher.created_at,
            expires_at=voucher.expires_at,
            redeemed_at=voucher.redeemed_at,
            code=format_code(code),
            balance_cr=to_cr(balance_micro),
        )

    async def replace_code(self, user: AuthenticatedUser, voucher_id: UUID) -> VoucherCodeResponse:
        """Give one of the user's vouchers a fresh code; the code it had stops working
        (`POST /vouchers/{voucher_id}/code`).

        This is how a code is shown again. The server never had the plain code, so it
        cannot repeat it: it makes another, and the phone that lost the first answer,
        or never stored it, holds a code that works.

        One transaction:
        1. Lock the caller's account (`accounts.get_user_account(user.id,
           for_update=True)`). It is the caller's own account and no other: the
           request names a voucher, and nothing a request names decides whose account
           is locked. It is the first lock here as in every method.
        2. `voucher = vouchers.get_owned(voucher_id, user.id, for_update=True)`: the
           row is selected by its id and its owner together. None raises
           `NotFoundError` (404): there is no such voucher, or it is somebody else's.
           The two get the same answer, and a voucher of somebody else is neither
           read nor locked.
        3. The caller's own voucher whose status is not ACTIVE, or whose
           `expires_at <= now`, raises `ConflictError` (409 `CONFLICT`): it is theirs,
           and a voucher that is used up or expired takes no code. Nothing is written;
           an expired voucher is refunded by the upkeep of its owner's requests
           (`expire_due`), not here.
        4. `code = generate_code(VOUCHER_CODE_LENGTH)`; set `voucher.code_hash =
           hash_code(code)`. Nothing else changes: no credit moves, no journal entry is
           written, `expires_at` stays what it was.

        Returns `VoucherCodeResponse(voucher_id=voucher.id, code=format_code(code),
        expires_at=voucher.expires_at)`. The plain code is never stored and never
        logged; this answer is the only place it appears.

        Against a scan of the code the voucher had: `redeem` locks the same account
        before the row, so one of the two runs first. If this does, the scan finds no
        voucher under the hash it reads again and fails; if the scan does, step 3 here
        finds the voucher REDEEMED and answers 409.

        A hash another voucher already has is left to `uq_vouchers_code_hash`, as in
        `issue`.
        """
        code = generate_code(self._vouchers.code_length)
        async with self._uow_factory() as uow:
            await _account_of(uow, user.id, for_update=True)
            voucher = await uow.vouchers.get_owned(voucher_id, user.id, for_update=True)
            if voucher is None:
                raise NotFoundError
            if _effective_status(voucher, self._clock.now()) is not VoucherStatus.ACTIVE:
                raise ConflictError
            voucher.code_hash = hash_code(code)

        return VoucherCodeResponse(
            voucher_id=voucher.id,
            product_name=voucher.product_name,
            code=format_code(code),
            expires_at=voucher.expires_at,
        )

    async def list_for_user(self, user: AuthenticatedUser) -> VoucherListResponse:
        """The user's vouchers in every status, newest first, without any code
        (`GET /me/vouchers`).

        Each item names its product from the row (`product_id`, `product_name`) and
        its `value_cr` from the row's `value_micro`. `status` is the effective one: an
        ACTIVE row with `expires_at <= now` is reported EXPIRED even before its refund
        is booked. Reads only.
        """
        async with self._uow_factory() as uow:
            vouchers = await uow.vouchers.list_for_user(user.id)
        now = self._clock.now()
        return VoucherListResponse(
            items=[
                VoucherItem(
                    id=voucher.id,
                    product_id=voucher.product_id,
                    product_name=voucher.product_name,
                    status=_effective_status(voucher, now),
                    refunded=_is_refunded(voucher),
                    value_cr=to_cr(voucher.value_micro),
                    created_at=voucher.created_at,
                    expires_at=voucher.expires_at,
                    redeemed_at=voucher.redeemed_at,
                )
                for voucher in vouchers
            ]
        )

    async def redeem(self, barista: AuthenticatedUser, code: str) -> VoucherRedeemResponse:
        """A barista scans a voucher: mark it used and move its value to `vis:redeemed`
        (`POST /barista/vouchers/redeem`).

        `code` is the `code` of the request body: whatever the scanner read or the
        barista typed.
        1. `normalize_code(code)`. None, or a length other than
           `VOUCHER_CODE_LENGTH`: `CodeInvalidError`.
        2. One transaction. Read the voucher by `hash_code(normalized)` without a lock
           to learn its owner; none: `CodeInvalidError`. Lock the owner's account, then
           read the voucher again by the same hash with `for_update=True`. None now
           means its owner replaced the code in between: `CodeInvalidError`. So does a
           voucher that is not that owner's: the code would have had to pass from one
           voucher to another in between, and the account that is locked would not be
           the one a refund belongs to. Then read the clock.
        3. Status REDEEMED: a repeat when `_is_repeat` says so, otherwise
           `CodeInvalidError`. A repeat is the scan of a voucher that this very
           barista redeemed (`redeemed_by == barista.id`) at most
           `VOUCHER_RESCAN_SECONDS` ago (`now - redeemed_at <= settings.vouchers.
           rescan_window`, the last second of the window included). It is answered
           like the scan that redeemed the voucher, with the same `redeemed_at`, and
           `repeat=True`. Nothing is written: no row changes and no entry is posted.
        4. Status EXPIRED: `CodeInvalidError`.
        5. ACTIVE but `expires_at <= now`: refund it exactly as `expire_due` does and
           let the transaction commit; then, after the block, raise `CodeInvalidError`.
        6. Otherwise set `status=REDEEMED`, `redeemed_by=barista.id` and
           `redeemed_at=now` together, and post `VOUCHER_REDEEM`:
           `vis:redemption_pending` -t, `vis:redeemed` +t, with t the row's
           `value_micro` and `voucher_redeem_key(voucher.id)`. The answer carries
           `repeat=False`.

        Returns the voucher's id, its `product_id` and `product_name` from the row
        (what the barista hands over), `value_cr`, `redeemed_at` and `repeat`.

        Why a repeat is answered at all. The answer to the scan that redeems a voucher
        can get lost on its way to the barista's screen, and the barista then scans
        the same voucher again. The screen has to tell "you redeemed this a moment
        ago" from a voucher that somebody else used up, and only the server knows
        which it is. The repeat gives away nothing: it goes to the one barista who
        redeemed the voucher, who was told all of it by the first answer.

        Every failure is the one generic `CodeInvalidError` (400), so a voucher cannot
        be probed (A.6). The scan of a redeemed voucher by anybody else, or by the
        same barista after the window, fails at step 3, and a scan of a code that was
        replaced fails at step 2.
        """
        normalized = normalize_code(code)
        if normalized is None or len(normalized) != self._vouchers.code_length:
            raise CodeInvalidError
        code_hash = hash_code(normalized)

        answer: VoucherRedeemResponse | None = None
        async with self._uow_factory() as uow:
            unlocked = await uow.vouchers.get_by_hash(code_hash)
            if unlocked is None:
                raise CodeInvalidError
            account = await _account_of(uow, unlocked.user_id, for_update=True)
            voucher = await uow.vouchers.get_by_hash(code_hash, for_update=True)
            if voucher is None or voucher.user_id != account.owner_id:
                raise CodeInvalidError

            now = self._clock.now()
            if self._is_repeat(voucher, barista, now):
                return _redemption(voucher, repeat=True)
            if voucher.status is not VoucherStatus.ACTIVE:
                raise CodeInvalidError

            pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
            if voucher.expires_at <= now:
                await self._refund(uow, voucher, account.id, pending.id, now)
            else:
                voucher.status = VoucherStatus.REDEEMED
                voucher.redeemed_by = barista.id
                voucher.redeemed_at = now
                redeemed = await uow.accounts.get_system_account(AccountKind.VIS_REDEEMED)
                await self._ledger.post(
                    uow,
                    kind=JournalKind.VOUCHER_REDEEM,
                    ref_type=LedgerRef.VOUCHER,
                    ref_id=voucher.id,
                    idempotency_key=voucher_redeem_key(voucher.id),
                    lines=[
                        PostingLine(pending.id, -voucher.value_micro),
                        PostingLine(redeemed.id, voucher.value_micro),
                    ],
                    at=now,
                )
                answer = _redemption(voucher, repeat=False)

        if answer is None:
            # The voucher had expired. Its refund is committed; the scan still fails,
            # with the answer every other failure gets.
            raise CodeInvalidError
        return answer

    def _is_repeat(self, voucher: Voucher, barista: AuthenticatedUser, now: datetime) -> bool:
        """Whether a scan by `barista` at `now` is one of a voucher they redeemed a
        moment ago: the row is REDEEMED, `barista` is who redeemed it, and that was
        at most `VOUCHER_RESCAN_SECONDS` before `now`. The caller holds the lock on
        the row."""
        if voucher.status is not VoucherStatus.REDEEMED or voucher.redeemed_at is None:
            return False
        return (
            voucher.redeemed_by == barista.id
            and now - voucher.redeemed_at <= self._vouchers.rescan_window
        )

    async def expire_due(self, uow: UnitOfWork, account: Account) -> int:
        """Refund the expired vouchers of an account's owner and return how many that
        was (A.3, A.6).

        Runs inside the caller's transaction. `account` is a user's own account and
        the caller holds its lock. For each row of `vouchers.list_expired_active(
        account.owner_id, now, for_update=True)`: set `status=EXPIRED` and post
        `VOUCHER_REFUND`: `vis:redemption_pending` -t, `account` +t, with t the
        voucher's `value_micro`, `LedgerRef.VOUCHER`, the voucher's id and
        `voucher_refund_key(voucher.id)`. The refund is what the voucher took, not
        what its product takes today.

        An account that is no user's, a market's or one of VIS's, raises `ValueError`
        before anything is read: no voucher is refunded to such an account.
        """
        if account.owner_id is None:
            raise ValueError(f"the {account.kind} account {account.id} is no user's")
        now = self._clock.now()
        expired = await uow.vouchers.list_expired_active(account.owner_id, now, for_update=True)
        if not expired:
            return 0
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        for voucher in expired:
            await self._refund(uow, voucher, account.id, pending.id, now)
        return len(expired)

    async def _refund(
        self,
        uow: UnitOfWork,
        voucher: Voucher,
        owner_account_id: UUID,
        pending_account_id: UUID,
        now: datetime,
    ) -> None:
        """Mark a voucher EXPIRED and give its owner back what it took (A.8).

        The caller holds the lock on the owner's account and on the voucher row, and
        has found the row ACTIVE with `expires_at <= now`.
        """
        voucher.status = VoucherStatus.EXPIRED
        await self._ledger.post(
            uow,
            kind=JournalKind.VOUCHER_REFUND,
            ref_type=LedgerRef.VOUCHER,
            ref_id=voucher.id,
            idempotency_key=voucher_refund_key(voucher.id),
            lines=[
                PostingLine(pending_account_id, -voucher.value_micro),
                PostingLine(owner_account_id, voucher.value_micro),
            ],
            at=now,
        )


def _effective_status(voucher: Voucher, now: datetime) -> VoucherStatus:
    """The status a voucher has at `now`: an ACTIVE row with `expires_at <= now` is
    EXPIRED, whether or not its refund has been booked yet (A.6)."""
    if voucher.status is VoucherStatus.ACTIVE and voucher.expires_at <= now:
        return VoucherStatus.EXPIRED
    return voucher.status


def _redemption(voucher: Voucher, *, repeat: bool) -> VoucherRedeemResponse:
    """What a barista is told about a voucher that is REDEEMED: what to hand over, and
    when it was redeemed, which is the row's own `redeemed_at` in a repeat too."""
    if voucher.redeemed_at is None:
        raise ValueError(f"voucher {voucher.id} is {voucher.status} and was never redeemed")
    return VoucherRedeemResponse(
        voucher_id=voucher.id,
        product_id=voucher.product_id,
        product_name=voucher.product_name,
        value_cr=to_cr(voucher.value_micro),
        redeemed_at=voucher.redeemed_at,
        repeat=repeat,
    )


def _is_refunded(voucher: Voucher) -> bool:
    """Whether the refund of an expired voucher has been booked. The row says EXPIRED
    from the statement that books it (`_refund`) and never before: until then an
    expired voucher is an ACTIVE row past its `expires_at`."""
    return voucher.status is VoucherStatus.EXPIRED


async def _account_of(uow: UnitOfWork, user_id: UUID, *, for_update: bool = False) -> Account:
    """The ledger account of a user. One that is missing is a broken database, not an
    answer to anybody: a user and their account are created together and neither is
    ever deleted."""
    account = await uow.accounts.get_user_account(user_id, for_update=for_update)
    if account is None:
        raise LookupError(f"user {user_id} has no ledger account")
    return account
