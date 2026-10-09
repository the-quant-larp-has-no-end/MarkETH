"""How credits leave: `coffee_tickets` (SPEC §2, §7, A.6).

A ticket is credit parked in `vis:redemption_pending` until a barista redeems it or it
expires and is refunded. `value_micro` records what was debited at issue, so the redeem
and refund entries move exactly that amount even if `COFFEE_TICKET_VALUE_CR` has changed
since.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import CoffeeTicketStatus
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check


class CoffeeTicket(Base):
    """`redeemed_by` is the barista who scanned the ticket, `user_id` the user who holds it."""

    __tablename__ = "coffee_tickets"
    __table_args__ = (
        sha256_hex_check("code_hash"),
        CheckConstraint("value_micro > 0", name="value_positive"),
        CheckConstraint(
            "(status = 'REDEEMED') = (redeemed_by IS NOT NULL)"
            " AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)",
            name="redeemed_iff_redeemer",
        ),
        Index(None, "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    status: Mapped[CoffeeTicketStatus] = mapped_column(enum_column(CoffeeTicketStatus, "status"))
    value_micro: Mapped[int]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    redeemed_at: Mapped[datetime | None]
    redeemed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
