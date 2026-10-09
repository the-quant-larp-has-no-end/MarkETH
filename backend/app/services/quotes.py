"""Pricing a trade without executing it (SPEC §4, §10, Appendix A.4, A.9).

`quote` serves `GET /markets/{id}/quote`. `price` and `to_response` are the same
computation on a market row the caller already holds; `TradingService` uses them on the
locked row, so a trade is priced exactly the way its quote was.
"""

from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.models.market import Market
from app.pricing.lmsr import TradeQuote
from app.schemas.trading import QuoteQuery, QuoteResponse, TradeSize
from app.services import UowFactory
from app.services.market_view import MarketView


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

    async def quote(self, market_id: UUID, query: QuoteQuery) -> QuoteResponse:
        """What a trade would cost or return right now (`GET /markets/{id}/quote`).

        Public and about nobody: it checks neither a balance nor a stake limit, and a
        sell is priced whether or not anyone holds the shares.

        1. Read the market without a lock. Unknown, PROPOSED or REJECTED:
           `NotFoundError` (404).
        2. Effective status not OPEN: `MarketNotOpenError` (409).
        3. `to_response(market, price(market, query))`.

        Not cached: a quote must come from the current quantities.
        """
        raise NotImplementedError

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
        cover, both of which are bugs here, not answers to a user.

        Whether this trader holds the shares, has the balance or stays within the
        stake limit is the trading service's check.
        """
        raise NotImplementedError

    def to_response(self, market: Market, quote: TradeQuote) -> QuoteResponse:
        """A `TradeQuote` as the API shows it. No I/O.

        `shares` and `amount_cr` through `to_cr`; `average_price =
        MarketView.average_price(amount_micro, shares_micro)`; the four probabilities
        from `MarketView.probabilities` of `p_yes_before_micro` and
        `p_yes_after_micro`; `quote_version = market.version`, read before the trade
        changes it. `details.quote` of `QUOTE_STALE` is this, `model_dump(mode="json")`.
        """
        raise NotImplementedError
