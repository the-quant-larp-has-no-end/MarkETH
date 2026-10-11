"""Users as an admin manages them (SPEC §3, §10, Appendix A.3, A.5, A.8).

An admin lists the users, suspends an account and lifts a suspension, gives and takes
roles, and credits or debits a balance. One adjustment adds at most
`MAX_ADMIN_ADJUSTMENT_CR` to a balance and takes at most what the balance holds. The
list is a read. The four actions change something, and they share these rules:

* Only an admin acts. An `actor` for whom `actor.is_admin` is false raises
  `ForbiddenError` (403) before anything is read. The routes have a guard of their own;
  the rule also holds for whoever else calls this service.
* Nobody acts on their own account: `user_id == actor.id` raises
  `ForbiddenError(OWN_ACCOUNT_MESSAGE)` (403), also before anything is read. An admin who
  could lift their own suspension, give themselves a role or credit themselves would
  answer to nobody.
* A reason with a NUL character in it raises `ValidationFailedError` (422) naming its
  field: Postgres stores no such text (`markets.refuse_nul`).
* One transaction. An unknown user raises `NotFoundError` (404).
* One `audit_log` row per change, in that transaction, with `actor_id=actor.id` and the
  target `AuditService.target(AuditTarget.USER, user.id)`. `before` and `after` hold
  what changed and what the admin said about it.

A bootstrap admin, an address of `ADMIN_EMAILS`, is an admin by the server's
configuration (`EmailPolicy`). No admin can change the role of such an account or
suspend it: the answer is 403, with a sentence of its own for each. Its suspension can
be lifted and its balance adjusted like anybody's.

Nothing here tells the user's open requests. `IdentityService` reads the row with every
request, so a suspension, its end and a role all hold from the user's next request on.

A suspension also takes down what the account wrote in the live chats that run
(`ChatService.remove_of_user`, SPEC §6). That is the chat's work inside the transaction
of the suspension: the audit rows of the deleted messages and the event that tells the
open chats are the chat's own.
"""

import json
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import AccountKind, JournalKind, UserStatus
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.security import sha256_hex
from app.core.units import format_tokens, to_cr, to_micro
from app.models.ledger import Account
from app.models.user import User
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.users import (
    AdjustBalanceRequest,
    AdminUserItem,
    AdminUserListQuery,
    AdminUserListResponse,
    BalanceAdjustmentResponse,
    ReinstateUserRequest,
    SetUserRoleRequest,
    SuspendUserRequest,
)
from app.schemas.common import Limit
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.chat import ChatService
from app.services.email_policy import EmailPolicy
from app.services.ledger import LedgerRef, LedgerService, PostingLine, admin_adjustment_key
from app.services.markets import refuse_nul

OWN_ACCOUNT_MESSAGE: Final[str] = "You cannot do this to your own account. Ask another admin."
"""Message of the `ForbiddenError` every action here answers an admin with who names
their own account."""

BOOTSTRAP_ROLE_MESSAGE: Final[str] = (
    "This account is an admin by the server's configuration. Its role cannot be changed here."
)
"""Message of the `ForbiddenError` that `set_role` answers for a bootstrap admin."""

BOOTSTRAP_SUSPEND_MESSAGE: Final[str] = (
    "This account is an admin by the server's configuration. It cannot be suspended."
)
"""Message of the `ForbiddenError` that `suspend` answers for a bootstrap admin."""

ALREADY_SUSPENDED_MESSAGE: Final[str] = "This account is already suspended."
"""Message of the `ConflictError` that `suspend` answers for a SUSPENDED account."""

NOT_SUSPENDED_MESSAGE: Final[str] = "This account is not suspended."
"""Message of the `ConflictError` that `reinstate` answers for an ACTIVE account."""

DEBIT_ABOVE_BALANCE_MESSAGE: Final[str] = "This account holds fewer tokens than that."
"""Message of the `InsufficientBalanceError` that `adjust_balance` answers a debit with
that is larger than the balance."""

CURSOR_FIELD: Final[str] = "cursor"
"""`details.field` of the `ValidationFailedError` that refuses a cursor."""


def adjustment_limit_message(cap_micro: int) -> str:
    """Message of the `LimitExceededError` that `adjust_balance` answers a credit with
    that is above `MAX_ADMIN_ADJUSTMENT_CR`: it names that figure in tokens."""
    return f"One adjustment can add at most {format_tokens(cap_micro)}."


