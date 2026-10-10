"""Free credits by rule: the signup grant and the monthly grants (SPEC §2, Appendix A.3, A.8).

Both are booked from `vis:issuance` to the user. What makes each grant happen once is
its `grants` row: unique per (user, period), inserted with ON CONFLICT DO
NOTHING. Only the caller that gets a row back books the credit.

Monthly grants need no cron job: they are booked lazily when a user makes a request
(`UserUpkeepService`), and an admin can book everything owed at once.
"""

import logging
from datetime import date, datetime

from app.core.clock import Clock, first_of_next_month, local_date, month_period
from app.core.config import Settings
from app.core.enums import SIGNUP_GRANT_PERIOD, AccountKind, JournalKind, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.db import database
from app.models.grant import Grant
from app.models.ledger import Account
from app.models.user import User
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.grants import MonthlyGrantRunResponse
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.ledger import (
    LedgerRef,
    LedgerService,
    PostingLine,
    monthly_grant_key,
    signup_grant_key,
)

logger = logging.getLogger(__name__)


class GrantService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        ledger: LedgerService,
        audit: AuditService,
    ) -> None:
        self._uow_factory = uow_factory
        self._grants = settings.grants
        self._timezone = settings.app.timezone
        self._clock = clock
        self._ledger = ledger
        self._audit = audit

    async def grant_signup(self, uow: UnitOfWork, user: User, account: Account) -> bool:
        """Book the signup grant once. True if this call booked it, False if it existed.

        Runs inside the caller's transaction. The caller holds the lock on `account`,
        the user's own account (`get_user_account(user.id, for_update=True)`).

        1. `grants.insert_if_absent(user.id, SIGNUP_GRANT_PERIOD, now)`. None
           means the grant exists: return False.
        2. Post `SIGNUP_GRANT`: `vis:issuance` -g, user +g, with g = `SIGNUP_GRANT_CR`,
           `ref_type=LedgerRef.GRANT`, `ref_id` the grant row's id and
           `idempotency_key=signup_grant_key(user.id)`.
        3. `grants.set_entry(grant.id, entry.id)`; return True.

        With `SIGNUP_GRANT_CR=0` the row is still inserted, nothing is posted, and the
        result is True.
        """
        now = self._clock.now()
        grant = await uow.grants.insert_if_absent(user.id, SIGNUP_GRANT_PERIOD, now)
        if grant is None:
            return False
        await self._book(
            uow,
            grant,
            account,
            kind=JournalKind.SIGNUP_GRANT,
            amount_micro=self._grants.signup_grant_micro,
            idempotency_key=signup_grant_key(user.id),
            at=now,
        )
        return True

    async def grant_owed_months(self, uow: UnitOfWork, user: User, account: Account) -> int:
        """Book every monthly grant the user is owed and return how many that was.

        Runs inside the caller's transaction; the caller holds the lock on `account`.

        A user is owed one grant for every calendar month in `APP_TIMEZONE` after the
        month of `user.created_at`, up to and including the month of `clock.now()`
        (A.3: from the month after signup, including months missed in between).

        1. Compute those periods with `local_date` and `month_period` and drop the ones
           in `grants.list_periods(user.id)`.
        2. For each remaining period, oldest first: `insert_if_absent(user.id, period,
           now)`; if a row comes back, post `MONTHLY_GRANT` (`vis:issuance` -g, user
           +g, g = `MONTHLY_GRANT_CR`, `LedgerRef.GRANT`, the grant row's id,
           `monthly_grant_key(user.id, period)`) and `set_entry`.

        A repeated call inserts and books nothing. With `MONTHLY_GRANT_CR=0` rows are
        inserted and nothing is posted; the result counts the rows.
        """
        now = self._clock.now()
        granted = frozenset(await uow.grants.list_periods(user.id))
        booked = 0
        for period in self._periods_owed(user.created_at, now):
            if period in granted:
                continue
            grant = await uow.grants.insert_if_absent(user.id, period, now)
            if grant is None:
                continue
            await self._book(
                uow,
                grant,
                account,
                kind=JournalKind.MONTHLY_GRANT,
                amount_micro=self._grants.monthly_grant_micro,
                idempotency_key=monthly_grant_key(user.id, period),
                at=now,
            )
            booked += 1
        return booked

    async def run_monthly(self, actor: AuthenticatedUser) -> MonthlyGrantRunResponse:
        """Book every owed monthly grant of every ACTIVE user (SPEC §2, A.2).

        1. Read all users in one transaction (`users.list_all()`), keeping those with
           status ACTIVE.
        2. One transaction per user, so no lock is held for long: lock the user's
           account, then `grant_owed_months`. A user whose transaction fails costs
           only that user: the exception is logged with its traceback and the user's
           id, counted, and the run goes on. Nothing is half-booked, because that
           user's transaction rolled back.

           A lock that was not granted within `POSTGRES_LOCK_TIMEOUT_MS`
           (`database.is_lock_timeout`) is such a failure too. Postgres is up; what
           was busy for too long is that user's account, held by a trade or a
           settlement, or `vis:issuance`. The user is counted as failed, a warning
           says so, and the run goes on with the next one.

           The one failure that is not a user's is Postgres itself: everything else
           `database.is_unavailable` recognises (a lost connection, a server that
           does not serve) is raised again and ends the run with the API's 503,
           instead of being counted once per user. The two are asked in this order,
           because a lock timeout is one of the errors `is_unavailable` recognises.
        3. In a final transaction write the audit row: `AuditAction.MONTHLY_GRANT_RUN`,
           target `AuditService.target(AuditTarget.GRANTS, "monthly")`, `after =
           {"users_checked", "users_failed", "grants_booked", "total_micro"}`. This
           one admin action spans transactions, so its audit row is written after
           the fact rather than with the change (A.5). It is written even when users
           failed. Each grant is a journal entry of its own all the same; if the
           process dies before this step, the grants are in the ledger and running
           again books nothing twice.

        `users_checked` counts the ACTIVE users, `users_failed` those whose transaction
        failed, `grants_booked` the grants this run booked, `total_cr` their sum. Safe
        to run at any time and to repeat; a repeat retries the users that failed.
        """
        async with self._uow_factory() as uow:
            users = [
                user for user in await uow.users.list_all() if user.status is UserStatus.ACTIVE
            ]

        users_failed = 0
        grants_booked = 0
        for user in users:
            try:
                async with self._uow_factory() as uow:
                    account = await uow.accounts.get_user_account(user.id, for_update=True)
                    if account is None:
                        raise LookupError(f"user {user.id} has no ledger account")
                    booked = await self.grant_owed_months(uow, user, account)
            except Exception as error:
                if database.is_lock_timeout(error):
                    logger.warning(
                        "Monthly grants of user %s left for the next run, a lock was not "
                        "granted in time: %r",
                        user.id,
                        error,
                    )
                elif database.is_unavailable(error):
                    raise
                else:
                    logger.exception("Monthly grants of user %s failed", user.id)
                users_failed += 1
            else:
                # Counted once the user's transaction has committed.
                grants_booked += booked

        total_micro = grants_booked * self._grants.monthly_grant_micro
        async with self._uow_factory() as uow:
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.MONTHLY_GRANT_RUN,
                target=AuditService.target(AuditTarget.GRANTS, "monthly"),
                before=None,
                after={
                    "users_checked": len(users),
                    "users_failed": users_failed,
                    "grants_booked": grants_booked,
                    "total_micro": total_micro,
                },
            )
        return MonthlyGrantRunResponse(
            users_checked=len(users),
            users_failed=users_failed,
            grants_booked=grants_booked,
            total_cr=to_cr(total_micro),
        )

    def next_grant_date(self) -> date:
        """When the next monthly grant falls due: the first day of the month after
        `clock.now()` in `APP_TIMEZONE` (`first_of_next_month(local_date(now, tz))`).

        The current month's grant is booked by the lazy upkeep, at the latest
        `UPKEEP_INTERVAL_SECONDS` after the user's first request in that month, so it
        is never the "next" one.
        """
        return first_of_next_month(local_date(self._clock.now(), self._timezone))

    def _periods_owed(self, created_at: datetime, now: datetime) -> list[str]:
        """The `YYYY-MM` of every month after the one of `created_at`, up to and
        including the one of `now`, oldest first. Months are those of `APP_TIMEZONE`."""
        today = local_date(now, self._timezone)
        day = first_of_next_month(local_date(created_at, self._timezone))
        periods: list[str] = []
        while day <= today:
            periods.append(month_period(day))
            day = first_of_next_month(day)
        return periods

    async def _book(
        self,
        uow: UnitOfWork,
        grant: Grant,
        account: Account,
        *,
        kind: JournalKind,
        amount_micro: int,
        idempotency_key: str,
        at: datetime,
    ) -> None:
        """Move a grant's credits from `vis:issuance` to the user's account and link the
        `grants` row to the journal entry.

        An amount of zero moves nothing and writes no entry (A.8): the row alone
        records that the grant was made.
        """
        if amount_micro == 0:
            return
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        entry = await self._ledger.post(
            uow,
            kind=kind,
            ref_type=LedgerRef.GRANT,
            ref_id=grant.id,
            idempotency_key=idempotency_key,
            lines=[
                PostingLine(issuance.id, -amount_micro),
                PostingLine(account.id, amount_micro),
            ],
            at=at,
        )
        await uow.grants.set_entry(grant.id, entry.id)
