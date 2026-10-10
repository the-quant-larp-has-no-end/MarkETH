"""How a market row becomes the numbers the API reports (SPEC §4, Appendix A.2, A.4).

The list, the detail page, the portfolio, quotes, trades, the risk page and live events
all show markets. The rules they must agree on live here, once:

* the effective status: an OPEN market past its deadline is CLOSED everywhere, and
  a PROPOSED one past it is EXPIRED (A.4);
* when trading in a market ended, which is where its chart stops;
* display probabilities: rounded once, with NO as the complement of the rounded YES;
* what a holding is worth, at which prices the open positions of everybody are
  valued, what a user's net stake in a market is and how much of the stake limit it
  leaves, what a settled market paid or gave back, what voiding a market would cost
  VIS;
* what a viewer can buy of each outcome on the trade sheet, in shares: where the
  slider of a buy runs and which of its shortcuts would be accepted, at the market
  maker's prices; and how many shares each sell shortcut stands for and what it
  brings in;
* which buy buttons for a budget would be accepted, and between which amounts the
  slider of a buy for a budget runs.

`MarketView` reads rows and settings and computes; it does no I/O. The frontend
computes nothing (golden rule 6), so every price it shows went through this class.
"""

import logging
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
    BuyEntry,
    BuyPreset,
    BuySlider,
    Holding,
    MarketSummary,
    SellPortion,
    SellPreset,
    StakePreset,
)

logger = logging.getLogger(__name__)

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

