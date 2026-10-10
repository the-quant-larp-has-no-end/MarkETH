"""Free credits by code: `code_batches`, `codes` (SPEC §2, §7).

A code is redeemed under a row lock and its `redeemed_by` is set once, so it is
idempotent by construction. A code is worth its batch's `value_micro`.

A code is found by its SHA-256 hash. While it is ACTIVE the row also holds the code
itself, so that an admin can print its card again; the statement that redeems or revokes
a code clears it, and a CHECK refuses a row for which the two disagree.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import CODE_MAX_LENGTH
from app.core.enums import CodeStatus
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check


class CodeBatch(Base):
    __tablename__ = "code_batches"
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


class Code(Base):
    __tablename__ = "codes"
    __table_args__ = (
        sha256_hex_check("code_hash"),
        CheckConstraint(
            "(status = 'REDEEMED') = (redeemed_by IS NOT NULL)"
            " AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)",
            name="redeemed_iff_redeemer",
        ),
        # The code is kept exactly as long as it can still be redeemed.
        CheckConstraint("(status = 'ACTIVE') = (code IS NOT NULL)", name="code_iff_active"),
        Index(None, "batch_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("code_batches.id"))
    code_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    code: Mapped[str | None] = mapped_column(String(CODE_MAX_LENGTH))
    """The code as `normalize_code` writes it, without dashes; NULL once the code is
    redeemed or revoked."""

    status: Mapped[CodeStatus] = mapped_column(enum_column(CodeStatus, "status"))
    redeemed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    redeemed_at: Mapped[datetime | None]
    entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("journal_entries.id"))
