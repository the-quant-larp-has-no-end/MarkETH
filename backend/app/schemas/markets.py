"""Markets as the API shows them: list, detail and proposal (SPEC §6, §10, A.4, A.5, A.11).

The frontend computes nothing (golden rule 6), so a market carries every number its
screens show: display probabilities, the effective status, and for a signed-in viewer
their holdings, their stake, which buy buttons would be accepted and how many shares
each sell button stands for.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from app.core.enums import MarketStatus, MarketTier, Outcome, ResolutionOutcome
from app.schemas.common import (
    CreditAmount,
    Probability,
    RequestModel,
    ResponseModel,
    ShareAmount,
)

QUESTION_MIN_LENGTH: Final[int] = 10
QUESTION_MAX_LENGTH: Final[int] = 200
SHORT_TEXT_MAX_LENGTH: Final[int] = 500
LONG_TEXT_MAX_LENGTH: Final[int] = 2000

QuestionText = Annotated[str, Field(min_length=QUESTION_MIN_LENGTH, max_length=QUESTION_MAX_LENGTH)]
ShortText = Annotated[str, Field(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH)]
"""An outcome definition, a source, a reason."""

LongText = Annotated[str, Field(min_length=1, max_length=LONG_TEXT_MAX_LENGTH)]
"""Resolution criteria, resolution evidence."""


class ProposeMarketRequest(RequestModel):
    """`POST /markets`: what a proposal must state (SPEC §6).

    The time rules that depend on the clock are the service's: `close_at` in the future
    and within `MAX_MARKET_LIFETIME`, `resolve_by` within `MAX_RESOLUTION_DELAY_DAYS`
    of `close_at`.
    """

    question: QuestionText
    yes_definition: ShortText
    no_definition: ShortText
    resolution_criteria: LongText
    resolution_source: ShortText
    close_at: AwareDatetime
    resolve_by: AwareDatetime
    requested_tier: MarketTier

    @field_validator("requested_tier")
    @classmethod
    def _fixed_tier(cls, value: MarketTier) -> MarketTier:
        if value is MarketTier.CUSTOM:
            raise ValueError("a proposal asks for TIGHT, DEFAULT or MEDIUM; CUSTOM is set by VIS")
        return value

    @model_validator(mode="after")
    def _resolves_after_close(self) -> Self:
        if self.resolve_by < self.close_at:
            raise ValueError("resolve_by must not be before close_at")
        return self


class MarketSummary(ResponseModel):
    """A market as a card: what the list, the portfolio and the detail header show."""

    id: UUID
    question: str
    status: MarketStatus
    """The effective status (A.4): an OPEN market past `close_at` is reported CLOSED."""

    trading_open: bool
    """True exactly when `status` is OPEN."""

    p_yes: Probability
    p_no: Probability
    """Always `1 - p_yes`, from the rounded value. A market never opened shows 0.5."""

    volume_cr: CreditAmount
    close_at: datetime
    resolve_by: datetime
    version: int
    """Grows by one per trade. A live event with a lower version is out of date."""

    outcome: ResolutionOutcome | None
    """YES or NO once RESOLVED, VOID once VOID, otherwise null."""


class MarketListResponse(ResponseModel):
    """`GET /markets`: soonest `close_at` first."""

    items: list[MarketSummary]


class MarketResolution(ResponseModel):
    """How a market ended. For VOID, `evidence` is the reason VIS gave."""

    outcome: ResolutionOutcome
    evidence: str
    resolved_at: datetime


class Holding(ResponseModel):
    """What a user holds of one outcome of a market."""

    outcome: Outcome
    shares: ShareAmount
    value_cr: CreditAmount
    """Shares at the current price of the outcome, rounded down. In a RESOLVED market:
    1 CR per share of the winning outcome, 0 for the other."""


class StakePreset(ResponseModel):
    """One buy button of the trade sheet (A.11)."""

    amount_cr: CreditAmount
    allowed: bool
    """Whether a buy for this budget would pass right now: the viewer may trade, has
    the balance and stays within the stake limit."""


class SellPortion(StrEnum):
    """How much of a holding a sell button of the trade sheet stands for."""

    HALF = "HALF"
    """Half the shares held, rounded down to a millionth of a share."""

    ALL = "ALL"


class SellPreset(ResponseModel):
    """One sell button of the trade sheet (A.11): a number of shares the server worked
    out, so the client halves and compares nothing."""

    outcome: Outcome
    portion: SellPortion
    shares: ShareAmount
    """Send this as `shares` of a SELL of `outcome`, and as `shares` of its quote."""

    allowed: bool
    """Whether that sell would pass right now: the viewer may trade and `shares` is at
    least the minimum trade size."""


class TradeBlockReason(StrEnum):
    """Why `MarketViewer.can_trade` is false."""

    MARKET_NOT_OPEN = "MARKET_NOT_OPEN"
    OWN_MARKET = "OWN_MARKET"
    """The viewer proposed this market (A.4, self-dealing)."""

    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"


class MarketViewer(ResponseModel):
    """The signed-in user's side of a market (SPEC §4, A.4)."""

    balance_cr: CreditAmount
    holdings: list[Holding]
    """Always two entries, YES then NO, with zero shares where nothing is held."""

    cost_cr: CreditAmount
    """Everything the viewer paid for buys in this market."""

    proceeds_cr: CreditAmount
    """Everything the viewer received for sells in this market."""

    net_stake_cr: CreditAmount
    """max(0, cost - proceeds): what a VOID would refund."""

    stake_limit_cr: CreditAmount
    stake_left_cr: CreditAmount
    """`stake_limit_cr - net_stake_cr`, never below zero."""

    stake_presets: list[StakePreset]
    sell_presets: list[SellPreset]
    """Always four entries: YES half, YES all, NO half, NO all."""

    can_trade: bool
    cannot_trade_reason: TradeBlockReason | None
    """Null exactly when `can_trade` is true."""


class MarketDetailResponse(MarketSummary):
    """`GET /markets/{id}`, and what proposing or an admin action returns."""

    yes_definition: str
    no_definition: str
    resolution_criteria: str
    resolution_source: str
    requested_tier: MarketTier
    tier: MarketTier | None
    """Null until VIS approves the market."""

    b_cr: CreditAmount
    max_loss_cr: CreditAmount
    """The market's loss budget (`markets.budget_micro`). Both are 0 until approval."""

    void_rule: str
    """What happens to stakes if the market is voided, shown before anyone trades."""

    created_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    rejection_reason: str | None
    resolution: MarketResolution | None
    proposer_email: str | None
    """Who proposed the market. Sent to admins only, null for everyone else."""

    viewer: MarketViewer | None
    """Null without a session."""
