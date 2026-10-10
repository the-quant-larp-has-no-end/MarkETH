"""What users hold and did: `positions`, `trades`, `idempotency_keys` (SPEC §4, §7, §8).

A user's net stake in a market is `cost_micro - proceeds_micro` summed over both of
their positions there. Trades are the immutable history behind those running totals.
"""

from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Outcome, TradeSide
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check

IDEMPOTENCY_KEY_MAX_LENGTH: Final[int] = 64
"""Room for the UUID a client sends as `Idempotency-Key` (SPEC §8), in any text form."""


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint("shares_micro >= 0", name="shares_non_negative"),
        CheckConstraint("cost_micro >= 0", name="cost_non_negative"),
        CheckConstraint("proceeds_micro >= 0", name="proceeds_non_negative"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"), primary_key=True, index=True)
    outcome: Mapped[Outcome] = mapped_column(enum_column(Outcome, "outcome"), primary_key=True)
    shares_micro: Mapped[int] = mapped_column(default=0)
    cost_micro: Mapped[int] = mapped_column(default=0)
    proceeds_micro: Mapped[int] = mapped_column(default=0)


class Trade(Base):
    """One executed buy or sell. `amount_micro` is the cost of a buy or the proceeds of a sell.

    The proceeds of a sell can be 0 (A.4), the cost of a buy cannot. `market_version` is
    the market's version after the trade: every trade of a market has its own, because
    each one runs under the market lock (SPEC §8) and the chart is keyed by it (SPEC §9).
    """

    __tablename__ = "trades"
    __table_args__ = (
        CheckConstraint("shares_micro > 0", name="shares_positive"),
        CheckConstraint("amount_micro >= 0", name="amount_non_negative"),
        UniqueConstraint("market_id", "market_version"),
        Index(None, "market_id", "created_at"),
        Index(None, "user_id", "id"),
        # A user's trade instants after a day: what a streak counts.
        Index(None, "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    side: Mapped[TradeSide] = mapped_column(enum_column(TradeSide, "side"))
    outcome: Mapped[Outcome] = mapped_column(enum_column(Outcome, "outcome"))
    shares_micro: Mapped[int]
    amount_micro: Mapped[int]
    p_yes_before_micro: Mapped[int]
    p_yes_after_micro: Mapped[int]
    market_version: Mapped[int]
    created_at: Mapped[datetime]


class IdempotencyKey(Base):
    """A client's claim on one request. `response_json` is NULL until the request commits.

    `request_hash` is the SHA-256 of the request (A.4), never the request itself.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (sha256_hex_check("request_hash"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(IDEMPOTENCY_KEY_MAX_LENGTH), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH))
    response_json: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime]