_NUL: Final[str] = "\x00"
"""The one character Postgres holds in no text, and so in nobody's name or address."""


class AdminUserService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        email_policy: EmailPolicy,
        ledger: LedgerService,
        audit: AuditService,
        chat: ChatService,
    ) -> None:
        self._uow_factory = uow_factory
        self._max_adjustment_micro = settings.risk.max_admin_adjustment_micro
        self._clock = clock
        self._email_policy = email_policy
        self._ledger = ledger
        self._audit = audit
        self._chat = chat

    async def list_users(self, query: AdminUserListQuery) -> AdminUserListResponse:
        """One page of the users, newest profile first (`GET /admin/users`).

        Filters, from `query`. All are optional, and every one that is given must hold:

        * `q`: the username, the name or the address contains it, whatever the case of
          its letters. It is taken literally, so a `%` or an `_` matches that
          character and nothing else. A `q` with a NUL character in it matches nobody:
          a query string can carry one, no column holds one, and Postgres refuses even
          to compare with it, so that list is answered without asking the database.
        * `role`: the role in effect, the one an item reports. ADMIN lists the
          bootstrap admins beside the accounts whose role was set to ADMIN, and USER
          and BARISTA list no bootstrap admin, whatever its row says.
        * `status`: ACTIVE or SUSPENDED.

        Paging. The cursor is the id of the last user of the previous page, as text.
        One that is not a UUID raises `ValidationFailedError` (422) with
        `details.field = "cursor"`.

        1. One read transaction, one statement: `users.search(text=query.q,
           role=query.role, status=query.status,
           always_admin=EmailPolicy.bootstrap_admins, before_id=<cursor>,
           limit=query.limit + 1)`. It joins each user to their account, so a page
           costs one query however many users it holds.
        2. Return the first `query.limit` rows. `next_cursor` is the id of the last
           returned user when one more row came back, else null.

        Reading the list writes nothing: no audit row, as for every other page an
        admin reads.
        """
        before_id = _user_cursor(query.cursor)
        if query.q is not None and _NUL in query.q:
            return AdminUserListResponse(items=[], next_cursor=None)

        async with self._uow_factory() as uow:
            rows = await uow.users.search(
                text=query.q,
                role=query.role,
                status=query.status,
                always_admin=self._email_policy.bootstrap_admins,
                before_id=before_id,
                limit=query.limit + 1,
            )

        shown = rows[: query.limit]
        more = len(rows) > query.limit
        return AdminUserListResponse(
            items=[self._item(row.user, row.balance_micro) for row in shown],
            next_cursor=str(shown[-1].user.id) if more else None,
        )

    async def suspend(
        self, actor: AuthenticatedUser, user_id: UUID, request: SuspendUserRequest
    ) -> AdminUserItem:
        """Suspend an account (`POST /admin/users/{id}/suspend`).

        After the rules every action shares, one transaction:

        1. Lock the user's row (`users.get(user_id, for_update=True)`).
        2. A bootstrap admin: `ForbiddenError(BOOTSTRAP_SUSPEND_MESSAGE)` (403).
        3. An account that is SUSPENDED: `ConflictError(ALREADY_SUSPENDED_MESSAGE)`
           (409). Of two admins who suspend one account at once, the second waits for
           the first on the row and gets this.
        4. Set `status = SUSPENDED`. Audit `USER_SUSPEND` with `before = {"status":
           "ACTIVE"}` and `after = {"status": "SUSPENDED", "reason": ...}`.
        5. `chat.remove_of_user(uow, user.id, actor.id)`: the chat deletes what the
           account wrote in the live chats that run. It keeps the deleted messages in
           audit rows of its own, `CHAT_MESSAGE_REMOVE_OF_USER` with `actor_id=actor.id`
           and one row per broadcast, and announces `broadcast.changed` to each of
           those markets, so an open chat reads again. An account that wrote in no
           running chat: nothing is deleted, audited or announced.

        Returns the user as the list shows them. From their next request on every
        guard refuses the account (`app/api/deps.py`). Its balance, positions and
        vouchers stay as they are; a suspended account is booked no monthly grant and
        is still refunded an expired voucher (`UserUpkeepService.run`).

        Step 5 is part of the transaction: a suspension whose chat messages cannot be
        deleted does not happen. A message the account sends in the instant of its
        suspension can be stored after step 5 and stay; an admin removes it
        (`ChatService.remove`). Lifting a suspension brings no message back.

        Locks: the user's row, then inside step 5 the advisory locks of those chats. A
        message that is being stored holds the lock of its chat and writes a row that
        points at its author; the row lock of step 1 lets it (`FOR NO KEY UPDATE`), so
        the two never wait for each other in a circle.
        """
        self._check_actor(actor, user_id)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            user = await _lock(uow, user_id)
            if self._email_policy.is_bootstrap_admin(user.email):
                raise ForbiddenError(BOOTSTRAP_SUSPEND_MESSAGE)
            if user.status is UserStatus.SUSPENDED:
                raise ConflictError(ALREADY_SUSPENDED_MESSAGE)
            await self._set_status(uow, actor, user, UserStatus.SUSPENDED, request.reason)
            await self._chat.remove_of_user(uow, user.id, actor.id)
            return self._item(user, (await _account_of(uow, user.id)).balance_micro)

    async def reinstate(
        self, actor: AuthenticatedUser, user_id: UUID, request: ReinstateUserRequest
    ) -> AdminUserItem:
        """Lift the suspension of an account (`POST /admin/users/{id}/reinstate`).

        After the rules every action shares, one transaction:

        1. Lock the user's row.
        2. An account that is ACTIVE: `ConflictError(NOT_SUSPENDED_MESSAGE)` (409).
        3. Set `status = ACTIVE`. Audit `USER_REINSTATE` with `before = {"status":
           "SUSPENDED"}` and `after = {"status": "ACTIVE", "reason": ...}`.

        Returns the user as the list shows them. Their next request is let in again,
        and it books the monthly grants that fell due while the account was suspended
        (`GrantService.grant_owed_months`).

        A bootstrap admin whose row says SUSPENDED is reinstated like anybody else:
        the rule about such accounts is that nobody suspends them.
        """
        self._check_actor(actor, user_id)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            user = await _lock(uow, user_id)
            if user.status is UserStatus.ACTIVE:
                raise ConflictError(NOT_SUSPENDED_MESSAGE)
            await self._set_status(uow, actor, user, UserStatus.ACTIVE, request.reason)
            return self._item(user, (await _account_of(uow, user.id)).balance_micro)

    async def set_role(
        self, actor: AuthenticatedUser, user_id: UUID, request: SetUserRoleRequest
    ) -> AdminUserItem:
        """Give an account a role (`POST /admin/users/{id}/role`).

        After the rules every action shares, one transaction:

        1. Lock the user's row.
        2. A bootstrap admin: `ForbiddenError(BOOTSTRAP_ROLE_MESSAGE)` (403). Its role
           is ADMIN for as long as `ADMIN_EMAILS` names it, and its row is left alone.
        3. The role the account already has: nothing is written, no audit row either,
           and the answer is the user as they are. Of two admins who set one role at
           once, both get what they asked for and the log shows one change.
        4. Otherwise set `role`. Audit `USER_SET_ROLE` with `before = {"role": ...}`
           and `after = {"role": ...}`.

        Returns the user as the list shows them. The role holds from their next
        request on. A suspended account can be given a role; it has none of the
        role's rights while it is suspended.
        """
        self._check_actor(actor, user_id)
        async with self._uow_factory() as uow:
            user = await _lock(uow, user_id)
            if self._email_policy.is_bootstrap_admin(user.email):
                raise ForbiddenError(BOOTSTRAP_ROLE_MESSAGE)
            if user.role is not request.role:
                before = {"role": user.role.value}
                user.role = request.role
                await self._audit.record(
                    uow,
                    actor_id=actor.id,
                    action=AuditAction.USER_SET_ROLE,
                    target=AuditService.target(AuditTarget.USER, user.id),
                    before=before,
                    after={"role": user.role.value},
                )
            return self._item(user, (await _account_of(uow, user.id)).balance_micro)

    async def adjust_balance(
        self,
        actor: AuthenticatedUser,
        user_id: UUID,
        idempotency_key: UUID,
        request: AdjustBalanceRequest,
    ) -> BalanceAdjustmentResponse:
        """Credit or debit a user's balance (`POST /admin/users/{id}/adjustments`).

        The route has validated the body and the Idempotency-Key header. After the
        rules every action shares, and before any transaction: an amount above
        `MAX_ADMIN_ADJUSTMENT_CR` raises `LimitExceededError` (409,
        `adjustment_limit_message(...)`, which names the figure in tokens) with
        `details={"limit": Limit.MAX_ADMIN_ADJUSTMENT_CR, "requested_cr": to_cr(...),
        "cap_cr": to_cr(...)}`. It bounds what one adjustment adds to a balance; the
        amount at the cap itself passes. What an adjustment takes away has no such
        bound: step 3 holds it to the balance. No key is claimed for a refused amount.

        Then one transaction:

        1. Claim the key. The request hash is `sha256_hex(json.dumps({"user_id":
           str(user_id), **request.model_dump(mode="json")}, sort_keys=True,
           separators=(",", ":")))`: the user, the amount in its one API form and the
           reason. `idempotency.claim(actor.id, str(idempotency_key), request_hash,
           clock.now())`:
           * None: the admin never used the key and this transaction owns it.
           * A row with another `request_hash`: `IdempotencyKeyReusedError` (422).
             The keys of an admin's trades are in the same table, so a key that
             admin used for a trade is such a row.
           * A row with the same hash: return `BalanceAdjustmentResponse.
             model_validate(row.response_json)` and do nothing else. It is what the
             first request answered, with the balance as it was then.
        2. `users.get(user_id)`; unknown raises `NotFoundError` (404). Then lock the
           user's account (`accounts.get_user_account(user_id, for_update=True)`): it
           is the first account lock, before the one `LedgerService.post` takes on
           `vis:issuance`. Then read the clock: `now` is the instant of the
           adjustment everywhere below.
        3. A debit of more than the account holds: `InsufficientBalanceError` (409,
           `DEBIT_ABOVE_BALANCE_MESSAGE`) with `details={"balance_cr": to_cr(...),
           "required_cr": to_cr(...)}`.
        4. Post `ADMIN_ADJUSTMENT` with a = `request.amount_cr` in µCR: `vis:issuance`
           -a, user +a. A positive a moves credits from `vis:issuance` to the user, a
           negative one from the user back to `vis:issuance`. `LedgerRef.USER`, the
           user's id, `admin_adjustment_key(actor.id, idempotency_key)`, `at=now`.
        5. Audit `USER_ADJUST_BALANCE` with `before = {"balance_micro": ...}` and
           `after = {"balance_micro": ..., "amount_micro": a, "reason": ...,
           "entry_id": ...}`.
        6. Build the response (the user's id, a as a signed amount, the balance after
           it, `created_at=now`) and store `response.model_dump(mode="json")` with
           `idempotency.store_response`. COMMIT.

        The checks run in the order of the steps, so an answer names the first thing
        that is wrong: 403 for a caller who is no admin or names their own account,
        422 for a reason that cannot be stored, 409 `LIMIT_EXCEEDED` for a credit
        above the cap, 422 `IDEMPOTENCY_KEY_REUSED`, 404, 409 `INSUFFICIENT_BALANCE`.

        Any error rolls the transaction back, the claimed key included, so the admin
        may send the request again under the same key.

        An adjustment books twice in no case. The claim answers a repeated request
        with the stored response, and behind it the journal accepts the key of step 4
        once (`uq_journal_entries_idempotency_key`).

        A suspended account is adjusted like any other, and so is another admin's.
        """
        self._check_actor(actor, user_id)
        refuse_nul(request)
        amount_micro = to_micro(request.amount_cr)
        if amount_micro > self._max_adjustment_micro:
            raise LimitExceededError(
                adjustment_limit_message(self._max_adjustment_micro),
                details={
                    "limit": Limit.MAX_ADMIN_ADJUSTMENT_CR,
                    "requested_cr": to_cr(amount_micro),
                    "cap_cr": to_cr(self._max_adjustment_micro),
                },
            )
        key = str(idempotency_key)
        request_hash = _request_hash(user_id, request)
        async with self._uow_factory() as uow:
            stored = await uow.idempotency.claim(actor.id, key, request_hash, self._clock.now())
            if stored is not None:
                if stored.request_hash != request_hash:
                    raise IdempotencyKeyReusedError
                return BalanceAdjustmentResponse.model_validate(stored.response_json)

            user = await uow.users.get(user_id)
            if user is None:
                raise NotFoundError
            account = await _account_of(uow, user.id, for_update=True)
            now = self._clock.now()
            balance_before_micro = account.balance_micro
            if balance_before_micro + amount_micro < 0:
                raise InsufficientBalanceError(
                    DEBIT_ABOVE_BALANCE_MESSAGE,
                    details={
                        "balance_cr": to_cr(balance_before_micro),
                        "required_cr": to_cr(-amount_micro),
                    },
                )

            issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
            entry = await self._ledger.post(
                uow,
                kind=JournalKind.ADMIN_ADJUSTMENT,
                ref_type=LedgerRef.USER,
                ref_id=user.id,
                idempotency_key=admin_adjustment_key(actor.id, idempotency_key),
                lines=[
                    PostingLine(issuance.id, -amount_micro),
                    PostingLine(account.id, amount_micro),
                ],
                at=now,
            )
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.USER_ADJUST_BALANCE,
                target=AuditService.target(AuditTarget.USER, user.id),
                before={"balance_micro": balance_before_micro},
                after={
                    # `post` read the account again: this is the balance after it.
                    "balance_micro": account.balance_micro,
                    "amount_micro": amount_micro,
                    "reason": request.reason,
                    "entry_id": str(entry.id),
                },
            )
            response = BalanceAdjustmentResponse(
                user_id=user.id,
                amount_cr=to_cr(amount_micro),
                balance_cr=to_cr(account.balance_micro),
                created_at=now,
            )
            await uow.idempotency.store_response(actor.id, key, response.model_dump(mode="json"))
        return response

    def _check_actor(self, actor: AuthenticatedUser, user_id: UUID) -> None:
        """The two rules about who acts: an admin, and never on their own account."""
        if not actor.is_admin:
            raise ForbiddenError
        if user_id == actor.id:
            raise ForbiddenError(OWN_ACCOUNT_MESSAGE)

    async def _set_status(
        self,
        uow: UnitOfWork,
        actor: AuthenticatedUser,
        user: User,
        status: UserStatus,
        reason: str,
    ) -> None:
        """Move the locked `user` to `status` and write the audit row of that change."""
        before = {"status": user.status.value}
        user.status = status
        suspended = status is UserStatus.SUSPENDED
        await self._audit.record(
            uow,
            actor_id=actor.id,
            action=AuditAction.USER_SUSPEND if suspended else AuditAction.USER_REINSTATE,
            target=AuditService.target(AuditTarget.USER, user.id),
            before=before,
            after={"status": status.value, "reason": reason},
        )

    def _item(self, user: User, balance_micro: int) -> AdminUserItem:
        """A user as the admin panel shows them: the row, the role in effect, whether
        that role is the configuration's, and the balance."""
        return AdminUserItem(
            id=user.id,
            email=user.email,
            name=user.name,
            username=user.username,
            degree_level=user.degree_level,
            degree_programme=user.degree_programme,
            role=self._email_policy.effective_role(user.email, user.role),
            status=user.status,
            bootstrap_admin=self._email_policy.is_bootstrap_admin(user.email),
            balance_cr=to_cr(balance_micro),
            created_at=user.created_at,
        )


