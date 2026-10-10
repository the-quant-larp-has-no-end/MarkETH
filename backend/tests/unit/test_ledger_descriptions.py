"""What a line of a user's ledger is called (SPEC §11 "Wallet", Appendix A.8).

The label is server text: the wallet shows `LedgerItem.description` as it arrives.
"""

import pytest

from app.core.enums import JournalKind
from app.services.account import LEDGER_DESCRIPTIONS, ledger_description


def test_every_way_credits_can_move_has_a_label() -> None:
    assert set(LEDGER_DESCRIPTIONS) == set(JournalKind)
    assert all(LEDGER_DESCRIPTIONS.values())


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (JournalKind.SIGNUP_GRANT, "Welcome tokens"),
        (JournalKind.MONTHLY_GRANT, "Monthly tokens"),
        (JournalKind.CODE_REDEMPTION, "Code redeemed"),
        (JournalKind.TRADE_BUY, "Bought shares"),
        (JournalKind.TRADE_SELL, "Sold shares"),
        (JournalKind.MARKET_RESOLUTION, "Market payout"),
        (JournalKind.MARKET_VOID, "Market voided, stake refunded"),
        (JournalKind.ADMIN_ADJUSTMENT, "Adjustment by VIS"),
    ],
)
def test_a_line_is_called_by_the_kind_of_its_entry(kind: JournalKind, label: str) -> None:
    assert ledger_description(kind) == label


@pytest.mark.parametrize(
    ("kind", "product_name", "label"),
    [
        (JournalKind.VOUCHER_ISSUE, "Mate", "Voucher: Mate"),
        (JournalKind.VOUCHER_ISSUE, "Iced Tea", "Voucher: Iced Tea"),
        (JournalKind.VOUCHER_ISSUE, "M&Ms", "Voucher: M&Ms"),
        (JournalKind.VOUCHER_REFUND, "Mate", "Voucher expired, refunded: Mate"),
        (JournalKind.VOUCHER_REDEEM, "Toblerone", "Voucher redeemed: Toblerone"),
    ],
)
def test_a_line_about_a_voucher_says_what_the_voucher_is_for(
    kind: JournalKind, product_name: str, label: str
) -> None:
    assert ledger_description(kind, product_name) == label


def test_the_unit_is_called_a_token_and_never_a_credit() -> None:
    """On screen a credit is a token, and the labels are shown as they are."""
    for label in LEDGER_DESCRIPTIONS.values():
        assert "credit" not in label.lower()
        assert "CR" not in label
