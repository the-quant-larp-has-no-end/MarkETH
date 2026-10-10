"""Quotes and trades (SPEC §4, §8, §10, Appendix A.4).

A trade is sized in one of three modes, and each mode has its own slippage guard:

| Side | Sized by    | Guard             | `QUOTE_STALE` when                 |
| ---- | ----------- | ----------------- | ---------------------------------- |
| BUY  | `amount_cr` | `min_shares`      | fewer shares than `min_shares`     |
| BUY  | `shares`    | `max_cost_cr`     | cost above `max_cost_cr`           |
| SELL | `shares`    | `min_proceeds_cr` | proceeds below `min_proceeds_cr`   |

The models reject every other combination with 422, so a service never sees one. A
form sends none of them, so these rules are worded for whoever builds a client: the
size that is wrong and the guard that does not fit are named in the error's `loc`.
"""

from datetime import datetime
from typing import Final, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from app.core.enums import Outcome, TradeSide
from app.schemas.common import (
    CreditAmount,
    PositiveCreditAmount,
    PositiveShareAmount,
    Probability,
    RequestModel,
    ResponseModel,
    ShareAmount,
    on_field,
    said,
)

SIZED_ONE_WAY_MESSAGE: Final[str] = "Enter either an amount or a number of shares."
"""What a trade or a quote says that names both sizes or neither."""

SELL_BY_SHARES_MESSAGE: Final[str] = "Enter the number of shares to sell."
"""What a sell says that is sized by an amount, on `amount_cr`."""

GUARD_DOES_NOT_APPLY_MESSAGE: Final[str] = "This limit does not apply to this trade."
"""What a trade says on a guard that is not the one of its mode."""


class TradeSize(RequestModel):
    """What to trade and how much of it: the part a quote and a trade have in common."""

    side: TradeSide
    outcome: Outcome
    amount_cr: PositiveCreditAmount | None = None
    """A budget for a buy. It buys as many shares as it pays for, rounded down, and
    the trader is charged exactly this amount."""

    shares: PositiveShareAmount | None = None

    @model_validator(mode="after")
    def _sized_one_way(self) -> Self:
        if (self.amount_cr is None) == (self.shares is None):
            raise said("value_error", SIZED_ONE_WAY_MESSAGE)
        if self.side is TradeSide.SELL and self.amount_cr is not None:
            raise on_field("amount_cr", "value_error", SELL_BY_SHARES_MESSAGE)
        return self


class QuoteQuery(TradeSize):
    """Query string of `GET /markets/{id}/quote`: a trade without its guards."""


class TradeRequest(TradeSize):
    """Body of `POST /markets/{id}/trades`. Guards are optional (A.4)."""

    min_shares: PositiveShareAmount | None = None
    max_cost_cr: PositiveCreditAmount | None = None
    min_proceeds_cr: CreditAmount | None = None
    quote_version: StrictInt | None = Field(default=None, ge=0)
    """`quote_version` of the quote the user saw. Informational: the guards decide."""

    @model_validator(mode="after")
    def _guard_fits_mode(self) -> Self:
        allowed = self._guard_of_mode()
        for guard in ("min_shares", "max_cost_cr", "min_proceeds_cr"):
            if guard != allowed and getattr(self, guard) is not None:
                raise on_field(guard, "value_error", GUARD_DOES_NOT_APPLY_MESSAGE)
        return self

    def _guard_of_mode(self) -> str:
        if self.side is TradeSide.SELL:
            return "min_proceeds_cr"
        return "min_shares" if self.amount_cr is not None else "max_cost_cr"


class QuoteResponse(ResponseModel):
    """What a trade would do right now. Also sent as `details.quote` of `QUOTE_STALE`.

    `GET /markets/{id}/quote` answers with it for a trade the caller could make. For
    one they could not, it answers the error the trade would get instead, with the
    same code, message and details: 403 `FORBIDDEN` in a market the caller proposed,
    409 `MARKET_NOT_OPEN`, 409 `INSUFFICIENT_SHARES` for a sell of more than the
    caller holds, 422 `TRADE_TOO_SMALL`, 409 `LIMIT_EXCEEDED` for a buy above the
    stake left and 409 `INSUFFICIENT_BALANCE` for one above the balance. So the
    answer to a typed amount is the server's, and the client compares nothing."""

    market_id: UUID
    side: TradeSide
    outcome: Outcome
    shares: ShareAmount
    amount_cr: CreditAmount
    """The cost of a buy (rounded up) or the proceeds of a sell (rounded down). The
    proceeds of a sell can be 0 when the shares are all but worthless."""

    average_price: Probability
    """`amount_cr / shares`: credits per share, rounded like a probability."""

    p_yes_before: Probability
    p_no_before: Probability
    p_yes_after: Probability
    p_no_after: Probability
    quote_version: int
    """The market's `version` this quote was computed from."""


class TradeResponse(ResponseModel):
    """An executed trade. Sending the same Idempotency-Key again returns it unchanged."""

    trade_id: UUID
    market_id: UUID
    side: TradeSide
    outcome: Outcome
    shares: ShareAmount
    amount_cr: CreditAmount
    """What was paid for a buy or received for a sell."""

    average_price: Probability
    p_yes_before: Probability
    p_no_before: Probability
    p_yes_after: Probability
    p_no_after: Probability
    market_version: int
    """The market's `version` after this trade."""

    balance_cr: CreditAmount
    """The trader's balance after this trade."""

    net_stake_cr: CreditAmount
    stake_left_cr: CreditAmount
    """What the trader may still put at stake in this market."""

    created_at: datetime
