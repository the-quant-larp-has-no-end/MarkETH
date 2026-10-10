"""The admin actions that move a market through its life (SPEC §5, §6, A.4, A.5, A.8).

    PROPOSED -> OPEN (approve) | REJECTED (reject)
    OPEN     -> HALTED (halt)  | CLOSED (close, or `close_at` reached)
    HALTED   -> OPEN (resume)  | CLOSED (close)
    CLOSED   -> RESOLVED (resolve YES or NO) | VOID (void)

Shared by all seven methods:

* Only an admin acts. An `actor` for whom `actor.is_admin` is false raises
  `ForbiddenError` (403) before anything is read. The routes have a guard of their
  own; the rule also holds for whoever else calls this service.
* A text of the request with a NUL character in it (a reason, a note, the evidence, an
  edited wording) raises `ValidationFailedError` (422) naming its field, also before
  anything is read: Postgres stores no such text (`markets.refuse_nul`).
* One transaction. The market row is locked first (`markets.get_for_update`); an unknown
  market raises `NotFoundError` (404).
* `now = clock.now()` is read after that lock was granted and is the instant of the
  action everywhere below. An action that waited for the row is dated when it happened,
  and a deadline is judged at that moment, not when the request arrived.
* A move that `ALLOWED_TRANSITIONS` does not list raises `ConflictError` (409).
* Lock order: the market row, then the user accounts a settlement pays (all at once,
  `accounts.lock_user_accounts`), then whatever `LedgerService.post` locks. Positions
  need no lock of their own: every writer of a market's positions holds its row.
* One `audit_log` row per status change, in the transaction, with `actor_id=actor.id`,
  target `AuditService.target(AuditTarget.MARKET, market.id)`, and `before` and `after`
  holding at least `{"status": ...}` plus what the action changed or was told (tier,
  budget, a reason, the outcome).
* After the commit, best effort: `MarketCache.invalidate_lists()` and
  `LiveQuoteService.publish_status(market)`. `reject` does neither: a proposal was never
  in a list and nobody is watching it.
* The result is the market's detail afterwards: `MarketService.detail(actor, market.id)`.

Construction checks that the liquidity settings can work, and raises `ValueError` if
they cannot, so the api and the relay do not start on them: the b of every fixed tier
and `lmsr.b_from_budget_micro(MAX_MARKET_BUDGET)` must be quotable
(`lmsr.is_quotable(b, precision=PRICING_DECIMAL_PRECISION)`), and the loss bound of
every fixed tier (`lmsr.max_loss_micro`) must fit `MAX_MARKET_BUDGET`. Without the first
an admin could open a market nobody can trade; without the second a tier could never be
approved. `check_liquidity_settings` is that check.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import (
    AccountKind,
    JournalKind,
    MarketStatus,
    MarketTier,
    OutboxTopic,
    ResolutionOutcome,
)
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import format_cr, to_micro
from app.integrations.redis.market_cache import MarketCache
from app.integrations.scylla.quote_history import QuotePoint
from app.models.market import Market, Resolution
from app.models.trading import Position
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.common import Limit
from app.schemas.markets import MarketDetailResponse
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.ledger import LedgerRef, LedgerService, PostingLine, resolution_key, void_key
from app.services.live_quotes import LiveQuoteService
from app.services.market_view import EVEN_ODDS_MICRO, MarketView
from app.services.markets import MarketService, check_market_dates, refuse_nul
from app.services.outbox_events import quote_updated_payload
from app.services.public_config import FIXED_TIERS
from app.services.risk import CAPPED_STATUSES

ALLOWED_TRANSITIONS: Final[Mapping[MarketStatus, frozenset[MarketStatus]]] = {
    MarketStatus.PROPOSED: frozenset({MarketStatus.OPEN, MarketStatus.REJECTED}),
    MarketStatus.REJECTED: frozenset(),
    MarketStatus.OPEN: frozenset({MarketStatus.HALTED, MarketStatus.CLOSED}),
    MarketStatus.HALTED: frozenset({MarketStatus.OPEN, MarketStatus.CLOSED}),
    MarketStatus.CLOSED: frozenset({MarketStatus.RESOLVED, MarketStatus.VOID}),
    MarketStatus.RESOLVED: frozenset(),
    MarketStatus.VOID: frozenset(),
}
"""The state machine of SPEC §6, on stored statuses: from a status, where it may go."""

APPROVAL_LOCK: Final[str] = "market-approval"
"""Name of the advisory lock that makes approvals check the caps one at a time (A.5)."""

SELF_DEALING_MESSAGE: Final[str] = "You traded this market, so another admin has to settle it."
"""Message of the `ForbiddenError` that `resolve` and `void` answer an admin with who
has a trade in the market (A.4). `MarketViewer.can_settle` is false for that admin."""

CLOSE_FIRST_MESSAGE: Final[str] = "Trading is still open in this market. Close it first."
"""Message of the `ConflictError` that `resolve` and `void` answer for an OPEN market
whose `close_at` has not come yet (A.4)."""

BUDGET_LIMIT_MESSAGE: Final[str] = "This budget is above the most a market may have."
"""Message of the `LimitExceededError` of `approve` for `Limit.MAX_MARKET_BUDGET`."""

OPEN_MARKETS_LIMIT_MESSAGE: Final[str] = (
    "The limit of open markets is reached. Close one before you open another."
)
"""Message of the `LimitExceededError` of `approve` for `Limit.MAX_OPEN_MARKETS`."""

LOSS_BUDGET_LIMIT_MESSAGE: Final[str] = (
    "With this market, the open markets could lose more than their limit in total."
)
"""Message of the `LimitExceededError` of `approve` for
`Limit.MAX_AGGREGATE_LOSS_BUDGET`."""


def check_liquidity_settings(settings: Settings) -> None:
    """Refuse settings on which a market could not be opened or not be traded.

    Raises `ValueError`, naming the variable of `.env` that is wrong, unless

    * `MAX_MARKET_BUDGET` is above zero and the b it gives a CUSTOM market,
      `lmsr.b_from_budget_micro(MAX_MARKET_BUDGET)`, is one the quote functions take
      (`lmsr.is_quotable`). A smaller budget gives a smaller b, so every budget an
      approval lets through is then quotable too;
    * the b of every fixed tier is above zero and quotable, and its loss bound
      (`lmsr.max_loss_micro`), which is the budget approval gives the tier, fits
      `MAX_MARKET_BUDGET`.

    All at `PRICING_DECIMAL_PRECISION` digits. `MarketLifecycleService` calls this when
    it is built, so neither the api nor the relay starts on settings that fail it.
    """
    pricing = settings.pricing
    precision = pricing.decimal_precision
    max_budget_micro = settings.risk.max_market_budget_micro
    if max_budget_micro <= 0:
        raise ValueError("MAX_MARKET_BUDGET must be above zero")
    largest_b_micro = lmsr.b_from_budget_micro(max_budget_micro, precision=precision)
    if not lmsr.is_quotable(largest_b_micro, precision=precision):
        raise ValueError(
            f"MAX_MARKET_BUDGET = {format_cr(max_budget_micro)} CR lets a market have "
            f"b = {format_cr(largest_b_micro)} CR, which PRICING_DECIMAL_PRECISION = "
            f"{precision} cannot price"
        )
    for tier in FIXED_TIERS:
        variable = f"LMSR_TIER_{tier.value}_B"
        b_micro = pricing.tier_b_micro(tier)
        if b_micro <= 0:
            raise ValueError(f"{variable} must be above zero")
        if not lmsr.is_quotable(b_micro, precision=precision):
            raise ValueError(
                f"{variable} = {format_cr(b_micro)} CR cannot be priced with "
                f"PRICING_DECIMAL_PRECISION = {precision}"
            )
        loss_micro = lmsr.max_loss_micro(b_micro, precision=precision)
        if loss_micro > max_budget_micro:
            raise ValueError(
                f"{variable} = {format_cr(b_micro)} CR can lose {format_cr(loss_micro)} CR, "
                f"more than MAX_MARKET_BUDGET = {format_cr(max_budget_micro)} CR: the tier "
                "could never be approved"
            )


class MarketLifecycleService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        ledger: LedgerService,
        audit: AuditService,
        markets: MarketService,
        cache: MarketCache,
        live: LiveQuoteService,
    ) -> None:
        check_liquidity_settings(settings)
        self._uow_factory = uow_factory
        self._settings = settings
        self._clock = clock
        self._view = view
        self._ledger = ledger
        self._audit = audit
        self._markets = markets
        self._cache = cache
        self._live = live

    async def approve(
        self, actor: AuthenticatedUser, market_id: UUID, request: ApproveMarketRequest
    ) -> MarketDetailResponse:
        """Open a proposed market in one atomic step (SPEC §6, A.5). PROPOSED -> OPEN.

        After locking the market row:
        1. `uow.advisory_lock(APPROVAL_LOCK)`, so two approvals cannot both pass a cap.
           Taking it while holding the market row cannot deadlock: nothing that holds
           this advisory lock waits for a market row. A market that is not PROPOSED
           is refused before it, and the clock is read once it is granted: an approval
           that waited for another one opens its market at that moment.
        2. Apply the edits the request carries (wording, `close_at`, `resolve_by`).
        3. Dates, on the edited values, each a `ValidationFailedError` (422) naming the
           field in `details.field`: `now < close_at <= now + MAX_MARKET_LIFETIME` and
           `close_at <= resolve_by <= close_at + MAX_RESOLUTION_DELAY_DAYS`
           (`markets.check_market_dates`, the rules a proposal is held to).
        4. Budget and b. A fixed tier: `b = settings.pricing.tier_b_micro(tier)`,
           `budget = lmsr.max_loss_micro(b)`. CUSTOM: `budget = to_micro(budget_cr)`,
           `b = lmsr.b_from_budget_micro(budget)`. Pass `precision=
           PRICING_DECIMAL_PRECISION` to both. `budget > MAX_MARKET_BUDGET`, or a b
           for which `lmsr.is_quotable(b, precision=...)` is false, raises
           `LimitExceededError` (409) with `details={"limit":
           Limit.MAX_MARKET_BUDGET}`: a market must never open with a b the quote
           functions refuse, because the database keeps b for as long as it is OPEN.
        5. Caps, each a `LimitExceededError` whose `details.limit` names the cap:
           `markets.count_by_statuses({OPEN, HALTED}) >= MAX_OPEN_MARKETS`
           (`Limit.MAX_OPEN_MARKETS`); `markets.sum_budget_by_statuses({OPEN,
           HALTED}) + budget > MAX_AGGREGATE_LOSS_BUDGET`
           (`Limit.MAX_AGGREGATE_LOSS_BUDGET`).
        6. Write: `status=OPEN`, `tier`, `budget_micro`, `b_micro`,
           `approved_by=actor.id`, `opened_at=now`, all in the one UPDATE that opens
           the market: assign them together, with nothing awaited in between, so that
           no flush sends the status ahead of b. The database refuses an approved
           market without b, and once it is OPEN it refuses to change budget or b
           (SPEC §5). Create the market's account
           (`accounts.create_market_account`). Queue the version-0 point of the chart:
           `outbox.add(QUOTE_UPDATED, quote_updated_payload(QuotePoint(market.id, now,
           0, EVEN_ODDS_MICRO, 0, 0, None)), now)`. Audit `MARKET_APPROVE` with the
           tier, the budget and b, and each wording or date the approval changed, as
           it was and as it is.

        No credit moves: a market starts at q = 0 with an empty account. That account
        never goes below zero; what the market maker loses here, at most the budget,
        is booked to `vis:mm_pnl` when the market settles (A.8).
        """
        _require_admin(actor)
        refuse_nul(request)
        risk = self._settings.risk
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            _require_move(market, MarketStatus.OPEN, start=MarketStatus.PROPOSED)
            await uow.advisory_lock(APPROVAL_LOCK)
            now = self._clock.now()

            before: dict[str, Any] = {
                "status": market.status.value,
                "tier": None if market.tier is None else market.tier.value,
                "budget_micro": market.budget_micro,
                "b_micro": market.b_micro,
            }
            proposed = _terms(market)
            _apply_edits(market, request)
            check_market_dates(
                self._settings, now=now, close_at=market.close_at, resolve_by=market.resolve_by
            )

            budget_micro, b_micro = self._liquidity(request)
            if budget_micro > risk.max_market_budget_micro or not lmsr.is_quotable(
                b_micro, precision=self._settings.pricing.decimal_precision
            ):
                raise LimitExceededError(
                    BUDGET_LIMIT_MESSAGE, details={"limit": Limit.MAX_MARKET_BUDGET}
                )
            if await uow.markets.count_by_statuses(CAPPED_STATUSES) >= risk.max_open_markets:
                raise LimitExceededError(
                    OPEN_MARKETS_LIMIT_MESSAGE, details={"limit": Limit.MAX_OPEN_MARKETS}
                )
            capped_micro = await uow.markets.sum_budget_by_statuses(CAPPED_STATUSES)
            if capped_micro + budget_micro > risk.max_aggregate_loss_budget_micro:
                raise LimitExceededError(
                    LOSS_BUDGET_LIMIT_MESSAGE,
                    details={"limit": Limit.MAX_AGGREGATE_LOSS_BUDGET},
                )

            # One UPDATE: nothing is awaited until all six are set.
            market.status = MarketStatus.OPEN
            market.tier = request.tier
            market.budget_micro = budget_micro
            market.b_micro = b_micro
            market.approved_by = actor.id
            market.opened_at = now
            await uow.accounts.create_market_account(market.id)
            opening = QuotePoint(market.id, now, 0, EVEN_ODDS_MICRO, 0, 0, None)
            await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, quote_updated_payload(opening), now)
            edited_from, edited_to = _differences(proposed, _terms(market))
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_APPROVE,
                market,
                before={**before, **edited_from},
                after={
                    "status": market.status.value,
                    "tier": request.tier.value,
                    "budget_micro": budget_micro,
                    "b_micro": b_micro,
                    **edited_to,
                },
            )

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    async def reject(
        self, actor: AuthenticatedUser, market_id: UUID, request: RejectMarketRequest
    ) -> MarketDetailResponse:
        """Turn a proposal down. PROPOSED -> REJECTED.

        Set `status=REJECTED` and `rejection_reason=request.reason`; audit
        `MARKET_REJECT`. A rejected market was never public: no list is dropped from
        the cache and no status event is published.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            previous = market.status
            _require_move(market, MarketStatus.REJECTED)
            market.status = MarketStatus.REJECTED
            market.rejection_reason = request.reason
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_REJECT,
                market,
                before={"status": previous.value},
                after={"status": market.status.value, "reason": request.reason},
            )

        return await self._markets.detail(actor, market.id)

    async def halt(
        self, actor: AuthenticatedUser, market_id: UUID, request: HaltMarketRequest
    ) -> MarketDetailResponse:
        """Suspend trading. OPEN -> HALTED.

        The effective status (`MarketView.effective_status`) must be OPEN: a market
        past `close_at` is already closed to trading and raises `ConflictError`. Audit
        `MARKET_HALT` with the reason.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            now = self._clock.now()
            previous = market.status
            _require_move(market, MarketStatus.HALTED)
            if not self._view.is_trading_open(market, now):
                raise ConflictError
            market.status = MarketStatus.HALTED
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_HALT,
                market,
                before={"status": previous.value},
                after={"status": market.status.value, "reason": request.reason},
            )

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    async def resume(
        self, actor: AuthenticatedUser, market_id: UUID, request: ResumeMarketRequest
    ) -> MarketDetailResponse:
        """Let a halted market trade again. HALTED -> OPEN.

        Allowed past `close_at` too; the market is then stored OPEN and reported
        CLOSED like any other past its deadline. Audit `MARKET_RESUME` with the note.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            previous = market.status
            _require_move(market, MarketStatus.OPEN, start=MarketStatus.HALTED)
            market.status = MarketStatus.OPEN
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_RESUME,
                market,
                before={"status": previous.value},
                after={"status": market.status.value, "note": request.note},
            )

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    async def close(
        self, actor: AuthenticatedUser, market_id: UUID, request: CloseMarketRequest
    ) -> MarketDetailResponse:
        """Stop trading for good. OPEN -> CLOSED or HALTED -> CLOSED.

        Works before and after `close_at`. Set `status=CLOSED` and `closed_at=now`;
        audit `MARKET_CLOSE` with the note.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            now = self._clock.now()
            _require_move(market, MarketStatus.CLOSED)
            await self._close(uow, actor, market, now, note=request.note)

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    async def resolve(
        self, actor: AuthenticatedUser, market_id: UUID, request: ResolveMarketRequest
    ) -> MarketDetailResponse:
        """Settle a market to YES or NO and pay the winners (SPEC §6, A.5, A.8).

        After locking the market row:
        1. Self-dealing (A.4): `trades.exists_for_user_market(actor.id, market_id)`
           raises `ForbiddenError(SELF_DEALING_MESSAGE)` (403). An admin who ever
           traded the market may not decide it, whatever they hold now; another admin
           has to. `MarketViewer.can_settle` tells the admin screen beforehand.
        2. Reach CLOSED. Stored CLOSED: go on. HALTED, or OPEN with `close_at`
           reached: set `status=CLOSED` and `closed_at=now` first, with its own
           `MARKET_CLOSE` audit row. OPEN before `close_at`: `ConflictError` ("close
           the market first", `CLOSE_FIRST_MESSAGE`). Any other status:
           `ConflictError`; this is also what a second resolution gets.
        3. Insert `Resolution(market_id, outcome, evidence, resolved_by=actor.id,
           created_at=now)`. Its unique `market_id` is the backstop against a second
           one.
        4. Payouts. `positions.list_for_market(market_id)`; every position of the
           winning outcome with shares pays its `shares_micro` (1 CR per share). Lock
           the winners' accounts in one query, `accounts.lock_user_accounts(<their
           user ids>)` (ascending account id; `owner_id` maps each back to its user),
           then read the market account; B is its balance.
        5. Post one `MARKET_RESOLUTION` entry: each winner +shares, market account -B,
           `vis:mm_pnl` +(B - Σ payouts), which may be negative. `LedgerRef.MARKET`,
           the market's id, `resolution_key(market.id)`. If every line is zero (nobody
           traded), post nothing.
        6. `status=RESOLVED`. No position is touched: each keeps its shares, cost
           and proceeds. The portfolio, the market page and the leaderboard read a
           user's payout from the shares of the winning outcome. Audit
           `MARKET_RESOLVE` with the outcome and Σ payouts.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            now = self._clock.now()
            await self._refuse_a_trader(uow, actor, market)
            await self._bring_to_closed(uow, actor, market, now)
            previous = market.status

            await uow.resolutions.add(
                Resolution(
                    market_id=market.id,
                    outcome=ResolutionOutcome(request.outcome.value),
                    evidence=request.evidence,
                    resolved_by=actor.id,
                    created_at=now,
                )
            )
            payouts_micro = {
                position.user_id: position.shares_micro
                for position in await uow.positions.list_for_market(market.id)
                if position.outcome is request.outcome and position.shares_micro > 0
            }
            await self._pay(
                uow,
                market,
                kind=JournalKind.MARKET_RESOLUTION,
                idempotency_key=resolution_key(market.id),
                owed_micro=payouts_micro,
                now=now,
            )
            market.status = MarketStatus.RESOLVED
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_RESOLVE,
                market,
                before={"status": previous.value},
                after={
                    "status": market.status.value,
                    "outcome": request.outcome.value,
                    "payouts_micro": sum(payouts_micro.values()),
                },
            )

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    async def void(
        self, actor: AuthenticatedUser, market_id: UUID, request: VoidMarketRequest
    ) -> MarketDetailResponse:
        """Cancel a market and refund every net stake (SPEC §6, A.5, A.8).

        Steps 1 and 2 are those of `resolve`: an admin who traded the market may not
        void it either (A.4: a void would refund that admin's own stake) and gets the
        same `ForbiddenError(SELF_DEALING_MESSAGE)`, and the market is brought to
        CLOSED the same way. Then:
        3. Insert `Resolution(market_id, outcome=VOID, evidence=request.reason,
           resolved_by=actor.id, created_at=now)`.
        4. Refunds. `positions.list_for_market(market_id)`, grouped by user; each
           user's refund is `MarketView`'s net stake, max(0, Σ cost - Σ proceeds) over
           both outcomes. Lock those users' accounts with
           `accounts.lock_user_accounts`, then read the market account; B is its
           balance. A user whose net stake is zero gets no line and no lock.
        5. Post one `MARKET_VOID` entry: each user +refund, market account -B,
           `vis:mm_pnl` +(B - Σ refunds). `LedgerRef.MARKET`, the market's id,
           `void_key(market.id)`. If every line is zero, post nothing. Σ refunds is
           never below B: what it exceeds B by is what traders who sold above their
           cost took out, and a void does not take that back. The risk page shows
           this amount beforehand as `void_cost_cr`.
        6. Set `shares_micro=0` on every position and the market's `q_yes_micro` and
           `q_no_micro` to 0; `status=VOID`. `cost_micro` and `proceeds_micro` of a
           position stay as they are: the portfolio and the market page read a
           user's refund from them. Audit `MARKET_VOID` with the reason and Σ refunds.
        """
        _require_admin(actor)
        refuse_nul(request)
        async with self._uow_factory() as uow:
            market = await _lock(uow, market_id)
            now = self._clock.now()
            await self._refuse_a_trader(uow, actor, market)
            await self._bring_to_closed(uow, actor, market, now)
            previous = market.status

            await uow.resolutions.add(
                Resolution(
                    market_id=market.id,
                    outcome=ResolutionOutcome.VOID,
                    evidence=request.reason,
                    resolved_by=actor.id,
                    created_at=now,
                )
            )
            positions = await uow.positions.list_for_market(market.id)
            held_by: dict[UUID, list[Position]] = {}
            for position in positions:
                held_by.setdefault(position.user_id, []).append(position)
            refunds_micro = {
                user_id: refund_micro
                for user_id, held in held_by.items()
                if (refund_micro := self._view.net_stake_micro(held)) > 0
            }
            await self._pay(
                uow,
                market,
                kind=JournalKind.MARKET_VOID,
                idempotency_key=void_key(market.id),
                owed_micro=refunds_micro,
                now=now,
            )
            for position in positions:
                position.shares_micro = 0
            market.q_yes_micro = 0
            market.q_no_micro = 0
            market.status = MarketStatus.VOID
            await self._record(
                uow,
                actor,
                AuditAction.MARKET_VOID,
                market,
                before={"status": previous.value},
                after={
                    "status": market.status.value,
                    "reason": request.reason,
                    "refunds_micro": sum(refunds_micro.values()),
                },
            )

        await self._after_commit(market)
        return await self._markets.detail(actor, market.id)

    def _liquidity(self, request: ApproveMarketRequest) -> tuple[int, int]:
        """Step 4 of `approve`: the budget and the b of the tier asked for, in µCR."""
        pricing = self._settings.pricing
        precision = pricing.decimal_precision
        if request.tier is MarketTier.CUSTOM:
            if request.budget_cr is None:
                # The request model refuses this; only a request built around it is here.
                raise ValidationFailedError(details={"field": "budget_cr"})
            budget_micro = to_micro(request.budget_cr)
            return budget_micro, lmsr.b_from_budget_micro(budget_micro, precision=precision)
        b_micro = pricing.tier_b_micro(request.tier)
        return lmsr.max_loss_micro(b_micro, precision=precision), b_micro

    async def _refuse_a_trader(
        self, uow: UnitOfWork, actor: AuthenticatedUser, market: Market
    ) -> None:
        """Step 1 of `resolve` and `void`: an admin who traded the market may not
        settle it (A.4)."""
        if await uow.trades.exists_for_user_market(actor.id, market.id):
            raise ForbiddenError(SELF_DEALING_MESSAGE)

    async def _bring_to_closed(
        self, uow: UnitOfWork, actor: AuthenticatedUser, market: Market, now: datetime
    ) -> None:
        """Step 2 of `resolve` and `void`: leave `market` stored CLOSED, or refuse.

        A HALTED market and an OPEN one whose `close_at` has come are closed here, in
        the settlement's own transaction, with a `MARKET_CLOSE` audit row of their own.
        """
        if market.status is MarketStatus.CLOSED:
            return
        if self._view.is_trading_open(market, now):
            raise ConflictError(CLOSE_FIRST_MESSAGE)
        _require_move(market, MarketStatus.CLOSED)
        await self._close(uow, actor, market, now, note=None)

    async def _close(
        self,
        uow: UnitOfWork,
        actor: AuthenticatedUser,
        market: Market,
        now: datetime,
        *,
        note: str | None,
    ) -> None:
        """Store `market` CLOSED as of `now` and audit it. The caller has checked that
        it may be closed."""
        previous = market.status
        market.status = MarketStatus.CLOSED
        market.closed_at = now
        await self._record(
            uow,
            actor,
            AuditAction.MARKET_CLOSE,
            market,
            before={"status": previous.value},
            after={"status": market.status.value, "note": note},
        )

    async def _pay(
        self,
        uow: UnitOfWork,
        market: Market,
        *,
        kind: JournalKind,
        idempotency_key: str,
        owed_micro: Mapping[UUID, int],
        now: datetime,
    ) -> None:
        """Book the one journal entry of a settlement (A.8).

        `owed_micro` is what each user gets, by user id, without the users who get
        nothing. Their accounts are locked first, all at once; then the market's
        account is read, whose balance B nobody can move while the market row is held.
        The entry credits every user their amount, takes B from the market's account
        and gives `vis:mm_pnl` the difference, B - Σ owed, which may be negative.
        Nothing is posted when every line is zero: a market nobody traded.
        """
        locked = await uow.accounts.lock_user_accounts(list(owed_micro)) if owed_micro else ()
        accounts = {account.owner_id: account for account in locked}
        lines: list[PostingLine] = []
        for user_id, amount_micro in owed_micro.items():
            account = accounts.get(user_id)
            if account is None:
                raise LookupError(f"user {user_id} has no ledger account")
            lines.append(PostingLine(account.id, amount_micro))

        market_account = await uow.accounts.get_market_account(market.id)
        if market_account is None:
            raise LookupError(f"market {market.id} is {market.status} but has no account")
        balance_micro = market_account.balance_micro
        pnl = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
        lines.append(PostingLine(market_account.id, -balance_micro))
        lines.append(PostingLine(pnl.id, balance_micro - sum(owed_micro.values())))
        if any(line.amount_micro for line in lines):
            await self._ledger.post(
                uow,
                kind=kind,
                ref_type=LedgerRef.MARKET,
                ref_id=market.id,
                idempotency_key=idempotency_key,
                lines=lines,
                at=now,
            )

    async def _record(
        self,
        uow: UnitOfWork,
        actor: AuthenticatedUser,
        action: AuditAction,
        market: Market,
        *,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> None:
        """The audit row of one status change of `market`, in the caller's transaction."""
        await self._audit.record(
            uow,
            actor_id=actor.id,
            action=action,
            target=AuditService.target(AuditTarget.MARKET, market.id),
            before=before,
            after=after,
        )

    async def _after_commit(self, market: Market) -> None:
        """What follows a committed change of a public market, best effort: drop the
        cached lists, then tell open phones. Neither call raises."""
        await self._cache.invalidate_lists()
        await self._live.publish_status(market)


def _require_admin(actor: AuthenticatedUser) -> None:
    if not actor.is_admin:
        raise ForbiddenError


async def _lock(uow: UnitOfWork, market_id: UUID) -> Market:
    """The market row, locked: the first lock of every action here."""
    market = await uow.markets.get_for_update(market_id)
    if market is None:
        raise NotFoundError
    return market


def _require_move(
    market: Market, target: MarketStatus, *, start: MarketStatus | None = None
) -> None:
    """`ConflictError` unless `ALLOWED_TRANSITIONS` lets `market` become `target`.

    Two actions end OPEN: a proposal is approved and a halted market is resumed. Each
    names the status it starts from as `start`, so that neither takes the other's
    market.
    """
    if target not in ALLOWED_TRANSITIONS[market.status]:
        raise ConflictError
    if start is not None and market.status is not start:
        raise ConflictError


def _apply_edits(market: Market, request: ApproveMarketRequest) -> None:
    """Step 2 of `approve`: a field the request leaves out keeps what the proposal says."""
    if request.question is not None:
        market.question = request.question
    if request.yes_definition is not None:
        market.yes_def = request.yes_definition
    if request.no_definition is not None:
        market.no_def = request.no_definition
    if request.resolution_criteria is not None:
        market.resolution_criteria = request.resolution_criteria
    if request.resolution_source is not None:
        market.resolution_source = request.resolution_source
    if request.close_at is not None:
        market.close_at = request.close_at
    if request.resolve_by is not None:
        market.resolve_by = request.resolve_by


def _terms(market: Market) -> dict[str, str | datetime]:
    """What an approval may edit, the wording and the two dates, under the names the
    API gives them."""
    return {
        "question": market.question,
        "yes_definition": market.yes_def,
        "no_definition": market.no_def,
        "resolution_criteria": market.resolution_criteria,
        "resolution_source": market.resolution_source,
        "close_at": market.close_at,
        "resolve_by": market.resolve_by,
    }


def _differences(
    was: Mapping[str, str | datetime], now_is: Mapping[str, str | datetime]
) -> tuple[dict[str, str], dict[str, str]]:
    """The terms that differ between two readings of `_terms`, as an audit row keeps
    them: what each was and what it is. An instant is written in UTC, and two spellings
    of one instant are no difference."""
    changed = [name for name, value in was.items() if now_is[name] != value]
    return (
        {name: _audited(was[name]) for name in changed},
        {name: _audited(now_is[name]) for name in changed},
    )


def _audited(value: str | datetime) -> str:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value
