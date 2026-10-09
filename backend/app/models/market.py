"""Markets and their one resolution: `markets`, `resolutions` (SPEC §4, §6, §7, A.7).

The market row is the lock every trade takes first (SPEC §8). It carries the LMSR state
(`b_micro`, `q_yes_micro`, `q_no_micro`) and a `version` that grows by one per trade.

SPEC §5: budget and b are immutable while a market is OPEN. A trigger (in the
migrations) rejects such an UPDATE; changing them means halting the market first.
"""

from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import MarketStatus, MarketTier, ResolutionOutcome
from app.db.base import Base, enum_column

LN_2_ROUNDED_DOWN: Final[str] = "0.693147180559945309417232121458"
"""ln 2 cut off after 30 places, for the CHECK below. Cut off, not rounded, so a budget
that covers b · ln 2 exactly is never refused."""


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (
        CheckConstraint("budget_micro >= 0", name="budget_non_negative"),
        CheckConstraint("b_micro >= 0", name="b_non_negative"),
        # A.7: budget and b stay 0 until approval; a market that can trade needs b > 0.
        CheckConstraint(
            "status IN ('PROPOSED', 'REJECTED') OR b_micro > 0", name="b_positive_once_approved"
        ),
        # SPEC §5: b · ln 2 <= budget. With the check above, an approved market has a budget.
        CheckConstraint(
            f"b_micro * {LN_2_ROUNDED_DOWN} <= budget_micro", name="loss_bound_within_budget"
        ),
        CheckConstraint("q_yes_micro >= 0", name="q_yes_non_negative"),
        CheckConstraint("q_no_micro >= 0", name="q_no_non_negative"),
        CheckConstraint("version >= 0", name="version_non_negative"),
        CheckConstraint("volume_micro >= 0", name="volume_non_negative"),
        Index(None, "status", "close_at"),
        Index(None, "created_by", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    question: Mapped[str] = mapped_column(Text)
    yes_def: Mapped[str] = mapped_column(Text)
    no_def: Mapped[str] = mapped_column(Text)
    resolution_criteria: Mapped[str] = mapped_column(Text)
    resolution_source: Mapped[str] = mapped_column(Text)
    status: Mapped[MarketStatus] = mapped_column(enum_column(MarketStatus, "status"))
    requested_tier: Mapped[MarketTier] = mapped_column(enum_column(MarketTier, "requested_tier"))
    tier: Mapped[MarketTier | None] = mapped_column(enum_column(MarketTier, "tier"))
    budget_micro: Mapped[int] = mapped_column(default=0)
    b_micro: Mapped[int] = mapped_column(default=0)
    q_yes_micro: Mapped[int] = mapped_column(default=0)
    q_no_micro: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=0)
    volume_micro: Mapped[int] = mapped_column(default=0)
    close_at: Mapped[datetime]
    resolve_by: Mapped[datetime]
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime]
    opened_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]


class Resolution(Base):
    """How a market ended. The unique `market_id` makes a second resolution impossible."""

    __tablename__ = "resolutions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"), unique=True)
    outcome: Mapped[ResolutionOutcome] = mapped_column(enum_column(ResolutionOutcome, "outcome"))
    evidence: Mapped[str] = mapped_column(Text)
    resolved_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]
