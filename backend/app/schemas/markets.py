"""Markets as the API shows them: list, detail and proposal (SPEC §6, §10, A.4, A.5, A.11).

The frontend computes nothing (golden rule 6), so a market carries every number its
screens show: display probabilities, the effective status, and for the viewer their
holdings, their stake, what they can buy of each outcome right now, and how many shares
each sell button stands for and what it would bring in.

How much liquidity a market has is VIS's business. A proposal names none, an admin
picks the tier when approving, and a market tells its tier, its b and its loss budget
to admins alone.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Final, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.core.enums import MarketStatus, MarketTier, Outcome, ResolutionOutcome
from app.schemas.common import (
    Accepted,
    CreditAmount,
    Probability,
    RequestModel,
    ResponseModel,
    ShareAmount,
    SignedCreditAmount,
    on_field,
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

DECIDED_BEFORE_CLOSE_MESSAGE: Final[str] = "A market cannot be decided before trading closes."
"""What a proposal says on `resolve_by` when it lies before `close_at`. The service
says the same for dates an approval edits (`check_market_dates`)."""


class ProposeMarketRequest(RequestModel):
    """`POST /markets`: what a proposal must state (SPEC §6).

    The time rules that depend on the clock are the service's: `close_at` in the future
    and within `MAX_MARKET_LIFETIME`, `resolve_by` within `MAX_RESOLUTION_DELAY_DAYS`
    of `close_at`.

    A proposal says nothing about liquidity: the admin who approves the market picks
    its tier. A field that names one is refused like any field the request does not
    have.
    """

    question: QuestionText
    yes_definition: ShortText
    no_definition: ShortText
    resolution_criteria: LongText
    resolution_source: ShortText
    close_at: AwareDatetime
    resolve_by: AwareDatetime
    policy_accepted: Accepted
    """The proposer accepts the usage policy for this proposal. Every proposal says
    so again: `true`, or the request is rejected on this field."""

    @model_validator(mode="after")
    def _resolves_after_close(self) -> Self:
        if self.resolve_by < self.close_at:
            raise on_field("resolve_by", "value_error", DECIDED_BEFORE_CLOSE_MESSAGE)
        return self


class MarketSummary(ResponseModel):
    """A market as a card: what the list, the portfolio and the detail header show."""

    id: UUID
    question: str
    status: MarketStatus
    """The effective status (A.4): an OPEN market past `close_at` is reported CLOSED,
    and a PROPOSED one past `close_at` is reported EXPIRED."""

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
    """`GET /markets`: the markets in the statuses asked for, soonest `close_at` first."""

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
    the balance and stays within the stake limit, and the amount is no smaller than
    the minimum trade."""


class BuySlider(ResponseModel):
    """The slider of a buy on the trade sheet (A.11): the whole shares of one outcome
    between which the viewer can buy right now, and the step between two positions.
    JSON integers, as a range input takes them. Every multiple of `step` from `min`
    to `max` is a size a buy passes with: send it as `shares`."""

    min: int
    """The smallest multiple of `step` that is a trade: at least `min_trade_shares`."""

    max: int
    """The largest multiple of `step` whose cost the stake left and the balance both
    cover at the price of this answer. Never below `min`."""

    step: int
    """`share_step` of `GET /config`."""


class BuyPreset(ResponseModel):
    """One shortcut of a buy on the trade sheet (A.11)."""

    shares: ShareAmount
    """A whole number of shares, one entry of `share_presets` of `GET /config`. Send
    it as `shares` of a BUY, and as `shares` of its quote."""

    allowed: bool
    """Whether a buy of this many shares of the outcome would pass right now: the
    viewer may trade, the size is no smaller than the minimum trade, and its cost is
    covered by the stake left and by the balance."""


class BuyEntry(ResponseModel):
    """What the viewer can buy of one outcome right now (SPEC §4, A.11). The server
    works it out with the market maker's prices, so the client prices and compares
    nothing."""

    outcome: Outcome
    slider: BuySlider | None
    """Where the slider of a buy of `outcome` runs. Null when the viewer cannot buy a
    whole step of it right now: they may not trade, or the stake left or the balance
    does not cover the smallest buy. A size typed beside the slider is judged by
    `GET /markets/{id}/quote`."""

    presets: list[BuyPreset]
    """One entry per shortcut, in the order of `share_presets` of `GET /config`."""


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

    proceeds_cr: CreditAmount
    """What selling `shares` of `outcome` would bring in at the price of this answer,
    rounded down: the `amount_cr` `GET /markets/{id}/quote` gives for that sale. 0
    when `shares` is 0 or the viewer may not trade."""

    allowed: bool
    """Whether that sell would pass right now: the viewer may trade and `shares` is at
    least the minimum trade size."""


