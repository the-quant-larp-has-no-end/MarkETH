"""The admin risk page and its alerts (SPEC §5, §10, Appendix A.6)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.enums import AlertSeverity, MarketStatus, MarketTier
from app.schemas.common import (
    CreditAmount,
    Probability,
    ResponseModel,
    ShareAmount,
    SignedCreditAmount,
)


class Obligations(ResponseModel):
    """Outstanding obligations: the most VIS could owe right now (SPEC §5, A.6)."""

    user_balances_cr: CreditAmount
    pending_vouchers_cr: CreditAmount
    market_exposure_cr: SignedCreditAmount
    """Over every market not yet settled: max(q_yes, q_no) minus its account balance."""

    total_cr: SignedCreditAmount
    """The sum of the three components."""

    alert_threshold_cr: CreditAmount
    above_threshold: bool
    """True when `total_cr` exceeds `alert_threshold_cr`. This flag is the alert of
    SPEC §5: the page shows a warning from it, and no `alerts` row is written."""


class RiskMarketRow(ResponseModel):
    """One market that is not yet settled."""

    id: UUID
    question: str
    status: MarketStatus
    """The effective status."""

    tier: MarketTier | None
    b_cr: CreditAmount
    budget_cr: CreditAmount
    """The market's loss budget, b · ln 2 rounded up."""

    p_yes: Probability
    q_yes: ShareAmount
    q_no: ShareAmount
    account_balance_cr: CreditAmount
    """What traders paid in, net of what sells paid out. Never negative: the market's
    loss, at most its budget, shows as `exposure_cr` and is booked at settlement."""

    exposure_cr: SignedCreditAmount
    """max(q_yes, q_no) minus the account balance: what VIS would have to add if the
    more heavily held outcome won. At most `budget_cr`."""

    void_cost_cr: SignedCreditAmount
    """What VIS would have to add if the market were voided now: the net stakes a void
    refunds, minus the account balance. It is what traders who sold above their cost
    have taken out, which a void does not take back. Never below zero while the books
    are right, and bounded by the stake limit per trader, not by `budget_cr`."""

    volume_cr: CreditAmount
    close_at: datetime


class AlertItem(ResponseModel):
    id: int
    severity: AlertSeverity
    message: str
    context: dict[str, Any]
    created_at: datetime
    acked_at: datetime | None


class RiskResponse(ResponseModel):
    """`GET /admin/risk`."""

    open_markets: int
    """Markets whose stored status is OPEN or HALTED."""

    max_open_markets: int
    loss_budget_cr: CreditAmount
    """Sum of the loss budgets of those markets."""

    max_loss_budget_cr: CreditAmount
    obligations: Obligations
    unredeemed_code_value_cr: CreditAmount
    """Value of the codes that could still be redeemed: active and not expired (A.6)."""

    code_issuance_this_month_cr: CreditAmount
    max_code_issuance_per_month_cr: CreditAmount
    markets: list[RiskMarketRow]
    """Soonest `close_at` first."""

    alerts: list[AlertItem]
    """Alerts nobody acknowledged yet, newest first."""
