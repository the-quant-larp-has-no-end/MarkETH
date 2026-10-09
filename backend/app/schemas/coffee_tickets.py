"""Coffee tickets as their owner sees them (SPEC §2, §10, Appendix A.6)."""

from datetime import datetime
from uuid import UUID

from app.core.enums import CoffeeTicketStatus
from app.schemas.common import CreditAmount, ResponseModel


class CoffeeTicketItem(ResponseModel):
    """A ticket in the wallet. It never contains the code."""

    id: UUID
    status: CoffeeTicketStatus
    """The effective status: an ACTIVE ticket past `expires_at` is reported EXPIRED,
    whether or not its refund has been booked yet."""

    value_cr: CreditAmount
    created_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None


class CoffeeTicketIssuedResponse(CoffeeTicketItem):
    """`POST /coffee-tickets`: the new ticket and, this once, its code."""

    code: str
    """Shown in groups of four (`XXXX-XXXX`). It is also the content of the ticket's QR.
    The server keeps only its hash and cannot show it again."""

    balance_cr: CreditAmount
    """The user's balance after the ticket was paid for."""


class CoffeeTicketListResponse(ResponseModel):
    """`GET /me/coffee-tickets`, newest first."""

    items: list[CoffeeTicketItem]
