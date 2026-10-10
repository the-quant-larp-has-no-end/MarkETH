"""The trade transaction (SPEC §8, with every rule of Appendix A.4).

A trade is one Postgres transaction that locks the market row first, so trades on one
market run one after another on fresh quantities. Postgres decides everything; Redis
only limits the rate before the transaction and spreads the news after it.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import (
    AlertSeverity,
    JournalKind,
    MarketStatus,
    OutboxTopic,
    Outcome,
    TradeSide,
)
from app.core.errors import (
    ForbiddenError,
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    InsufficientSharesError,
    InvariantViolationError,
    LimitExceededError,
    MarketNotOpenError,
    NotFoundError,
    QuoteStaleError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.core.identity import AuthenticatedUser
from app.core.security import sha256_hex
from app.core.units import to_cr, to_micro
from app.integrations.redis.client import RedisUnavailableError
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.integrations.scylla.quote_history import QuotePoint
from app.models.ledger import Account
from app.models.market import Market
from app.models.ops import Alert
from app.models.trading import Position, Trade
from app.pricing.lmsr import TradeQuote
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.common import Limit
from app.schemas.trading import TradeRequest, TradeResponse
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.invariants import InvariantChecker
from app.services.ledger import LedgerRef, LedgerService, PostingLine, trade_key
from app.services.live_quotes import LiveQuoteService
from app.services.market_view import HIDDEN_STATUSES, MarketView
from app.services.outbox_events import quote_updated_payload
from app.services.quotes import QuoteService

logger = logging.getLogger(__name__)

ORDER_WINDOW_SECONDS: Final[int] = 60
"""`MAX_ORDERS_PER_MINUTE` counts per minute."""

OWN_MARKET_MESSAGE: Final[str] = "You proposed this market, so you cannot trade it."
"""Message of the `ForbiddenError` a trade by the market's proposer gets (A.4).
`MarketViewer.cannot_trade_reason` says `OWN_MARKET` to that user beforehand."""


@dataclass(frozen=True, slots=True)
class _Executed:
    """What the trade transaction answers with, and what it left to announce."""

    response: TradeResponse
    market: Market | None
    """The committed market row of a trade that executed. None when the response is
    the stored one of an earlier request: nothing was written, nothing is announced."""


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
           `idempotency.claim(user.id, str(idempotency_key), request_hash,
           clock.now())`:
           * None: the key is new and this transaction owns it.
           * A row with another `request_hash`: `IdempotencyKeyReusedError` (422).
           * A row with the same hash: return `TradeResponse.model_validate(
             row.response_json)` and do nothing else.

        3. Lock the market row (`markets.get_for_update`). An unknown, PROPOSED or
           REJECTED market raises `NotFoundError` (404). Then read the clock: `now =
           clock.now()`, taken after the lock was granted, is the instant of this
           trade everywhere below (the deadline of step 4, the ledger entry,
           `Trade.created_at`, the outbox point, the response). A trade that waited
           for the row until `close_at` had passed is therefore refused, and the
           timestamps of a market's trades never run against their versions. Then
           lock the user's account (`accounts.get_user_account(user.id,
           for_update=True)`): market row first, account second, always. Every user
           has an account, because the profile transaction creates the two together.

        4. May this trade happen at all?
           * `market.created_by == user.id`: `ForbiddenError(OWN_MARKET_MESSAGE)`
             (403, self-dealing).
           * Effective status not OPEN, which includes `now >= close_at`:
             `MarketNotOpenError` (409).
           * Read the user's positions in this market once:
             `positions.list_for_user_market(user.id, market.id)`, without a lock of
             its own, because nobody writes a market's positions without holding its
             row. The same rows serve the check here, the stake check of step 6 and
             the write of step 7. A sell of more shares than the position of that
             outcome holds: `InsufficientSharesError` (409).
           Then `quote = QuoteService.price(market, request)` on the locked row, which
           raises `TradeTooSmallError` (422) below the minimum size.

        5. Slippage guard, when the request carries one. BUY by `amount_cr`:
           `quote.shares_micro < to_micro(min_shares)`. BUY by `shares`:
           `quote.amount_micro > to_micro(max_cost_cr)`. SELL: `quote.amount_micro <
           to_micro(min_proceeds_cr)`. Each raises `QuoteStaleError` (409) with
           `details={"quote": QuoteService.to_response(market, quote).model_dump(
           mode="json")}`.

        6. Buys only, two checks. With the positions of step 4: if `Σ cost - Σ
           proceeds + quote.amount_micro > MarketView.stake_limit_micro`,
           `LimitExceededError` (409) with `details={"limit":
           Limit.MAX_STAKE_PER_USER_MARKET, "stake_limit_cr": to_cr(...),
           "stake_left_cr": to_cr(...)}`, the stake left being the one before this
           trade, `MarketView.stake_left_micro(MarketView.net_stake_micro(...))`. That
           figure is never more than this check allows, so a buy for a stake preset
           the market's detail marked allowed passes it. Then, if the account's
           balance is below `quote.amount_micro`, `InsufficientBalanceError` (409)
           with `details={"balance_cr": to_cr(...), "required_cr": to_cr(...)}`, the
           keys `LedgerService` uses. Sells are never limited. "The market's limits"
           of SPEC §8 are not checked here: the LMSR bounds what a market can lose,
           and step 8 verifies that it did (A.4).

        7. Write, with `trade_id = uuid7()`:
           * Ledger. BUY: `TRADE_BUY`, user -cost, market account +cost. SELL:
             `TRADE_SELL`, market account -proceeds, user +proceeds. Both with
             `LedgerRef.TRADE`, `trade_id`, `trade_key(trade_id)` and `at=now`. The
             market account is `accounts.get_market_account(market.id)`. A sell whose
             proceeds are 0 (shares priced below a millionth of a credit round down
             to nothing) books no entry at all: do not call `LedgerService.post`,
             which refuses an entry without postings. Everything below is still
             written, with `amount_micro = 0` (A.4). A buy always costs at least 1 µCR.
           * Position (insert it if missing). BUY: `shares_micro += shares`,
             `cost_micro += amount`. SELL: `shares_micro -= shares`,
             `proceeds_micro += amount`. These two running sums and the shares are
             what the portfolio, a void and the leaderboard read.
           * Market: `q_yes_micro` or `q_no_micro` to the quote's `after` state,
             `version += 1`, `volume_micro += amount`.
           * `Trade(id=trade_id, market_id, user_id, side, outcome, shares_micro,
             amount_micro, p_yes_before_micro, p_yes_after_micro,
             market_version=<the new version>, created_at=now)`: exactly one row per
             executed trade, also for a sell that brought in nothing.
           * Outbox: `quote_updated_payload(QuotePoint(market.id, now, <new version>,
             quote.p_yes_after_micro, q_yes_micro, q_no_micro, trade_id))` under
             `OutboxTopic.QUOTE_UPDATED`.

        8. `InvariantChecker.check(uow, market)`: the four checks of the circuit
           breaker, the market's loss bound among them. Build the `TradeResponse` (the
           quote's figures as executed, which are those of `QuoteService.to_response`
           for this quote, `market_version`, the balance after the trade, the net
           stake and `stake_left_cr` after it, `created_at=now`) and store
           `response.model_dump(mode="json")` with `idempotency.store_response`. COMMIT.

        9. After the commit, best effort and never failing the request:
           `MarketCache.invalidate_lists()`, then
           `LiveQuoteService.publish_quote(market)`.

        The checks run in the order of the steps, so an answer names the first thing
        that is wrong: 429, 422 `IDEMPOTENCY_KEY_REUSED`, 404, 403, 409
        `MARKET_NOT_OPEN`, 409 `INSUFFICIENT_SHARES`, 422 `TRADE_TOO_SMALL`, 409
        `QUOTE_STALE`, 409 `LIMIT_EXCEEDED`, 409 `INSUFFICIENT_BALANCE`.

        Any error in steps 2 to 8 rolls the transaction back, the claimed key
        included, so the client may retry with the same key.

        Circuit breaker (SPEC §5): `InvariantViolationError` leaves the transaction,
        which rolls back. Catch it outside the block and, in a second transaction:
        lock the market row and read the clock; if the row is stored OPEN, set it
        HALTED and audit `MARKET_HALT` with `actor_id=None` and the status before and
        after; a market somebody else already took out of OPEN is left as it is and
        gets no audit row, because nothing was halted. In either case insert
        `Alert(severity=CRITICAL, message=f"Invariant violated: {invariant}",
        context=<the error's context plus market_id, user_id and the trade request
        as `request.model_dump(mode="json")`>, created_at=now)`. After that commit
        `MarketCache.invalidate_lists()` and `LiveQuoteService.publish_status` with
        the row just read, then raise `ServiceUnavailableError` (503). The violation
        is also logged as an error. A constraint the database itself refuses
        (`IntegrityError`) is not caught here: it is a bug and a 500.
        """
        await self._count_order(user)
        try:
            executed = await self._execute(user, market_id, idempotency_key, request)
        except InvariantViolationError as violation:
            await self._trip_breaker(user, market_id, request, violation)
            raise ServiceUnavailableError from violation
        if executed.market is not None:
            await self._cache.invalidate_lists()
            await self._live.publish_quote(executed.market)
        return executed.response

    async def _count_order(self, user: AuthenticatedUser) -> None:
        """Step 1: count this request against `MAX_ORDERS_PER_MINUTE`, in Redis, or in
        this process while Redis cannot be reached."""
        subject = str(user.id)
        try:
            decision = await self._rate_limiter.hit(self._order_limit, subject)
        except RedisUnavailableError:
            decision = await self._fallback_rate_limiter.hit(self._order_limit, subject)
        if not decision.allowed:
            raise RateLimitedError(retry_after_seconds=decision.retry_after_seconds)

    async def _execute(
        self,
        user: AuthenticatedUser,
        market_id: UUID,
        idempotency_key: UUID,
        request: TradeRequest,
    ) -> _Executed:
        """Steps 2 to 8: the one transaction of a trade.

        Leaves through an exception with everything rolled back, the claimed key
        included, or with the trade committed.
        """
        key = str(idempotency_key)
        request_hash = _request_hash(market_id, request)
        async with self._uow_factory() as uow:
            stored = await uow.idempotency.claim(user.id, key, request_hash, self._clock.now())
            if stored is not None:
                if stored.request_hash != request_hash:
                    raise IdempotencyKeyReusedError
                return _Executed(TradeResponse.model_validate(stored.response_json), market=None)

            market = await uow.markets.get_for_update(market_id)
            if market is None or market.status in HIDDEN_STATUSES:
                raise NotFoundError
            now = self._clock.now()
            account = await uow.accounts.get_user_account(user.id, for_update=True)
            if account is None:
                raise LookupError(f"user {user.id} has no ledger account")

            if market.created_by == user.id:
                raise ForbiddenError(OWN_MARKET_MESSAGE)
            if not self._view.is_trading_open(market, now):
                raise MarketNotOpenError
            positions = list(await uow.positions.list_for_user_market(user.id, market.id))
            if request.side is TradeSide.SELL:
                held = _position_of(positions, request.outcome)
                if held is None or held.shares_micro < _sell_size_micro(request):
                    raise InsufficientSharesError
            quote = self._quotes.price(market, request)
            # Made of the row before the trade changes it: these are the figures the
            # quote endpoint gives for the same trade, and the ones the response repeats.
            quoted = self._quotes.to_response(market, quote)

            if _moved_past_guard(request, quote):
                raise QuoteStaleError(details={"quote": quoted.model_dump(mode="json")})
            if quote.side is TradeSide.BUY:
                self._check_buy(positions, account, quote)

            trade_id = uuid7()
            await self._book(uow, market, account, quote, trade_id, now)
            position = _position_of(positions, quote.outcome)
            first_of_its_outcome = position is None
            if position is None:
                position = Position(
                    user_id=user.id,
                    market_id=market.id,
                    outcome=quote.outcome,
                    shares_micro=0,
                    cost_micro=0,
                    proceeds_micro=0,
                )
            if quote.side is TradeSide.BUY:
                position.shares_micro += quote.shares_micro
                position.cost_micro += quote.amount_micro
            else:
                position.shares_micro -= quote.shares_micro
                position.proceeds_micro += quote.amount_micro
            if first_of_its_outcome:
                # Inserted with this trade in it: one statement under the market lock.
                positions.append(await uow.positions.add(position))
            market.q_yes_micro = quote.after.q_yes
            market.q_no_micro = quote.after.q_no
            market.version += 1
            market.volume_micro += quote.amount_micro
            await uow.trades.add(
                Trade(
                    id=trade_id,
                    market_id=market.id,
                    user_id=user.id,
                    side=quote.side,
                    outcome=quote.outcome,
                    shares_micro=quote.shares_micro,
                    amount_micro=quote.amount_micro,
                    p_yes_before_micro=quote.p_yes_before_micro,
                    p_yes_after_micro=quote.p_yes_after_micro,
                    market_version=market.version,
                    created_at=now,
                )
            )
            point = QuotePoint(
                market_id=market.id,
                ts=now,
                version=market.version,
                p_yes_micro=quote.p_yes_after_micro,
                q_yes_micro=market.q_yes_micro,
                q_no_micro=market.q_no_micro,
                trade_id=trade_id,
            )
            await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, quote_updated_payload(point), now)

            await self._invariants.check(uow, market)
            net_stake_micro = self._view.net_stake_micro(positions)
            response = TradeResponse(
                trade_id=trade_id,
                market_id=quoted.market_id,
                side=quoted.side,
                outcome=quoted.outcome,
                shares=quoted.shares,
                amount_cr=quoted.amount_cr,
                average_price=quoted.average_price,
                p_yes_before=quoted.p_yes_before,
                p_no_before=quoted.p_no_before,
                p_yes_after=quoted.p_yes_after,
                p_no_after=quoted.p_no_after,
                market_version=market.version,
                # `LedgerService.post` read the account again after it posted.
                balance_cr=to_cr(account.balance_micro),
                net_stake_cr=to_cr(net_stake_micro),
                stake_left_cr=to_cr(self._view.stake_left_micro(net_stake_micro)),
                created_at=now,
            )
            await uow.idempotency.store_response(user.id, key, response.model_dump(mode="json"))
        return _Executed(response, market)

    def _check_buy(
        self, positions: Sequence[Position], account: Account, quote: TradeQuote
    ) -> None:
        """Step 6: the stake limit, then the balance. `positions` are the buyer's rows
        in the market before the trade, `account` is their locked account."""
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
        if account.balance_micro < quote.amount_micro:
            raise InsufficientBalanceError(
                details={
                    "balance_cr": to_cr(account.balance_micro),
                    "required_cr": to_cr(quote.amount_micro),
                }
            )

    async def _book(
        self,
        uow: UnitOfWork,
        market: Market,
        account: Account,
        quote: TradeQuote,
        trade_id: UUID,
        now: datetime,
    ) -> None:
        """The journal entry of a trade (A.8): the cost of a buy from the trader to the
        market's account, the proceeds of a sell the other way. A sell that brings in
        nothing books no entry."""
        if quote.amount_micro == 0:
            return
        market_account = await uow.accounts.get_market_account(market.id)
        if market_account is None:
            raise LookupError(f"market {market.id} has no ledger account")
        if quote.side is TradeSide.BUY:
            kind = JournalKind.TRADE_BUY
            payer, payee = account, market_account
        else:
            kind = JournalKind.TRADE_SELL
            payer, payee = market_account, account
        await self._ledger.post(
            uow,
            kind=kind,
            ref_type=LedgerRef.TRADE,
            ref_id=trade_id,
            idempotency_key=trade_key(trade_id),
            lines=[
                PostingLine(payer.id, -quote.amount_micro),
                PostingLine(payee.id, quote.amount_micro),
            ],
            at=now,
        )

    async def _trip_breaker(
        self,
        user: AuthenticatedUser,
        market_id: UUID,
        request: TradeRequest,
        violation: InvariantViolationError,
    ) -> None:
        """The circuit breaker's own transaction, after the trade's was rolled back:
        halt the market if it is still open, and leave an alert for the admins."""
        logger.error(
            "A trade on market %s broke the invariant %s and was rolled back: %s",
            market_id,
            violation.invariant,
            violation.context,
        )
        async with self._uow_factory() as uow:
            market = await uow.markets.get_for_update(market_id)
            if market is None:
                raise LookupError(f"market {market_id} vanished while a trade on it was checked")
            now = self._clock.now()
            if market.status is MarketStatus.OPEN:
                market.status = MarketStatus.HALTED
                await self._audit.record(
                    uow,
                    actor_id=None,
                    action=AuditAction.MARKET_HALT,
                    target=AuditService.target(AuditTarget.MARKET, market.id),
                    before={"status": MarketStatus.OPEN.value},
                    after={"status": MarketStatus.HALTED.value},
                )
            await uow.alerts.add(
                Alert(
                    severity=AlertSeverity.CRITICAL,
                    message=f"Invariant violated: {violation.invariant}",
                    context={
                        **violation.context,
                        "market_id": str(market_id),
                        "user_id": str(user.id),
                        "request": request.model_dump(mode="json"),
                    },
                    created_at=now,
                )
            )
        await self._cache.invalidate_lists()
        await self._live.publish_status(market)


