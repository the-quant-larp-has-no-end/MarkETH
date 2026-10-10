"""What a user is told once: `notices` (SPEC §6, §7, A.5).

A notice is written by the transaction that gives rise to it and stays unseen until
its user's app has shown it. `GET /me` lists the unseen ones; marking one seen sets
`seen_at`, once. Nothing here moves a credit and nothing is ever decided from a
notice: it is a message about something another table already holds.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import NoticeKind
from app.db.base import Base, enum_column


class Notice(Base):
    """`user_id` is who is told, `market_id` the market the notice is about."""

    __tablename__ = "notices"
    __table_args__ = (
        # One market gives one user one notice of a kind: an approval happens once.
        UniqueConstraint("user_id", "kind", "market_id"),
        # `GET /me` asks for a user's unseen notices on every page; keep that index tiny.
        Index(
            "ix_notices_unseen",
            "user_id",
            "created_at",
            postgresql_where=text("seen_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[NoticeKind] = mapped_column(enum_column(NoticeKind, "kind"))
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"))
    created_at: Mapped[datetime]
    seen_at: Mapped[datetime | None]
