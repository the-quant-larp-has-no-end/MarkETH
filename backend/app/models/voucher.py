"""How credits enter: `voucher_grants`, `voucher_code_batches`, `voucher_codes` (SPEC §2, §7).

Each source is idempotent by construction. A grant is unique per (user, period); a code
is redeemed under a row lock and its `redeemed_by` is set once. A code is worth its
batch's `value_micro`, and only its SHA-256 hash is stored.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import VoucherCodeStatus
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check


class VoucherGrant(Base):
    """A signup or monthly grant. `entry_id` is the journal entry that booked it."""

    __tablename__ = "voucher_grants"
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


class VoucherCodeBatch(Base):
    __tablename__ = "voucher_code_batches"
    __table_args__ = (
        CheckConstraint("value_micro > 0", name="value_positive"),
        CheckConstraint("count > 0", name="count_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    label: Mapped[str] = mapped_column(Text)
    value_micro: Mapped[int]
    count: Mapped[int]
    expires_at: Mapped[datetime]
    one_per_user: Mapped[bool]
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]


class VoucherCode(Base):
    __tablename__ = "voucher_codes"
    __table_args__ = (
        sha256_hex_check("code_hash"),
        CheckConstraint(
            "(status = 'REDEEMED') = (redeemed_by IS NOT NULL)"
            " AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)",
            name="redeemed_iff_redeemer",
        ),
        Index(None, "batch_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("voucher_code_batches.id"))
    code_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    status: Mapped[VoucherCodeStatus] = mapped_column(enum_column(VoucherCodeStatus, "status"))
    redeemed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    redeemed_at: Mapped[datetime | None]
    entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("journal_entries.id"))
