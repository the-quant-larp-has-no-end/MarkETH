"""Pricing a trade without executing it (SPEC §4, §8, §10, Appendix A.4, A.9).

`quote` serves `GET /markets/{id}/quote`: what a trade would cost or return, or the
refusal the trade would get, for the one who asks.

The rules a trade is held to before it writes anything live here, once:

* `admit`: may this trader make this trade in this market at all, and at what price;
* `price`: the LMSR figures of a size on a market row, and the smallest size;
* `check_buy`: the stake limit and the balance of a buyer.

`quote` applies them to the rows as they stand, without a lock. `TradingService`
applies the same three to the rows it holds locked, with the slippage guard between
the last two, so a trade is priced and judged exactly the way its quote was.
`to_response` is how both show the figures.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import Outcome, TradeSide
from app.core.errors import (
    ForbiddenError,
    InsufficientBalanceError,
    InsufficientSharesError,
    LimitExceededError,
    MarketNotOpenError,
    NotFoundError,
    TradeTooSmallError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr, to_micro
from app.models.market import Market
from app.models.trading import Position
from app.pricing import lmsr
from app.pricing.lmsr import TradeQuote
from app.schemas.common import Limit
from app.schemas.trading import QuoteQuery, QuoteResponse, TradeSize
from app.services import UowFactory
from app.services.market_view import HIDDEN_STATUSES, MarketView

OWN_MARKET_MESSAGE: Final[str] = "You proposed this market, so you cannot trade it."
"""Message of the `ForbiddenError` a trade by the market's proposer gets, and a quote
for one (A.4). `MarketViewer.cannot_trade_reason` says `OWN_MARKET` to that user
beforehand."""


class QuoteService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
    ) -> None:
        self._uow_factory = uow_factory
        self._pricing = settings.pricing
        self._clock = clock
        self._view = view

    async def quote(
        self, viewer: AuthenticatedUser, market_id: UUID, query: QuoteQuery
    ) -> QuoteResponse:
        """What a trade would cost or return `viewer` right now, or the refusal the
        trade would get (`GET /markets/{id}/quote`).

        The trade sheet asks this for every amount somebody types, so the answer is
        the server's judgement of that amount: the frontend compares nothing with a
        balance or a limit (golden rule 6).

        1. One read transaction, no lock: the market, then the viewer's account and
           their positions in the market. Unknown, PROPOSED or REJECTED:
           `NotFoundError` (404), before the viewer's rows are read. Every user has
           an account, because the profile transaction creates the two together;
           one that is missing is a broken database and raises `LookupError`.
        2. `quote = admit(viewer, market, <the positions>, query, clock.now())`: the
           proposer rule, the effective status, the viewer's holding for a sell, and
           the smallest size.
        3. A buy: `check_buy(<the positions>, <the balance>, quote)`, the stake limit
           and then the balance.
        4. `to_response(market, quote)`.

        These are steps 3, 4 and 6 of `TradingService.trade`, in its order and with
        its errors: 404, 403, 409 `MARKET_NOT_OPEN`, 409 `INSUFFICIENT_SHARES`, 422
        `TRADE_TOO_SMALL`, 409 `LIMIT_EXCEEDED`, 409 `INSUFFICIENT_BALANCE`, each
        with the message and the details the trade gives. A quote takes no slippage
        guard, counts against no rate limit and claims no key.

        Nothing is written, nothing is locked and nothing is cached: a quote comes
        from the quantities of this moment, and the trade decides again on the rows
        it locks. The statements of the transaction are not one snapshot, so a trade
        that commits between two of them shows in the next quote.
        """
        async with self._uow_factory() as uow:
            market = await uow.markets.get(market_id)
            if market is None or market.status in HIDDEN_STATUSES:
                raise NotFoundError
            account = await uow.accounts.get_user_account(viewer.id)
            positions = await uow.positions.list_for_user_market(viewer.id, market.id)
        if account is None:
            raise LookupError(f"user {viewer.id} has no ledger account")

        quote = self.admit(viewer, market, positions, query, self._clock.now())
        if quote.side is TradeSide.BUY:
            self.check_buy(positions, account.balance_micro, quote)
        return self.to_response(market, quote)

    def admit(
        self,
        trader: AuthenticatedUser,
        market: Market,
        positions: Sequence[Position],
        size: TradeSize,
        now: datetime,
    ) -> TradeQuote:
        """May `trader` make the trade `size` in `market` at `now`, and at what price
        (SPEC §8 step 4, A.4)? No I/O.

        `positions` are the trader's rows in the market, at most one per outcome. The
        first that applies, in this order:

        * `market.created_by == trader.id`: `ForbiddenError(OWN_MARKET_MESSAGE)` (403,
          self-dealing).
        * Effective status not OPEN, which includes `now >= close_at`:
          `MarketNotOpenError` (409).
        * A sell of more shares than the position of that outcome holds:
          `InsufficientSharesError` (409).
        * `price(market, size)`, which raises `TradeTooSmallError` (422) below the
          smallest size.

        Whether a buyer stays within the stake limit and has the balance is
        `check_buy`. A market that is PROPOSED or REJECTED is the caller's to refuse:
        to a trader it does not exist.
        """
        if market.created_by == trader.id:
            raise ForbiddenError(OWN_MARKET_MESSAGE)
        if not self._view.is_trading_open(market, now):
            raise MarketNotOpenError
        if size.side is TradeSide.SELL:
            held = position_of(positions, size.outcome)
            if held is None or held.shares_micro < _sell_size_micro(size):
                raise InsufficientSharesError
        return self.price(market, size)

    def check_buy(
        self, positions: Sequence[Position], balance_micro: int, quote: TradeQuote
    ) -> None:
        """The two checks of a buy, the stake limit and then the balance (SPEC §8
        step 6). No I/O.

        `positions` are the buyer's rows in the market before the trade and
        `balance_micro` is what their account holds.

        * `Σ cost - Σ proceeds + quote.amount_micro > MarketView.stake_limit_micro`:
          `LimitExceededError` (409) with `details={"limit":
          Limit.MAX_STAKE_PER_USER_MARKET, "stake_limit_cr": to_cr(...),
          "stake_left_cr": to_cr(...)}`, the stake left being the one before this
          trade, `MarketView.stake_left_micro(MarketView.net_stake_micro(...))`. That
          figure is never more than this check allows, so a buy for a stake preset
          the market's detail marked allowed passes it, and so does a buy for any
          position of its `buy_slider`.
        * `balance_micro < quote.amount_micro`: `InsufficientBalanceError` (409) with
          `details={"balance_cr": to_cr(...), "required_cr": to_cr(...)}`, the keys
          `LedgerService` uses.

        Sells are never limited. "The market's limits" of SPEC §8 are not checked
        here: the LMSR bounds what a market can lose, and the circuit breaker
        verifies after every trade that it did (A.4).
        """
        cost_micro = sum(position.cost_micro for position in positions)
        proceeds_micro = sum(position.proceeds_micro for position in positions)
        stake_limit_micro = self._view.stake_limit_micro
        if cost_micro - proceeds_micro + quote.amount_micro > stake_limit_micro:
            stake_left_micro = self._view.stake_left_micro(self._view.net_stake_micro(positions))
            raise LimitExceededError(
                details={
                    "limit": Limit.MAX_STAKE_PER_USER_MARKET,
                    "stake_limit_cr": to_cr(stake_limit_micro),
                    "stake_left_cr": to_cr(stake_left_micro),
                }
            )
        if balance_micro < quote.amount_micro:
            raise InsufficientBalanceError(
                details={
                    "balance_cr": to_cr(balance_micro),
                    "required_cr": to_cr(quote.amount_micro),
                }
            )

    def price(self, market: Market, size: TradeSize) -> TradeQuote:
        """Price `size` against the row's quantities with `app.pricing.lmsr`. No I/O.

        The state is `MarketState(q_yes_micro, q_no_micro, b_micro)`; every lmsr call
        gets `precision=PRICING_DECIMAL_PRECISION`. `size` has passed the request
        model, so exactly one of `amount_cr` and `shares` is set and a sell has shares.

        * BUY with `amount_cr`: below `MIN_TRADE_CR` raises `TradeTooSmallError` (422).
          `quote_buy_for_budget(state, outcome, to_micro(amount_cr))`. The quote costs
          exactly the budget and holds at least as many µshares as the budget has µCR
          (A.9), so there is no zero-share quote to reject and no branch for one.
        * BUY with `shares`: below `MIN_TRADE_SHARES` raises `TradeTooSmallError`.
          `quote_buy_shares(...)`.
        * SELL with `shares`: below `MIN_TRADE_SHARES` raises `TradeTooSmallError`.
          More than the market has sold of that outcome (`q_yes_micro` or
          `q_no_micro`) raises `InsufficientSharesError` (409): nobody can hold them,
          and lmsr must not see a negative quantity. `quote_sell_shares(...)`. Its
          proceeds may be 0; that is a quote like any other (A.4).

        Pass the row's integers and the request's `Outcome` as they are: lmsr raises
        `TypeError` for anything else and `ValueError` for a b its precision cannot
        cover, both of which are bugs here, not answers to a user. So is a size that
        got around its model and carries neither `amount_cr` nor `shares`: `ValueError`.

        This is about the market alone. Whether a trader holds the shares is `admit`,
        whether they have the balance and stay within the stake limit is `check_buy`.
        """
        state = lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)
        precision = self._pricing.decimal_precision

        if size.amount_cr is not None:
            budget_micro = to_micro(size.amount_cr)
            if budget_micro < self._pricing.min_trade_micro:
                raise TradeTooSmallError
            return lmsr.quote_buy_for_budget(state, size.outcome, budget_micro, precision=precision)

        if size.shares is None:
            raise ValueError("a trade is sized by amount_cr or by shares; this one by neither")
        shares_micro = to_micro(size.shares)
        if shares_micro < self._pricing.min_trade_shares_micro:
            raise TradeTooSmallError
        if size.side is TradeSide.BUY:
            return lmsr.quote_buy_shares(state, size.outcome, shares_micro, precision=precision)

        sold_micro = market.q_yes_micro if size.outcome is Outcome.YES else market.q_no_micro
        if shares_micro > sold_micro:
            raise InsufficientSharesError
        return lmsr.quote_sell_shares(state, size.outcome, shares_micro, precision=precision)

    def to_response(self, market: Market, quote: TradeQuote) -> QuoteResponse:
        """A `TradeQuote` as the API shows it. No I/O.

        `shares` and `amount_cr` through `to_cr`; `average_price =
        MarketView.average_price(amount_micro, shares_micro)`; the four probabilities
        from `MarketView.probabilities` of `p_yes_before_micro` and
        `p_yes_after_micro`; `quote_version = market.version`, read before the trade
        changes it. `details.quote` of `QUOTE_STALE` is this, `model_dump(mode="json")`.
        """
        p_yes_before, p_no_before = self._view.probabilities(quote.p_yes_before_micro)
        p_yes_after, p_no_after = self._view.probabilities(quote.p_yes_after_micro)
        return QuoteResponse(
            market_id=market.id,
            side=quote.side,
            outcome=quote.outcome,
            shares=to_cr(quote.shares_micro),
            amount_cr=to_cr(quote.amount_micro),
            average_price=self._view.average_price(quote.amount_micro, quote.shares_micro),
            p_yes_before=p_yes_before,
            p_no_before=p_no_before,
            p_yes_after=p_yes_after,
            p_no_after=p_no_after,
            quote_version=market.version,
        )


def position_of(positions: Sequence[Position], outcome: Outcome) -> Position | None:
    """The row of `outcome` among a user's positions in one market, if there is one."""
    for position in positions:
        if position.outcome is outcome:
            return position
    return None


def _sell_size_micro(size: TradeSize) -> int:
    """µshares a sell asks for. The request model sizes every sell by `shares`."""
    if size.shares is None:
        raise ValueError("a sell is sized by shares; this one has none")
    return to_micro(size.shares)
