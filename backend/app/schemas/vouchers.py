"""Vouchers and the products they are for, as their owner sees them (SPEC §2, §10,
Appendix A.6)."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.config import PRODUCT_ID_MAX_LENGTH
from app.core.enums import VoucherStatus
from app.schemas.common import CreditAmount, RequestModel, ResponseModel


class VoucherProductItem(ResponseModel):
    """Something a voucher can be for, and whether the caller can have one now."""

    id: str
    """What `POST /vouchers` takes as `product_id`."""

    name: str
    """Shown as it is."""

    price_cr: CreditAmount
    """What a voucher for it takes from the balance."""

    affordable: bool
    """Whether the caller's balance covers `price_cr` right now. The wallet offers the
    product from this and compares nothing itself."""


class VoucherProductListResponse(ResponseModel):
    """`GET /vouchers/products`: every product, in the order to offer them."""

    items: list[VoucherProductItem]


class IssueVoucherRequest(RequestModel):
    """`POST /vouchers`: which product the voucher is for."""

    product_id: str = Field(min_length=1, max_length=PRODUCT_ID_MAX_LENGTH)
    """The `id` of a product of `GET /vouchers/products`."""


class VoucherItem(ResponseModel):
    """A voucher in the wallet. It never contains the code."""

    id: UUID
    product_id: str
    product_name: str
    """What the voucher is for, as the product was named when the voucher was issued."""

    status: VoucherStatus
    """The effective status: an ACTIVE voucher past `expires_at` is reported EXPIRED,
    whether or not its refund has been booked yet."""

    refunded: bool
    """Whether the refund of an EXPIRED voucher has been booked: its value is back on
    the balance. False while that refund is still to come, and for a voucher in any
    other status."""

    value_cr: CreditAmount
    """What the voucher took from the balance: the product's price when it was issued."""

    created_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None


class VoucherIssuedResponse(VoucherItem):
    """`POST /vouchers`: the voucher that was issued, and its code."""

    code: str
    """Shown in groups of four (`XXXX-XXXX`). It is also the content of the voucher's QR.
    The server keeps only its hash; `POST /vouchers/{voucher_id}/code` gives the
    voucher another one."""

    balance_cr: CreditAmount
    """The user's balance after the voucher was paid for."""


class VoucherCodeResponse(ResponseModel):
    """`POST /vouchers/{voucher_id}/code`: the code the voucher has from now on. The one
    it had before is void."""

    voucher_id: UUID
    product_name: str
    """What the voucher is for, as the product was named when the voucher was issued."""

    code: str
    """Shown in groups of four (`XXXX-XXXX`), and the content of the voucher's QR. The
    server keeps only its hash, so this answer is the one place it appears."""

    expires_at: datetime
    """Until when the voucher can be redeemed. A fresh code does not move it."""


class VoucherListResponse(ResponseModel):
    """`GET /me/vouchers`, newest first."""

    items: list[VoucherItem]
