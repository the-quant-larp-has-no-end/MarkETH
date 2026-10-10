"""How credits leave: `vouchers` (SPEC §2, §7, A.6).

A voucher is credit parked in `vis:redemption_pending` until a barista redeems it or it
expires and is refunded. It is for one product of `VOUCHER_PRODUCTS`, and the row keeps
what that product was when the voucher was issued: `product_id`, `product_name` and
`value_micro`, its price. The redeem and refund entries move exactly `value_micro`, and
every screen names `product_name`, whatever `.env` says about the product by then.

`code_hash` is the hash of the one code that redeems the voucher. Its owner can have
that code replaced: `code_hash` is the one column that changes while a voucher stays
ACTIVE.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import PRODUCT_ID_MAX_LENGTH, PRODUCT_NAME_MAX_LENGTH
from app.core.enums import VoucherStatus
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check


class Voucher(Base):
    """`redeemed_by` is the barista who scanned the voucher, `user_id` the user who holds it."""

    __tablename__ = "vouchers"
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
    product_id: Mapped[str] = mapped_column(String(PRODUCT_ID_MAX_LENGTH))
    product_name: Mapped[str] = mapped_column(String(PRODUCT_NAME_MAX_LENGTH))
    code_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    status: Mapped[VoucherStatus] = mapped_column(enum_column(VoucherStatus, "status"))
    value_micro: Mapped[int]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    redeemed_at: Mapped[datetime | None]
    redeemed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
