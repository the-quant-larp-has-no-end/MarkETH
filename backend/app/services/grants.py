"""Free credits by rule: the signup grant and the monthly grants (SPEC §2, Appendix A.3, A.8).

Both are booked from `vis:issuance` to the user. What makes each grant happen once is
its `grants` row: unique per (user, period), inserted with ON CONFLICT DO
NOTHING. Only the caller that gets a row back books the credit.

A user's monthly grants follow their sign-up. The first falls due one calendar month
after it, the second two months after it, and so on, each on the day of the month and
at the time of day of the sign-up in `APP_TIMEZONE` (`monthly_grant`). So every user
has a grant day of their own, and each calendar month after the one of the sign-up
brings exactly one grant.

Monthly grants need no cron job: they are booked lazily when a user makes a request
(`UserUpkeepService`), and an admin can book everything that has fallen due at once.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from zoneinfo import ZoneInfo

from app.core.clock import Clock, add_months, local_date, month_period, months_later
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


@dataclass(frozen=True, slots=True)
class MonthlyGrant:
    """One monthly grant of a user's schedule: which it is, and when it falls due."""

    number: int
    """1 for the first grant after the sign-up, 2 for the one after that."""

    period: str
    """Its `grants.period`: the `YYYY-MM` of the calendar month it falls due in."""

    due_at: datetime
    """The instant from which it is owed, in UTC."""


def monthly_grant(signed_up_at: datetime, number: int, timezone: ZoneInfo) -> MonthlyGrant:
    """The `number`-th monthly grant of somebody who signed up at `signed_up_at`.

    It falls due `number` calendar months after the sign-up on the calendar of
    `timezone`: on the same day of the month, at the same time of day
    (`app.core.clock.months_later`). A month that has no such day takes its last day
    instead, and the month after that is back on the day of the sign-up: signed up on
    31 January, the grants fall due on 28 February, 31 March, 30 April. On a day the
    clocks change, a time of day they skip falls due an hour later on the clock, and
    one they show twice the first time they show it.

    Its period is the `number`-th calendar month after the one of the sign-up, which
    is the month the due instant lies in. Grant 1 is in the month after the sign-up,
    grant 2 in the month after that: no month has two grants and none is left out.
    """
    signed_up_on = local_date(signed_up_at, timezone)
    return MonthlyGrant(
        number=number,
        period=month_period(add_months(signed_up_on, number)),
        due_at=months_later(signed_up_at, number, timezone),
    )


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
        """Book every monthly grant of the user that has fallen due and is not booked,
        and return how many that was.

        Runs inside the caller's transaction; the caller holds the lock on `account`.

        The grants of a user are `monthly_grant(user.created_at, number,
        APP_TIMEZONE)` for number 1, 2, 3 and so on. One has fallen due when its
        `due_at` is not after `clock.now()`. Every one that has is owed, also those of
        months in which the user never came by (A.3).

        1. `granted = grants.list_periods(user.id)`.
        2. For each grant that has fallen due, oldest first, whose period is not in
           `granted`: `insert_if_absent(user.id, period, now)`; if a row comes back,
           post `MONTHLY_GRANT` (`vis:issuance` -g, user +g, g = `MONTHLY_GRANT_CR`,
           `LedgerRef.GRANT`, the grant row's id, `monthly_grant_key(user.id,
           period)`) and `set_entry`.

        A repeated call inserts and books nothing. With `MONTHLY_GRANT_CR=0` rows are
        inserted and nothing is posted; the result counts the rows.
        """
        now = self._clock.now()
        granted = frozenset(await uow.grants.list_periods(user.id))
        booked = 0
        for due in self._schedule(user):
            if due.due_at > now:
                break
            if due.period in granted:
                continue
            grant = await uow.grants.insert_if_absent(user.id, due.period, now)
            if grant is None:
                continue
            await self._book(
                uow,
                grant,
                account,
                kind=JournalKind.MONTHLY_GRANT,
                amount_micro=self._grants.monthly_grant_micro,
                idempotency_key=monthly_grant_key(user.id, due.period),
                at=now,
            )
            booked += 1
        return booked

    async def run_monthly(self, actor: AuthenticatedUser) -> MonthlyGrantRunResponse:
        """Book every monthly grant that has fallen due, for every ACTIVE user
        (SPEC §2, A.2).

        1. Read all users in one transaction (`users.list_all()`), keeping those with
           status ACTIVE.
        2. One transaction per user, so no lock is held for long: lock the user's
           account, then `grant_owed_months`. A user whose transaction fails costs
           only that user: the exception is logged with its traceback and the user's
           id, counted, and the run goes on. Nothing is half-booked, because that
           user's transaction rolled back.

           A transaction that did not get through while Postgres is up
           (`database.is_transient`) is such a failure too: a lock that was not
           granted within `POSTGRES_LOCK_TIMEOUT_MS`, because that user's account,
           held by a trade or a settlement, or `vis:issuance` was busy for too long;
           a transaction Postgres ended as the victim of a deadlock or for a
           concurrent update; a pool with no connection free for
           `POSTGRES_POOL_TIMEOUT_SECONDS`. The user is counted as failed, a warning
           says so, and the run goes on with the next one.

           The one failure that is not a user's is Postgres itself: everything else
           `database.is_unavailable` recognises (a lost connection, a server that
           does not serve) is raised again and ends the run with the API's 503,
           instead of being counted once per user. The two are asked in this order,
           because a transient error is one of those `is_unavailable` recognises.
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
                if database.is_transient(error):
                    logger.warning(
                        "Monthly grants of user %s left for the next run, their "
                        "transaction did not get through this time: %r",
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

    def next_grant_at(self, user: User) -> datetime:
        """The instant the next monthly grant of `user` falls due: the `due_at` of
        their first grant that lies after `clock.now()`.

        A grant that has fallen due is booked by the lazy upkeep, at the latest
        `UPKEEP_INTERVAL_SECONDS` after the user's first request from then on, so it
        is never the "next" one.
        """
        now = self._clock.now()
        return next(due.due_at for due in self._schedule(user) if due.due_at > now)

    def _schedule(self, user: User) -> Iterator[MonthlyGrant]:
        """The monthly grants of `user`, the first one first, without an end."""
        return (monthly_grant(user.created_at, number, self._timezone) for number in count(1))

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
