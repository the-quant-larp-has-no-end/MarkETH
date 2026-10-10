"""Lazy upkeep: what a cron job would do, done when a user shows up (SPEC §2, A.3).

A user's owed monthly grants are booked and their expired vouchers refunded in one short
transaction, started by a request of that user. `IdentityService` asks for it on every
request of a registered caller; this service runs it at most once per
`UPKEEP_INTERVAL_SECONDS` per user, so every other request pays one dictionary lookup.
A run that Postgres or the connection pool did not let through does not count: the
user's next request starts it again.
"""

import logging
from datetime import datetime
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import UserStatus
from app.db import database
from app.models.user import User
from app.services import UowFactory
from app.services.grants import GrantService
from app.services.vouchers import VoucherService

logger = logging.getLogger(__name__)


class UserUpkeepService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        grants: GrantService,
        vouchers: VoucherService,
    ) -> None:
        self._uow_factory = uow_factory
        self._interval = settings.identity.upkeep_interval
        self._clock = clock
        self._grants = grants
        self._vouchers = vouchers
        # When upkeep was last started for a user, in this process.
        self._last_attempt: dict[UUID, datetime] = {}

    async def run_if_due(self, user: User) -> None:
        """Run `user`'s upkeep unless this process started it less than
        `UPKEEP_INTERVAL_SECONDS` ago.

        1. `now = clock.now()`. If `_last_attempt` holds an instant for `user.id` and
           `now - that < UPKEEP_INTERVAL_SECONDS`: return. No I/O.
        2. Store `now` for `user.id` before anything is awaited, so two requests of one
           user that arrive together cannot both pass step 1.
        3. `await self.run(user)`, inline: the request that starts the upkeep sees its
           result, for example a monthly grant that has just fallen due.
        4. If `run` raises, the first of these that applies:
           * `database.is_transient(error)`: the run did not get through and Postgres
             is up. Another transaction held a lock the upkeep needs, the user's
             account or an account of VIS, for longer than
             `POSTGRES_LOCK_TIMEOUT_MS`; or Postgres ended the run as the victim of a
             deadlock or for a concurrent update; or the pool had no connection free
             for `POSTGRES_POOL_TIMEOUT_SECONDS`. Nothing was written. Log a warning
             with the user's id and swallow it: the request goes on without its
             upkeep. Take the instant of step 2 back
             (`_last_attempt.pop(user.id, None)`), so the user's next request tries
             again instead of waiting out an interval. What was owed is still owed.
           * `database.is_unavailable(error)`: a lost connection, or a Postgres that
             does not serve. Raise it again; it becomes the API's 503. Ask in this
             order: a transient error is one of those `is_unavailable` recognises.
             The instant of step 2 stays: a Postgres in trouble is not asked again
             by every request, and the next attempt is one interval away.
           * Anything else: log it with its traceback and the user's id and swallow
             it. A request must not fail because of upkeep. The instant of step 2
             stays, so the next attempt is one interval away.

        A request that is cancelled while its run is under way is none of these: the
        cancellation is not caught, the transaction of the run is rolled back, and the
        instant of step 2 stays.

        The map lives in this process: it holds one instant per user who made a
        request, is never pruned, and is empty after a restart, when every user is due
        once. With several API processes each keeps its own. More runs change nothing,
        because `run` writes only what is owed.
        """
        now = self._clock.now()
        last_attempt = self._last_attempt.get(user.id)
        if last_attempt is not None and now - last_attempt < self._interval:
            return
        self._last_attempt[user.id] = now
        try:
            await self.run(user)
        except Exception as error:
            if database.is_transient(error):
                self._last_attempt.pop(user.id, None)
                logger.warning(
                    "Upkeep of user %s skipped, it did not get through this time: %r",
                    user.id,
                    error,
                )
            elif database.is_unavailable(error):
                raise
            else:
                logger.exception("Upkeep of user %s failed", user.id)

    async def run(self, user: User) -> None:
        """Bring one user's grants and vouchers up to date, in one transaction.

        1. Lock the user's account (`accounts.get_user_account(user.id,
           for_update=True)`): it is the first lock, before any voucher row. Every
           user has an account, because the profile transaction creates the two
           together. A user without one is a broken database, not a state to serve:
           `LookupError`, which `run_if_due` logs like any other failure.
        2. If `user.status` is ACTIVE: `GrantService.grant_owed_months(uow, user,
           account)`.
        3. For every status: `VoucherService.expire_due(uow, account)`.

        `user` is the row as the caller read it; its status is not read again here.

        Idempotent: a second run right after the first writes nothing. Two runs for
        the same user at once wait for each other on the account lock.

        Nothing is caught here: whatever a step raises rolls the transaction back and
        leaves this method. `run_if_due` decides what a failure means to a request.
        """
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_user_account(user.id, for_update=True)
            if account is None:
                raise LookupError(f"user {user.id} has no ledger account")
            if user.status is UserStatus.ACTIVE:
                await self._grants.grant_owed_months(uow, user, account)
            await self._vouchers.expire_due(uow, account)
