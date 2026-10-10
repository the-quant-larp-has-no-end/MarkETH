"""Free credits by rule: `grants` (SPEC §2, §7).

A grant is unique per (user, period), which makes it idempotent by construction: the
signup grant has the period `SIGNUP`, a monthly grant its month as `YYYY-MM`.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Grant(Base):
    """A signup or monthly grant. `entry_id` is the journal entry that booked it."""

    __tablename__ = "grants"
    __table_args__ = (
        UniqueConstraint("user_id", "period"),
        CheckConstraint(
            "period = 'SIGNUP' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="period_format"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    period: Mapped[str] = mapped_column(String(7))
    entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime]
