"""How a market row becomes the numbers the API reports (SPEC §4, Appendix A.2, A.4).

The list, the detail page, the portfolio, quotes, trades, the risk page and live events
all show markets. The rules they must agree on live here, once:

* the effective status: an OPEN market past its deadline is CLOSED everywhere, and
  a PROPOSED one past it is EXPIRED (A.4);
* display probabilities: rounded once, with NO as the complement of the rounded YES;
* what a holding is worth, at which prices the open positions of everybody are
  valued, what a user's net stake in a market is and how much of the stake limit it
  leaves, what a settled market paid or gave back;
* which buy buttons of the trade sheet would be accepted, between which amounts the
  slider of a buy runs, and how many shares its sell buttons stand for.

`MarketView` reads rows and settings and computes; it does no I/O. The frontend
computes nothing (golden rule 6), so every price it shows went through this class.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from app.core.config import Settings
from app.core.enums import MarketStatus, Outcome, ResolutionOutcome
from app.core.units import MICRO, complement, display_probability, probability_to_micro, to_cr
from app.models.market import Market, Resolution
from app.models.trading import Position
from app.pricing import lmsr
from app.schemas.markets import (
    BuySlider,
    Holding,
    MarketSummary,
    SellPortion,
    SellPreset,
    StakePreset,
)

EVEN_ODDS_MICRO: Final[int] = MICRO // 2
"""The price of a market that was never opened: it has no b yet, so no LMSR price."""

SETTLED_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.RESOLVED, MarketStatus.VOID}
)

UNSETTLED_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED}
)
"""Stored statuses of markets whose shares VIS may still have to pay out."""

HIDDEN_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.PROPOSED, MarketStatus.REJECTED}
)
"""Stored statuses of the markets only admins and their proposer may see (A.5): a
proposal, whatever became of it. To anyone else they are 404. An expired proposal is
one of them: its row says PROPOSED."""

_STORED_AS: Final[Mapping[MarketStatus, frozenset[MarketStatus]]] = {
    MarketStatus.CLOSED: frozenset({MarketStatus.CLOSED, MarketStatus.OPEN}),
    MarketStatus.EXPIRED: frozenset({MarketStatus.PROPOSED}),
}
"""What a row can say of a market that is reported in a status, where that is not the
status itself: the two that `MarketView.effective_status` works out from the clock."""


class MarketView:
    def __init__(self, settings: Settings) -> None:
        self._display_decimals = settings.pricing.price_display_decimals
        self._precision = settings.pricing.decimal_precision
        self._stake_limit_micro = settings.pricing.max_stake_per_user_market_micro
        self._stake_presets_micro = tuple(settings.pricing.stake_presets_micro)
        self._stake_step_micro = settings.pricing.stake_step_micro
        self._min_trade_micro = settings.pricing.min_trade_micro
        self._min_trade_shares_micro = settings.pricing.min_trade_shares_micro

    def effective_status(self, market: Market, now: datetime) -> MarketStatus:
        """The status to report and to act on (A.4): what the row says, except that from
        the instant of `close_at` on an OPEN market is CLOSED and a PROPOSED one is
        EXPIRED. Nobody trades the first any more and no admin decides on the second."""
        if now >= market.close_at:
            if market.status is MarketStatus.OPEN:
                return MarketStatus.CLOSED
            if market.status is MarketStatus.PROPOSED:
                return MarketStatus.EXPIRED
        return market.status

    def stored_statuses(self, effective: Collection[MarketStatus]) -> frozenset[MarketStatus]:
        """The statuses a row can say of a market whose effective status is one of
        `effective`: what a list reads before it keeps the rows that
        `effective_status` reports as asked.

        A status stands for itself, with two exceptions: a market reported CLOSED may
        be stored OPEN, and one reported EXPIRED is stored PROPOSED. EXPIRED is never
        among the result: no row says it.
        """
        return frozenset().union(
            *(_STORED_AS.get(status, frozenset({status})) for status in effective)
        )

    def is_trading_open(self, market: Market, now: datetime) -> bool:
        return self.effective_status(market, now) is MarketStatus.OPEN

    def p_yes_micro(self, market: Market) -> int:
        """The exact YES price in millionths, from the row's q and b."""
        if market.b_micro == 0:
            return EVEN_ODDS_MICRO
        state = lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)
        return lmsr.price_yes_micro(state, precision=self._precision)

    def probabilities(self, p_yes_micro: int) -> tuple[float, float]:
        """`(p_yes, p_no)` for display: YES rounded, NO its complement (A.2)."""
        p_yes = display_probability(p_yes_micro, self._display_decimals)
        return p_yes, complement(p_yes, self._display_decimals)

    def average_price(self, amount_micro: int, shares_micro: int) -> float:
        """Credits per share of a quote or trade, rounded like a probability."""
        if shares_micro <= 0:
            raise ValueError(f"shares_micro must be positive, got {shares_micro}")
        ratio_micro = probability_to_micro(Decimal(amount_micro) / Decimal(shares_micro))
        return display_probability(ratio_micro, self._display_decimals)

    def summary(
        self, market: Market, now: datetime, resolution: Resolution | None
    ) -> MarketSummary:
        """The card of a market. A RESOLVED or VOID market needs its `resolutions` row."""
        if market.status in SETTLED_STATUSES and resolution is None:
            raise ValueError(f"market {market.id} is {market.status} but no resolution was given")
        status = self.effective_status(market, now)
        p_yes, p_no = self.probabilities(self.p_yes_micro(market))
        return MarketSummary(
            id=market.id,
            question=market.question,
            status=status,
            trading_open=status is MarketStatus.OPEN,
            p_yes=p_yes,
            p_no=p_no,
            volume_cr=to_cr(market.volume_micro),
            close_at=market.close_at,
            resolve_by=market.resolve_by,
            version=market.version,
            outcome=None if resolution is None else resolution.outcome,
        )

    def net_stake_micro(self, positions: Sequence[Position]) -> int:
        """A user's net stake in one market: max(0, Σ cost - Σ proceeds) over both outcomes.

        `positions` are that user's rows in that market, at most one per outcome.
        """
        cost_micro = sum(position.cost_micro for position in positions)
        proceeds_micro = sum(position.proceeds_micro for position in positions)
        return max(0, cost_micro - proceeds_micro)

    @property
    def stake_limit_micro(self) -> int:
        """`MAX_STAKE_PER_USER_MARKET`: the most a user may have at stake in one market."""
        return self._stake_limit_micro

    def stake_left_micro(self, net_stake_micro: int) -> int:
        """What a user may still put at stake under `MAX_STAKE_PER_USER_MARKET`."""
        return max(0, self._stake_limit_micro - net_stake_micro)

    def stake_presets(
        self, stake_left_micro: int, balance_micro: int, can_trade: bool
    ) -> list[StakePreset]:
        """The buy buttons of the trade sheet: one per `TRADE_STAKE_PRESETS_CR`, in that
        order (A.11).

        A preset is allowed when the viewer may trade (`can_trade`) and its amount is at
        least `MIN_TRADE_CR`, at most `stake_left_micro` and at most `balance_micro`.
        `stake_left_micro` is `stake_left_micro(net_stake_micro(...))` of the viewer's
        positions, so a buy for an allowed preset passes the stake check of the trade.
        """
        return [
            StakePreset(
                amount_cr=to_cr(amount_micro),
                allowed=can_trade
                and self._min_trade_micro <= amount_micro <= stake_left_micro
                and amount_micro <= balance_micro,
            )
            for amount_micro in self._stake_presets_micro
        ]

    def buy_slider(
        self, stake_left_micro: int, balance_micro: int, can_trade: bool
    ) -> BuySlider | None:
        """Where the slider of a buy runs, in whole credits; None when the viewer cannot
        buy anything right now (A.11).

        `step` is `TRADE_STAKE_STEP_CR`, a whole number of credits. `min` is the
        smallest multiple of the step, above zero, that is at least `MIN_TRADE_CR`.
        `max` is the largest multiple of the step that is at most `stake_left_micro`
        and at most `balance_micro`: what is beyond a whole step of either does not
        count, so a balance of 10.5 credits ends a slider of step 1 at 10. There is
        no slider when the viewer may not trade (`can_trade`, as for the stake
        presets) and when `max` would lie below `min`.

        Every multiple of the step from `min` to `max` is at least the smallest
        trade, within the stake left and covered by the balance, which is all that
        `QuoteService.price` and `check_buy` ask of a buy for an amount. So wherever
        the slider stands, somebody who may trade gets that buy.
        """
        step_micro = self._stake_step_micro
        steps_to_the_smallest_trade = -(-self._min_trade_micro // step_micro)
        smallest_micro = max(1, steps_to_the_smallest_trade) * step_micro
        largest_micro = min(stake_left_micro, balance_micro) // step_micro * step_micro
        if not can_trade or largest_micro < smallest_micro:
            return None
        return BuySlider(
            min=smallest_micro // MICRO, max=largest_micro // MICRO, step=step_micro // MICRO
        )

    def payout_micro(
        self, positions: Sequence[Position], resolution: Resolution | None
    ) -> int | None:
        """What a RESOLVED market paid a user: 1 CR for every share they held of the
        winning outcome, 0 for someone who held none.

        `positions` are that user's rows in that market. None for a market that did not
        resolve to YES or NO: one that is not settled (no `resolution`), and a VOID one.
        Resolution leaves positions as they are, so the shares are still there to read.
        """
        if resolution is None or resolution.outcome is ResolutionOutcome.VOID:
            return None
        return sum(
            position.shares_micro
            for position in positions
            if position.outcome.value == resolution.outcome.value
        )

    def refund_micro(
        self, positions: Sequence[Position], resolution: Resolution | None
    ) -> int | None:
        """What a VOID market gave a user back: their net stake.

        None for every other market. A void sets the shares of a position to zero and
        keeps its cost and proceeds, which is what the net stake is read from.
        """
        if resolution is None or resolution.outcome is not ResolutionOutcome.VOID:
            return None
        return self.net_stake_micro(positions)

    def holding_value_micro(
        self, market: Market, outcome: Outcome, shares_micro: int, resolution: Resolution | None
    ) -> int:
        """What `shares_micro` of `outcome` are worth, rounded down.

        Before settlement: the shares at the outcome's current price. In a RESOLVED
        market: 1 CR per share of the winning outcome and nothing for the other. In a
        VOID market: nothing (its shares are zero; the refund is the net stake).
        """
        if resolution is not None:
            if resolution.outcome is ResolutionOutcome.VOID:
                return 0
            return shares_micro if resolution.outcome.value == outcome.value else 0
        p_yes_micro = self.p_yes_micro(market)
        price_micro = p_yes_micro if outcome is Outcome.YES else MICRO - p_yes_micro
        return shares_micro * price_micro // MICRO

    def open_prices_micro(self, markets: Iterable[Market]) -> dict[UUID, int]:
        """The exact YES price of each of `markets`, in millionths, by market id.

        They are the prices `holding_value_micro` values a holding at while its market
        is not settled, for a caller that values many holdings at once and not in
        Python: the leaderboard hands them to its statement, which does for every
        user what `holding_value_micro` does for one holding. A YES share is worth
        the price and a NO share a million minus it; the value of a holding is its
        shares times that, divided by a million and rounded down.
        """
        return {market.id: self.p_yes_micro(market) for market in markets}

    def holdings(
        self, market: Market, positions: Sequence[Position], resolution: Resolution | None
    ) -> list[Holding]:
        """A user's holdings in one market: always two entries, YES then NO."""
        shares_by_outcome = {position.outcome: position.shares_micro for position in positions}
        holdings: list[Holding] = []
        for outcome in (Outcome.YES, Outcome.NO):
            shares_micro = shares_by_outcome.get(outcome, 0)
            value_micro = self.holding_value_micro(market, outcome, shares_micro, resolution)
            holdings.append(
                Holding(outcome=outcome, shares=to_cr(shares_micro), value_cr=to_cr(value_micro))
            )
        return holdings

    def sell_presets(self, positions: Sequence[Position], can_trade: bool) -> list[SellPreset]:
        """The sell buttons of the trade sheet: half and all of each holding (A.11).

        Always four entries, YES then NO, HALF then ALL. Half is rounded down to a whole
        micro-share, so selling HALF and then ALL sells everything. A preset is allowed
        when the viewer may trade (`can_trade`, as for the stake presets) and its size
        is at least `MIN_TRADE_SHARES`; an outcome nobody holds gives two presets of
        zero shares that are not allowed.
        """
        shares_by_outcome = {position.outcome: position.shares_micro for position in positions}
        presets: list[SellPreset] = []
        for outcome in (Outcome.YES, Outcome.NO):
            held_micro = shares_by_outcome.get(outcome, 0)
            for portion in (SellPortion.HALF, SellPortion.ALL):
                size_micro = held_micro // 2 if portion is SellPortion.HALF else held_micro
                presets.append(
                    SellPreset(
                        outcome=outcome,
                        portion=portion,
                        shares=to_cr(size_micro),
                        allowed=can_trade
                        and size_micro > 0
                        and size_micro >= self._min_trade_shares_micro,
                    )
                )
        return presets