class SellSlider(ResponseModel):
    """The slider of a sell on the trade sheet (A.11): the whole shares of one outcome
    between which the viewer can sell right now, and the step between two positions.
    JSON integers, as a range input takes them. Every multiple of `step` from `min`
    to `max` is a size a sell passes with: send it as `shares`."""

    min: int
    """The smallest multiple of `step` that is a trade: at least `min_trade_shares`."""

    max: int
    """The largest multiple of `step` the viewer holds of the outcome. Never below
    `min`. What is held beyond it is sold with the ALL entry of `sell_presets`."""

    step: int
    """`share_step` of `GET /config`."""


class SellEntry(ResponseModel):
    """What the viewer can sell of one outcome by the slider right now (A.11). The
    server reads it from the holding, so the client compares nothing."""

    outcome: Outcome
    slider: SellSlider | None
    """Where the slider of a sell of `outcome` runs. Null when the viewer cannot sell
    a whole step of it right now: they may not trade, or hold less than the smallest
    sale. A size typed beside the slider is judged by `GET /markets/{id}/quote`."""


class TradeBlockReason(StrEnum):
    """Why `MarketViewer.can_trade` is false."""

    MARKET_NOT_OPEN = "MARKET_NOT_OPEN"
    OWN_MARKET = "OWN_MARKET"
    """The viewer proposed this market (A.4, self-dealing)."""


class MarketViewer(ResponseModel):
    """The viewer's side of a market (SPEC §4, A.4)."""

    balance_cr: CreditAmount
    holdings: list[Holding]
    """Always two entries, YES then NO, with zero shares where nothing is held."""

    cost_cr: CreditAmount
    """Everything the viewer paid for buys in this market."""

    proceeds_cr: CreditAmount
    """Everything the viewer received for sells in this market."""

    net_stake_cr: CreditAmount
    """max(0, cost - proceeds): what a VOID would refund."""

    payout_cr: CreditAmount | None
    """What the market paid the viewer when it was RESOLVED: 1 CR for every share held
    of the winning outcome, 0 for someone who held none. Null in any other status."""

    refund_cr: CreditAmount | None
    """What the viewer got back when the market was VOID: their net stake. Null in any
    other status."""

    stake_limit_cr: CreditAmount
    stake_left_cr: CreditAmount
    """`stake_limit_cr - net_stake_cr`, never below zero."""

    buy: list[BuyEntry]
    """Always two entries, YES then NO."""

    stake_presets: list[StakePreset]
    """One entry per buy button, in the order of `stake_presets_cr` of `GET /config`."""

    buy_slider: BuySlider | None
    """Where the slider of a buy by amount runs, counted in whole credits: every
    multiple of `step` from `min` to `max` is a budget a buy passes with, sent as
    `amount_cr`, and `step` is `stake_step_cr` of `GET /config`. Null when the viewer
    cannot buy anything right now: they may not trade, or the stake left or the balance
    is below the smallest buy. An amount typed in between two positions is judged by
    `GET /markets/{id}/quote`."""

    can_buy: bool
    """Whether any amount is a buy the viewer can make right now: they may trade, and
    the stake left and the balance both cover the smallest buy (`min_trade_cr` of
    `GET /config`). While it is false no amount passes, whatever is sent as
    `amount_cr`, so the trade sheet takes none."""

    sell_presets: list[SellPreset]
    """Always four entries: YES half, YES all, NO half, NO all."""

    sell: list[SellEntry]
    """Always two entries, YES then NO."""

    can_trade: bool
    cannot_trade_reason: TradeBlockReason | None
    """Null exactly when `can_trade` is true."""

    can_settle: bool
    """Whether the viewer is someone who may resolve or void this market: an admin who
    never traded it (A.4). An admin who did trade it is answered 403 by both actions,
    and another admin has to settle it. This says who may settle, not when: which
    actions the market's status allows is SPEC §6. False for everybody who is no admin."""


class MarketDetailResponse(MarketSummary):
    """`GET /markets/{id}`, and what proposing or an admin action returns."""

    yes_definition: str
    no_definition: str
    resolution_criteria: str
    resolution_source: str
    tier: MarketTier | None
    """The liquidity tier VIS opened the market with. Sent to admins only, null for
    everyone else. To an admin it is null until the market is approved."""

    b_cr: CreditAmount | None
    """The market's liquidity parameter b. Sent to admins only, null for everyone
    else. 0 until the market is approved."""

    max_loss_cr: CreditAmount | None
    """The market's loss budget (`markets.budget_micro`). Sent to admins only, null
    for everyone else. 0 until the market is approved."""

    void_cost_cr: SignedCreditAmount | None
    """What VIS would have to add if the market were voided now: the net stakes a void
    refunds, minus the balance of the market's account, as `RiskMarketRow.void_cost_cr`
    says it. Sent to admins only, null for everyone else, and null for a market with
    nothing to void: one that was never opened and one that is settled."""

    void_rule: str
    """What happens to stakes if the market is voided, shown before anyone trades."""

    created_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    rejection_reason: str | None
    resolution: MarketResolution | None
    proposer_email: str | None
    """Who proposed the market. Sent to admins only, null for everyone else."""

    viewer: MarketViewer
    """What the market is to whoever asked: every reader of a market is a user."""
