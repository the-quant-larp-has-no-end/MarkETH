"""A player's run of days with a trade: `streaks`.

One row per user who has a counted day. A day counts when the user makes at least one
trade on it, on the calendar of `APP_TIMEZONE`. The row says how long the run is that
ends on `last_day`; whether that run still counts today is worked out from the clock
when it is read, never stored.

No credit is decided from this table. What a counted day books is a journal entry with
a key of its own (`streak_reward_key`, `streak_bonus_key`), so a row that were lost or
set back could not book a day twice: the journal's unique key refuses it.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Streak(Base):
    __tablename__ = "streaks"
    __table_args__ = (
        CheckConstraint("current_days >= 1", name="current_days_positive"),
        CheckConstraint("longest_days >= current_days", name="longest_not_below_current"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # The length of the run of days in a row that ends on `last_day`.
    current_days: Mapped[int]
    # The largest `current_days` the row ever held.
    longest_days: Mapped[int]
    # The latest counted day, on the calendar of APP_TIMEZONE.
    last_day: Mapped[date]
    # The instant of the trade that last counted a day.
    updated_at: Mapped[datetime]
