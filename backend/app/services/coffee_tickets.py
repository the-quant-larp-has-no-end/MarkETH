"""Coffee tickets: how a voucher becomes a coffee (SPEC §2, Appendix A.6, A.8).

Issuing a ticket moves its value from the user to `vis:redemption_pending`. A barista's
scan moves it on to `vis:redeemed`; if nobody scans it within
`COFFEE_TICKET_EXPIRY_DAYS`, a refund moves it back to the user. The ticket's own
`value_micro` is what every later entry moves, whatever `COFFEE_TICKET_VALUE_CR` says
by then.

Lock order in every method: the ticket owner's account first, then the ticket row.
"""

from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.barista import CoffeeTicketRedeemResponse
from app.schemas.coffee_tickets import CoffeeTicketIssuedResponse, CoffeeTicketListResponse
from app.services import UowFactory
from app.services.ledger import LedgerService


class CoffeeTicketService:
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

    async def issue(self, user: AuthenticatedUser) -> CoffeeTicketIssuedResponse:
        """Turn `COFFEE_TICKET_VALUE_CR` of the user's balance into a ticket.

        One transaction:
        1. Lock the user's account. A balance below the ticket value raises
           `InsufficientBalanceError` (409) with `details={"balance_cr": to_cr(...),
           "required_cr": to_cr(...)}`.
        2. `code = generate_code(COFFEE_TICKET_CODE_LENGTH)`; insert `CoffeeTicket(
           user_id, code_hash=hash_code(code), status=ACTIVE, value_micro, created_at=
           now, expires_at=now + COFFEE_TICKET_EXPIRY_DAYS)`.
        3. Post `COFFEE_TICKET_ISSUE`: user -t, `vis:redemption_pending` +t, with
           `LedgerRef.COFFEE_TICKET`, the ticket's id and `ticket_issue_key(ticket.id)`.

        Returns the ticket, the balance after it, and the code as `format_code(code)`.
        The plain code is never stored and never logged; this response is the only
        place it appears.
        """
        raise NotImplementedError

    async def list_for_user(self, user: AuthenticatedUser) -> CoffeeTicketListResponse:
        """The user's tickets in every status, newest first, without any code.

        `status` is the effective one: an ACTIVE row with `expires_at <= now` is
        reported EXPIRED even before its refund is booked. Reads only.
        """
        raise NotImplementedError

    async def redeem(self, barista: AuthenticatedUser, code: str) -> CoffeeTicketRedeemResponse:
        """A barista scans a ticket: mark it used and move its value to `vis:redeemed`.

        `code` is whatever the scanner read or the barista typed.
        1. `normalize_code(code)`. None, or a length other than
           `COFFEE_TICKET_CODE_LENGTH`: `CodeInvalidError`.
        2. One transaction. Read the ticket by `hash_code(normalized)` without a lock
           to learn its owner; none: `CodeInvalidError`. Lock the owner's account, then
           read the ticket again with `for_update=True`.
        3. Status REDEEMED or EXPIRED: `CodeInvalidError`.
        4. ACTIVE but `expires_at <= now`: refund it exactly as `expire_due` does and
           let the transaction commit; then, after the block, raise `CodeInvalidError`.
        5. Otherwise set `status=REDEEMED`, `redeemed_by=barista.id` and
           `redeemed_at=now` together, and post `COFFEE_TICKET_REDEEM`:
           `vis:redemption_pending` -t, `vis:redeemed` +t, with
           `ticket_redeem_key(ticket.id)`.

        Every failure is the one generic `CodeInvalidError` (400), so a ticket cannot
        be probed (A.6). A second scan of a redeemed ticket fails at step 3.
        """
        raise NotImplementedError

    async def expire_due(self, uow: UnitOfWork, user_id: UUID) -> int:
        """Refund the user's expired tickets and return how many that was (A.3, A.6).

        Runs inside the caller's transaction; the caller holds the lock on the user's
        account. For each row of `coffee_tickets.list_expired_active(user_id, now,
        for_update=True)`: set `status=EXPIRED` and post `COFFEE_TICKET_REFUND`:
        `vis:redemption_pending` -t, user +t, with t the ticket's `value_micro`,
        `LedgerRef.COFFEE_TICKET`, the ticket's id and `ticket_refund_key(ticket.id)`.
        """
        raise NotImplementedError
