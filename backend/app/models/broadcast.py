"""A market's live video: `broadcasts`.

One row per time somebody asked to go live or went live. A proposer's row starts
REQUESTED and an admin approves or refuses it; an admin's own row starts LIVE. A row
that is REQUESTED, APPROVED or LIVE is open, and a market has at most one open row: a
unique index says so, whatever two requests do at the same moment.

A broadcast that ended by itself (no piece for too long, or the time limit) lets its
broadcaster go live again on the approval it ran on: the row that follows names the
ended one in `resumed_from`.

Nothing of the video is stored here or anywhere else. The app role cannot DELETE from
this table (`app.models.privileges`): a row is the record of what was asked, decided
and ended, and a chat message refers to the broadcast it was written beside.
"""

from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import BroadcastEndReason, BroadcastStatus
from app.db.base import Base, enum_column

OPEN_BROADCAST: Final[str] = "status IN ('REQUESTED', 'APPROVED', 'LIVE')"
"""The condition of the two partial indexes: a broadcast that is asked for, approved or
running."""


class Broadcast(Base):
    __tablename__ = "broadcasts"
    __table_args__ = (
        CheckConstraint("(decided_by IS NULL) = (decided_at IS NULL)", name="decided_together"),
        CheckConstraint(
            "(status = 'REQUESTED') = (decided_at IS NULL)", name="requested_is_undecided"
        ),
        CheckConstraint(
            "(status = 'REFUSED') = (refusal_reason IS NOT NULL)", name="refusal_has_reason"
        ),
        CheckConstraint(
            "status NOT IN ('LIVE', 'ENDED') "
            "OR (started_at IS NOT NULL AND last_chunk_at IS NOT NULL)",
            name="live_has_started",
        ),
        CheckConstraint(
            "(status = 'ENDED') = (ended_at IS NOT NULL) "
            "AND (status = 'ENDED') = (end_reason IS NOT NULL)",
            name="ended_is_complete",
        ),
        # A broadcast that runs on the approval of another one was started.
        CheckConstraint(
            "resumed_from IS NULL OR status IN ('LIVE', 'ENDED')", name="resumed_has_started"
        ),
        # One open broadcast per market.
        Index(
            "uq_broadcasts_market_open",
            "market_id",
            unique=True,
            postgresql_where=text(OPEN_BROADCAST),
        ),
        # The admin's list, and the count of the broadcasts that run.
        Index(
            "ix_broadcasts_open", "status", "requested_at", postgresql_where=text(OPEN_BROADCAST)
        ),
        # The latest broadcast of a market.
        Index(None, "market_id", "requested_at"),
        # What one user asked for on a day.
        Index(None, "broadcaster_id", "requested_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"))
    broadcaster_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[BroadcastStatus] = mapped_column(enum_column(BroadcastStatus, "status"))
    # When the row was created.
    requested_at: Mapped[datetime]
    # The admin who approved or refused, or who started it themselves.
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    # The ended broadcast whose approval this one runs on.
    resumed_from: Mapped[UUID | None] = mapped_column(ForeignKey("broadcasts.id"))
    started_at: Mapped[datetime | None]
    on_air_at: Mapped[datetime | None]
    last_chunk_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    # NULL when it ended by itself.
    ended_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    end_reason: Mapped[BroadcastEndReason | None] = mapped_column(
        enum_column(BroadcastEndReason, "end_reason")
    )
