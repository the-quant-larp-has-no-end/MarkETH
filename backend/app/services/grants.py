"""Free vouchers: the signup grant and the monthly grants (SPEC §2, Appendix A.3, A.8).

Both are booked from `vis:issuance` to the user. What makes each grant happen once is
its `voucher_grants` row: unique per (user, period), inserted with ON CONFLICT DO
NOTHING. Only the caller that gets a row back books the credit.

Monthly grants need no cron job: they are booked lazily when a user's session is loaded
(`UserUpkeepService`), and an admin can book everything owed at once.
"""

from datetime import date

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.models.ledger import Account
from app.models.user import User
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.grants import MonthlyGrantRunResponse
from app.services import UowFactory
from app.services.audit import AuditService
from app.services.ledger import LedgerService


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
        self._vouchers = settings.vouchers
        self._timezone = settings.app.timezone
        self._clock = clock
        self._ledger = ledger
        self._audit = audit

    async def grant_signup(self, uow: UnitOfWork, user: User, account: Account) -> bool:
        """Book the signup grant once. True if this call booked it, False if it existed.

        Runs inside the caller's transaction. The caller holds the lock on `account`,
        the user's own account (`get_user_account(user.id, for_update=True)`).

        1. `voucher_grants.insert_if_absent(user.id, SIGNUP_GRANT_PERIOD, now)`. None
           means the grant exists: return False.
        2. Post `SIGNUP_GRANT`: `vis:issuance` -g, user +g, with g = `SIGNUP_GRANT_CR`,
           `ref_type=LedgerRef.VOUCHER_GRANT`, `ref_id` the grant row's id and
           `idempotency_key=signup_grant_key(user.id)`.
        3. `voucher_grants.set_entry(grant.id, entry.id)`; return True.

        With `SIGNUP_GRANT_CR=0` the row is still inserted, nothing is posted, and the
        result is True.
        """
        raise NotImplementedError

    async def grant_owed_months(self, uow: UnitOfWork, user: User, account: Account) -> int:
        """Book every monthly grant the user is owed and return how many that was.

        Runs inside the caller's transaction; the caller holds the lock on `account`.

        A user is owed one grant for every calendar month in `APP_TIMEZONE` after the
        month of `user.created_at`, up to and including the month of `clock.now()`
        (A.3: from the month after signup, including months missed in between).

        1. Compute those periods with `local_date` and `month_period` and drop the ones
           in `voucher_grants.list_periods(user.id)`.
        2. For each remaining period, oldest first: `insert_if_absent(user.id, period,
           now)`; if a row comes back, post `MONTHLY_GRANT` (`vis:issuance` -g, user
           +g, g = `MONTHLY_GRANT_CR`, `LedgerRef.VOUCHER_GRANT`, the grant row's id,
           `monthly_grant_key(user.id, period)`) and `set_entry`.

        A repeated call inserts and books nothing. With `MONTHLY_GRANT_CR=0` rows are
        inserted and nothing is posted.
        """
        raise NotImplementedError

    async def run_monthly(self, actor: AuthenticatedUser) -> MonthlyGrantRunResponse:
        """Book every owed monthly grant of every ACTIVE user (SPEC §2, A.2).

        1. Read the ids of all users in one transaction (`users.list_all()`), keeping
           those with status ACTIVE.
        2. One transaction per user, so no lock is held for long: lock the user's
           account, then `grant_owed_months`. A user whose transaction fails costs
           only that user: the exception is logged with its traceback and the user's
           id, counted, and the run goes on. Nothing is half-booked, because that
           user's transaction rolled back.
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
        raise NotImplementedError

    def next_grant_date(self) -> date:
        """When the next monthly grant falls due: the first day of the month after
        `clock.now()` in `APP_TIMEZONE` (`first_of_next_month(local_date(now, tz))`).

        The current month's grant is booked by the lazy upkeep at the latest one
        session-cache TTL into the month, so it is never the "next" one.
        """
        raise NotImplementedError
