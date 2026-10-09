"""What a barista gets back from scanning a coffee ticket (SPEC §2, §10)."""

from datetime import datetime
from uuid import UUID

from app.schemas.common import CreditAmount, ResponseModel


class CoffeeTicketRedeemResponse(ResponseModel):
    """`POST /barista/coffee-tickets/{code}/redeem`: the ticket is now used up."""

    ticket_id: UUID
    value_cr: CreditAmount
    redeemed_at: datetime
