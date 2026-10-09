"""The trade transaction (SPEC §8, with every rule of Appendix A.4).

A trade is one Postgres transaction that locks the market row first, so trades on one
market run one after another on fresh quantities. Postgres decides everything; Redis
only limits the rate before the transaction and spreads the news after it.
"""

from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.schemas.trading import TradeRequest, TradeResponse
from app.services import UowFactory
from app.services.audit import AuditService
from app.services.invariants import InvariantChecker
from app.services.ledger import LedgerService
from app.services.live_quotes import LiveQuoteService
from app.services.market_view import MarketView
from app.services.quotes import QuoteService

ORDER_WINDOW_SECONDS: Final[int] = 60
"""`MAX_ORDERS_PER_MINUTE` counts per minute."""


class TradingService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        quotes: QuoteService,
        ledger: LedgerService,
        invariants: InvariantChecker,
        audit: AuditService,
        rate_limiter: RateLimiter,
        fallback_rate_limiter: RateLimiter,
        cache: MarketCache,
        live: LiveQuoteService,
    ) -> None:
        self._uow_factory = uow_factory
        self._pricing = settings.pricing
        self._clock = clock
        self._view = view
        self._quotes = quotes
        self._ledger = ledger
        self._invariants = invariants
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._fallback_rate_limiter = fallback_rate_limiter
        self._cache = cache
        self._live = live
        self._order_limit = RateLimit(
            "trade-user", settings.risk.max_orders_per_minute, ORDER_WINDOW_SECONDS
        )

    async def trade(
        self,
        user: AuthenticatedUser,
        market_id: UUID,
        idempotency_key: UUID,
        request: TradeRequest,
    ) -> TradeResponse:
        """Execute one buy or sell (`POST /markets/{id}/trades`).

        The numbers are the steps of SPEC §8. The route has authenticated the user and
        validated the body and the Idempotency-Key header.

        1. Rate limit. Hit `trade-user` with subject `str(user.id)` on the Redis
           limiter; on `RedisUnavailableError` hit the in-process limiter with the same
           rule instead (A.4). A rejected hit raises `RateLimitedError(
           retry_after_seconds=...)` (429). Every request counts, replays included.

        2. BEGIN, then claim the key. The request hash is
           `sha256_hex(json.dumps({"market_id": str(market_id), **request.model_dump(
           mode="json")}, sort_keys=True, separators=(",", ":")))`; the column accepts
           nothing but a SHA-256.
           `idempotency.claim(user.id, str(idempotency_key), request_hash, now)`:
           * None: the key is new and this transaction owns it.
           * A row with another `request_hash`: `IdempotencyKeyReusedError` (422).
           * A row with the same hash: return `TradeResponse.model_validate(
             row.response_json)` and do nothing else.

        3. Lock the market row (`markets.get_for_update`), then the user's account
           (`accounts.get_user_account(user.id, for_update=True)`), in this order
           always. An unknown, PROPOSED or REJECTED market raises `NotFoundError` (404).

        4. May this trade happen at all?
           * `market.created_by == user.id`: `ForbiddenError` (403, self-dealing).
           * Effective status not OPEN, which includes `now >= close_at`:
             `MarketNotOpenError` (409).
           * A sell of more than the user's position in that outcome holds
             (`positions.get(..., for_update=True)`): `InsufficientSharesError` (409).
           Then `quote = QuoteService.price(market, request)` on the locked row, which
           raises `TradeTooSmallError` (422) below the minimum size.

        5. Slippage guard, when the request carries one. BUY by `amount_cr`:
           `quote.shares_micro < to_micro(min_shares)`. BUY by `shares`:
           `quote.amount_micro > to_micro(max_cost_cr)`. SELL: `quote.amount_micro <
           to_micro(min_proceeds_cr)`. Each raises `QuoteStaleError` (409) with
           `details={"quote": QuoteService.to_response(market, quote).model_dump(
           mode="json")}`.

        6. Buys only, two checks. With the user's positions in this market
           (`positions.list_for_user_market`): if `Σ cost - Σ proceeds +
           quote.amount_micro > MAX_STAKE_PER_USER_MARKET`, `LimitExceededError` (409)
           with `details={"limit": Limit.MAX_STAKE_PER_USER_MARKET, "stake_limit_cr":
           to_cr(...), "stake_left_cr": to_cr(...)}`, the stake left being the one
           before this trade. Then, if the account's balance is below
           `quote.amount_micro`, `InsufficientBalanceError` (409) with
           `details={"balance_cr": to_cr(...), "required_cr": to_cr(...)}`, the keys
           `LedgerService` uses. Sells are never limited. "The market's limits" of
           SPEC §8 are not checked here: the LMSR bounds what a market can lose, and
           step 8 verifies that it did (A.4).

        7. Write, with `trade_id = uuid7()`:
           * Ledger. BUY: `TRADE_BUY`, user -cost, market account +cost. SELL:
             `TRADE_SELL`, market account -proceeds, user +proceeds. Both with
             `LedgerRef.TRADE`, `trade_id` and `trade_key(trade_id)`. The market
             account is `accounts.get_market_account(market.id)`. A sell whose
             proceeds are 0 (shares priced below a millionth of a credit round down
             to nothing) books no entry at all: do not call `LedgerService.post`,
             which refuses an entry without postings. Everything below is still
             written, with `amount_micro = 0` (A.4). A buy always costs at least 1 µCR.
           * Position (insert it if missing). BUY: `shares_micro += shares`,
             `cost_micro += amount`. SELL: `shares_micro -= shares`,
             `proceeds_micro += amount`.
           * Market: `q_yes_micro` or `q_no_micro` to the quote's `after` state,
             `version += 1`, `volume_micro += amount`.
           * `Trade(id=trade_id, market_id, user_id, side, outcome, shares_micro,
             amount_micro, p_yes_before_micro, p_yes_after_micro,
             market_version=<the new version>, created_at=now)`.
           * Outbox: `quote_updated_payload(QuotePoint(market.id, now, <new version>,
             quote.p_yes_after_micro, q_yes_micro, q_no_micro, trade_id))` under
             `OutboxTopic.QUOTE_UPDATED`.

        8. `InvariantChecker.check(uow, market)`: the four checks of the circuit
           breaker, the market's loss bound among them. Build the `TradeResponse` (the
           quote's figures as executed, `market_version`, the balance after the trade,
           the net stake and `stake_left_cr` after it, `created_at=now`) and store
           `response.model_dump(mode="json")` with `idempotency.store_response`. COMMIT.

        9. After the commit, best effort and never failing the request:
           `MarketCache.invalidate_market(market.id)`, then
           `LiveQuoteService.publish_quote(market)`.

        Any error in steps 2 to 8 rolls the transaction back, the claimed key
        included, so the client may retry with the same key.

        Circuit breaker (SPEC §5): `InvariantViolationError` leaves the transaction,
        which rolls back. Catch it outside the block and, in a second transaction:
        lock the market row; if it is stored OPEN set it HALTED; insert `Alert(
        severity=CRITICAL, message=f"Invariant violated: {invariant}", context=
        <the error's context plus market_id, user_id and trade request>, created_at=
        now)`; audit `MARKET_HALT` with `actor_id=None`. After that commit invalidate
        the cache and `publish_status`, then raise `ServiceUnavailableError` (503).
        """
        raise NotImplementedError
