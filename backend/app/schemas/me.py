"""What a user sees of themselves: `/me` and its lists (SPEC §10, Appendix A.2)."""

from datetime import datetime
from uuid import UUID

from app.core.enums import (
    DegreeLevel,
    JournalKind,
    MarketStatus,
    NoticeKind,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
)
from app.schemas.common import (
    CreditAmount,
    CursorPage,
    Probability,
    ResponseModel,
    ShareAmount,
    SignedCreditAmount,
)
from app.schemas.markets import Holding, MarketSummary


class NoticeItem(ResponseModel):
    """Something the user is told once (SPEC §6): the app shows it and marks it seen
    with `POST /me/notices/{id}/seen`."""

    id: UUID
    kind: NoticeKind
    """What happened. `PROPOSAL_APPROVED`: an admin approved the market the user
    proposed, and it is open."""

    market_id: UUID
    """The market the notice is about."""

    question: str
    """That market's question, as it reads now."""

    created_at: datetime
    """When it happened."""


class MeResponse(ResponseModel):
    """`GET /me`: profile, balance, the next monthly grant and what the user has not
    been told yet."""

    id: UUID
    email: str
    """The address the portal identifies the user by."""

    name: str
    """The display name the portal sent when the profile was last saved. May be empty."""

    username: str
    degree_level: DegreeLevel
    degree_programme: str
    """A label of `degree_level`, as `GET /config` lists it."""

    role: UserRole
    """What the user may do right now: the role an admin gave them, USER for
    everybody else, and ADMIN for an address the server names as an admin."""

    created_at: datetime
    policy_accepted_at: datetime
    """When the user accepted the usage policy: the instant the profile was
    created."""

    balance_cr: CreditAmount
    can_get_voucher: bool
    """Whether the balance covers a voucher for the cheapest product right now
    (SPEC §2). The wallet enables getting one from this."""

    monthly_grant_cr: CreditAmount
    next_monthly_grant_at: datetime
    """The instant the next monthly grant falls due (SPEC §2). The grants follow the
    sign-up: each month on the day of the month and at the time of day of
    `created_at` in the app's timezone, on the last day of a month that has no
    such day."""

    notices: list[NoticeItem]
    """The user's notices that are not marked seen, oldest first. Empty when there is
    nothing to tell."""


class PortfolioEntry(ResponseModel):
    """A user's position in one market (SPEC §11, Portfolio)."""

    market: MarketSummary
    holdings: list[Holding]
    """Always two entries, YES then NO."""

    entered_at: datetime
    """When the user entered the market: the instant of their first trade there. It
    stays that instant whatever they bought or sold since."""

    value_cr: CreditAmount
    """Sum of the holdings' values."""

    cost_cr: CreditAmount
    proceeds_cr: CreditAmount
    net_stake_cr: CreditAmount
    payout_cr: CreditAmount | None
    """What the market paid this user when it was RESOLVED; null in any other status."""

    refund_cr: CreditAmount | None
    """What the user got back when the market was VOID; null in any other status."""


class EquityPoint(ResponseModel):
    """A historical total equity, with an approximate coordinate used only for plotting."""

    t: datetime
    total_equity_cr: CreditAmount
    plot_value: float
    """Chart coordinate in tokens. Labels and all arithmetic use the exact amount."""


class PortfolioResponse(ResponseModel):
    """`GET /me/positions`: unsettled markets first (soonest `close_at` first), then
    settled ones (latest `close_at` first)."""

    items: list[PortfolioEntry]
    total_value_cr: CreditAmount
    """Sum of `value_cr` over the markets that are not yet RESOLVED or VOID."""

    balance_cr: CreditAmount
    """Remaining tokens in the user's ledger account."""

    total_equity_cr: CreditAmount
    """`total_value_cr` plus `balance_cr`; settled holdings are excluded."""

    equity_history: list[EquityPoint]
    """Oldest first, bounded by HISTORY_MAX_POINTS. Includes all equity-changing events."""

    until: datetime
    """The server's now, the right edge of the equity chart."""


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
    """A short English label for the line, e.g. "Monthly tokens"."""

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
    """The effective status, as in `MarketSummary`. EXPIRED for a proposal whose
    `close_at` passed before an admin decided on it: it is out of review."""

    outcome: ResolutionOutcome | None
    """YES or NO once RESOLVED, VOID once VOID, otherwise null."""

    close_at: datetime
    resolve_by: datetime
    created_at: datetime
    rejection_reason: str | None


class ProposalListResponse(ResponseModel):
    """`GET /me/proposals`, newest first."""

    items: list[ProposalItem]
