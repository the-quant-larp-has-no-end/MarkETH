"""Lazy upkeep: what a cron job would do, done when a user shows up (SPEC §2, A.3).

Whenever a session is loaded from Postgres (a cache miss, so at most once per
`SESSION_CACHE_TTL_SECONDS` per session), the user's owed monthly grants are booked and
their expired coffee tickets refunded, in one short transaction.
"""

from uuid import UUID

from app.services import UowFactory
from app.services.coffee_tickets import CoffeeTicketService
from app.services.grants import GrantService


class UserUpkeepService:
    def __init__(
        self,
        uow_factory: UowFactory,
        grants: GrantService,
        coffee_tickets: CoffeeTicketService,
    ) -> None:
        self._uow_factory = uow_factory
        self._grants = grants
        self._coffee_tickets = coffee_tickets

    async def run(self, user_id: UUID) -> None:
        """Bring one user's vouchers up to date, in one transaction.

        1. Load the user. Unknown: return.
        2. Lock the user's account (`get_user_account(user_id, for_update=True)`): it
           is the first lock, before any ticket row. No account: return.
        3. If the user is ACTIVE: `GrantService.grant_owed_months(uow, user, account)`.
        4. For every status: `CoffeeTicketService.expire_due(uow, user_id)`.

        Idempotent: a second run right after the first writes nothing. Two runs for
        the same user at once wait for each other on the account lock.
        """
        raise NotImplementedError