async def _lock(uow: UnitOfWork, user_id: UUID) -> User:
    """The user's row, locked: what `suspend`, `reinstate` and `set_role` change."""
    user = await uow.users.get(user_id, for_update=True)
    if user is None:
        raise NotFoundError
    return user


async def _account_of(uow: UnitOfWork, user_id: UUID, *, for_update: bool = False) -> Account:
    """The ledger account of a user. One that is missing is a broken database, not an
    answer to anybody: a user and their account are created together and neither is
    ever deleted."""
    account = await uow.accounts.get_user_account(user_id, for_update=for_update)
    if account is None:
        raise LookupError(f"user {user_id} has no ledger account")
    return account


def _request_hash(user_id: UUID, request: AdjustBalanceRequest) -> str:
    """What makes two requests under one Idempotency-Key the same adjustment: the user
    and the body. The amount is dumped in its one API form, so `"25"` and `"25.000000"`
    are one request."""
    body = {"user_id": str(user_id), **request.model_dump(mode="json")}
    return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _user_cursor(cursor: str | None) -> UUID | None:
    """The user id a cursor of the list stands for; None for the first page."""
    if cursor is None:
        return None
    try:
        return UUID(cursor)
    except ValueError:
        raise ValidationFailedError(details={"field": CURSOR_FIELD}) from None
