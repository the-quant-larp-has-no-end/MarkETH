"""What a signed-in user sees of themselves: `/me` and its lists (SPEC §10, Appendix A.2)."""

from datetime import date, datetime
from uuid import UUID

from app.core.enums import JournalKind, MarketStatus, MarketTier, Outcome, TradeSide, UserRole
from app.schemas.common import (
    CreditAmount,
    CursorPage,
    Probability,
    ResponseModel,
    ShareAmount,
    SignedCreditAmount,
)
from app.schemas.markets import Holding, MarketSummary


class MeResponse(ResponseModel):
    """`GET /me`: profile, balance and the next monthly grant."""

    id: UUID
    email: str
    role: UserRole
    created_at: datetime
    balance_cr: CreditAmount
    monthly_grant_cr: CreditAmount
    next_monthly_grant_on: date
    """First day of the next calendar month in the app's timezone (SPEC §2)."""


class PortfolioEntry(ResponseModel):
    """A user's position in one market (SPEC §11, Portfolio)."""

    market: MarketSummary
    holdings: list[Holding]
    """Always two entries, YES then NO."""

    value_cr: CreditAmount
    """Sum of the holdings' values."""

    cost_cr: CreditAmount
    proceeds_cr: CreditAmount
    net_stake_cr: CreditAmount
    payout_cr: CreditAmount | None
    """What the market paid this user when it was RESOLVED; null in any other status."""

    refund_cr: CreditAmount | None
    """What the user got back when the market was VOID; null in any other status."""


class PortfolioResponse(ResponseModel):
    """`GET /me/positions`: unsettled markets first (soonest `close_at` first), then
    settled ones (latest `close_at` first)."""

    items: list[PortfolioEntry]
    total_value_cr: CreditAmount
    """Sum of `value_cr` over the markets that are not yet RESOLVED or VOID."""


class TradeItem(ResponseModel):
    """One of the user's trades."""

    id: UUID
    market_id: UUID
    market_question: str
    side: TradeSide
    outcome: Outcome
    shares: ShareAmount
    amount_cr: CreditAmount
    """The cost of a buy or the proceeds of a sell."""

    average_price: Probability
    p_yes_before: Probability
    p_yes_after: Probability
    market_version: int
    created_at: datetime


class TradeListResponse(CursorPage[TradeItem]):
    """`GET /me/trades`, newest first."""


class LedgerItem(ResponseModel):
    """One movement on the user's balance (SPEC §11, Wallet)."""

    id: int
    kind: JournalKind
    amount_cr: SignedCreditAmount
    """Positive when the balance grew, negative when it shrank."""

    description: str
    """A short English label for the line, e.g. "Monthly voucher"."""

    created_at: datetime


class LedgerListResponse(CursorPage[LedgerItem]):
    """`GET /me/ledger`, newest first."""


class ProposalItem(ResponseModel):
    """A market the user proposed, in whatever status it is now."""

    id: UUID
    question: str
    yes_definition: str
    no_definition: str
    resolution_criteria: str
    resolution_source: str
    status: MarketStatus
    """The effective status, as in `MarketSummary`."""

    requested_tier: MarketTier
    close_at: datetime
    resolve_by: datetime
    created_at: datetime
    rejection_reason: str | None


class ProposalListResponse(ResponseModel):
    """`GET /me/proposals`, newest first."""

    items: list[ProposalItem]