def _request_hash(market_id: UUID, request: TradeRequest) -> str:
    """What makes two requests under one Idempotency-Key the same request (A.4): the
    market and the body. Amounts are dumped in their one API form, so `"25"` and
    `"25.000000"` are one request."""
    body = {"market_id": str(market_id), **request.model_dump(mode="json")}
    return sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")))


def _position_of(positions: Sequence[Position], outcome: Outcome) -> Position | None:
    """The row of `outcome` among a user's positions in one market, if there is one."""
    for position in positions:
        if position.outcome is outcome:
            return position
    return None


def _sell_size_micro(request: TradeRequest) -> int:
    """µshares a sell asks for. The request model sizes every sell by `shares`."""
    if request.shares is None:
        raise ValueError("a sell is sized by shares; this one has none")
    return to_micro(request.shares)


def _moved_past_guard(request: TradeRequest, quote: TradeQuote) -> bool:
    """Step 5: whether the price moved past the slippage guard of the request. The
    request model lets a request carry the guard of its own mode and no other (A.4),
    so the one that is set is the one that applies."""
    if request.min_shares is not None:
        return quote.shares_micro < to_micro(request.min_shares)
    if request.max_cost_cr is not None:
        return quote.amount_micro > to_micro(request.max_cost_cr)
    if request.min_proceeds_cr is not None:
        return quote.amount_micro < to_micro(request.min_proceeds_cr)
    return False