TRADING_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.OPEN, MarketStatus.HALTED}
)
"""Stored statuses of the markets in which trading is not over while their `close_at`
has not come: an open one, and a halted one, which trades nothing for now and may be
resumed."""

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
        self._share_presets_micro = tuple(settings.pricing.share_presets_micro)
        self._share_step_micro = settings.pricing.share_step_micro
        self._min_trade_micro = settings.pricing.min_trade_micro
        self._min_trade_shares_micro = settings.pricing.min_trade_shares_micro

    def effective_status(self, market: Market, now: datetime) -> MarketStatus:
        """The status to report and to act on (A.4): what the row says, except that from
        the instant of `close_at` on an OPEN market is CLOSED and a PROPOSED one is
        EXPIRED. Nobody trades the first and no admin decides on the second."""
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

    def trading_ended_at(self, market: Market, now: datetime) -> datetime | None:
        """The moment trading in `market` ended for good, or None while it can still
        trade at `now`. For a market that was opened.

        A market can still trade while its row says OPEN or HALTED and `close_at` has
        not come. In every other case trading is over. It ended at `close_at`, or
        before that at `closed_at`: the moment an admin closed the market, or a
        settlement closed it on its way. A market is resolved or voided no earlier
        than it was closed, so the earlier of `close_at` and `closed_at` is also the
        earlier of `close_at` and the moment it was closed, resolved or voided.

        The chart of a market is drawn up to this moment, and up to now while there
        is none (`HistoryResponse.until`).
        """
        if market.status in TRADING_STATUSES and now < market.close_at:
            return None
        if market.closed_at is None:
            return market.close_at
        return min(market.close_at, market.closed_at)

    def p_yes_micro(self, market: Market) -> int:
        """The exact YES price in millionths, from the row's q and b."""
        if market.b_micro == 0:
            return EVEN_ODDS_MICRO
        return lmsr.price_yes_micro(_state(market), precision=self._precision)

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

    def most_shares_micro(self, market: Market, outcome: Outcome, budget_micro: int) -> int:
        """The most µshares of `outcome` whose cost, rounded up, is at most
        `budget_micro`: `lmsr.quote_buy_for_budget(state, outcome,
        budget_micro).shares_micro`. 0 for a budget of nothing. The market has a b above
        zero: the caller asks for a market that trades."""
        if budget_micro <= 0:
            return 0
        quote = lmsr.quote_buy_for_budget(
            _state(market), outcome, budget_micro, precision=self._precision
        )
        return quote.shares_micro

    def buy_entries(
        self, market: Market, stake_left_micro: int, balance_micro: int, can_trade: bool
    ) -> list[BuyEntry]:
        """What the viewer can buy of each outcome, YES then NO (A.11).

        The budget is `min(stake_left_micro, balance_micro)`. Per outcome, `most` is 0
        when `can_trade` is false or the budget is 0, and else
        `most_shares_micro(market, outcome, budget)`. A viewer who may not trade or has
        no budget causes no call of the pricing module, so a market without a b (a
        proposal) is never handed to it; one read prices one budget per outcome and no
        size of its own.

        The slider: `step` is `TRADE_SHARE_STEP`; `min` is the smallest multiple of the
        step, above zero, that is at least `MIN_TRADE_SHARES`; `max` is the largest
        multiple of the step that is at most `most`; there is none when `max` would lie
        below `min`. A shortcut, one per `TRADE_SHARE_PRESETS` in that order, is allowed
        when `MIN_TRADE_SHARES <= shares <= most`.

        A buy of at most `most` µshares costs at most the budget, because the cost
        grows with the size and is rounded up either way. The budget is at most the
        stake left and at most the balance, which is all `QuoteService.check_buy` asks
        of a buy. So whatever an entry offers, the quote and the trade grant on the
        same rows.

        A budget quote whose rounding guard fails (`AssertionError` of the pricing
        module) is no range for that outcome: no slider, no shortcut allowed, and one
        error line in the log. The page of the market is read all the same, and the
        quote and the trade of that budget keep failing loudly.
        """
        budget_micro = min(stake_left_micro, balance_micro)
        step_micro = self._share_step_micro
        steps_to_the_smallest_trade = -(-self._min_trade_shares_micro // step_micro)
        smallest_micro = max(1, steps_to_the_smallest_trade) * step_micro
        entries: list[BuyEntry] = []
        for outcome in (Outcome.YES, Outcome.NO):
            most_micro = self._most_or_none(market, outcome, budget_micro) if can_trade else 0
            largest_micro = most_micro // step_micro * step_micro
            slider = (
                None
                if largest_micro < smallest_micro
                else BuySlider(
                    min=smallest_micro // MICRO,
                    max=largest_micro // MICRO,
                    step=step_micro // MICRO,
                )
            )
            presets = [
                BuyPreset(
                    shares=to_cr(shares_micro),
                    allowed=self._min_trade_shares_micro <= shares_micro <= most_micro,
                )
                for shares_micro in self._share_presets_micro
            ]
            entries.append(BuyEntry(outcome=outcome, slider=slider, presets=presets))
        return entries

    def _most_or_none(self, market: Market, outcome: Outcome, budget_micro: int) -> int:
        """`most_shares_micro`, and 0 with one error line in the log where the budget
        quote fails its rounding guard: the read of a market answers without a range
        for that outcome and does not fail."""
        try:
            return self.most_shares_micro(market, outcome, budget_micro)
        except AssertionError as failure:
            logger.error(
                "No buy range of %s in market %s: the quote of a budget of %s µCR "
                "failed at q_yes=%s q_no=%s b=%s (%s)",
                outcome.value,
                market.id,
                budget_micro,
                market.q_yes_micro,
                market.q_no_micro,
                market.b_micro,
                failure,
            )
            return 0

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

    def void_cost_micro(self, net_stakes_micro: int, account_balance_micro: int) -> int:
        """What VIS would have to add if a market were voided now, in µCR (A.5).

        A void refunds every trader their net stake. `net_stakes_micro` is those of
        all its traders added up (`positions.net_stake_totals`), and
        `account_balance_micro` what the market's account holds to pay them from. The
        difference is what traders who sold above their cost have taken out: a void
        does not take that back, and `vis:mm_pnl` books it. It is never below zero
        while the books are right, and it is bounded by the stake limit per trader,
        not by the market's budget.

        The risk page and the market detail of an admin show this figure, and both
        get it here, from those two readings.
        """
        return net_stakes_micro - account_balance_micro

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

    def sell_presets(
        self, market: Market, positions: Sequence[Position], can_trade: bool
    ) -> list[SellPreset]:
        """The sell shortcuts of the trade sheet: half and all of each holding, with
        what each brings in (A.11).

        Always four entries, YES then NO, HALF then ALL. Half is rounded down to a whole
        micro-share, so selling HALF and then ALL sells everything. A shortcut is
        allowed when the viewer may trade (`can_trade`) and its size is above zero and
        at least `MIN_TRADE_SHARES`; an outcome nobody holds gives two shortcuts of
        zero shares that are not allowed.

        `proceeds_cr` is `lmsr.quote_sell_shares(state, outcome, size).amount_micro`
        when the viewer may trade and the size is above zero, and 0 otherwise: the
        `amount_cr` a quote of that sale gives on the same rows. A shortcut below the
        smallest trade is not allowed and still says what it is worth. One read prices
        each shortcut at most once, and nothing for a viewer who may not trade, so a
        market without a b is never handed to the pricing module.

        A holding larger than the market has sold of that outcome is a broken
        database: `ValueError`, before the pricing module sees a negative quantity.
        """
        shares_by_outcome = {position.outcome: position.shares_micro for position in positions}
        presets: list[SellPreset] = []
        for outcome in (Outcome.YES, Outcome.NO):
            held_micro = shares_by_outcome.get(outcome, 0)
            for portion in (SellPortion.HALF, SellPortion.ALL):
                size_micro = held_micro // 2 if portion is SellPortion.HALF else held_micro
                proceeds_micro = (
                    self._sale_proceeds_micro(market, outcome, held_micro, size_micro)
                    if can_trade and size_micro > 0
                    else 0
                )
                presets.append(
                    SellPreset(
                        outcome=outcome,
                        portion=portion,
                        shares=to_cr(size_micro),
                        proceeds_cr=to_cr(proceeds_micro),
                        allowed=can_trade
                        and size_micro > 0
                        and size_micro >= self._min_trade_shares_micro,
                    )
                )
        return presets

    def _sale_proceeds_micro(
        self, market: Market, outcome: Outcome, held_micro: int, size_micro: int
    ) -> int:
        """What selling `size_micro` of a holding of `held_micro` µshares of `outcome`
        brings in, rounded down."""
        sold_micro = market.q_yes_micro if outcome is Outcome.YES else market.q_no_micro
        if held_micro > sold_micro:
            raise ValueError(
                f"a holding of {held_micro} µshares of {outcome.value} in market {market.id}, "
                f"which has sold {sold_micro}"
            )
        quote = lmsr.quote_sell_shares(
            _state(market), outcome, size_micro, precision=self._precision
        )
        return quote.amount_micro


def _state(market: Market) -> lmsr.MarketState:
    """The quantities of a market row as the pricing module takes them."""
    return lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)
