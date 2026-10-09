"""The admin actions that move a market through its life (SPEC §5, §6, A.4, A.5, A.8).

    PROPOSED -> OPEN (approve) | REJECTED (reject)
    OPEN     -> HALTED (halt)  | CLOSED (close, or `close_at` reached)
    HALTED   -> OPEN (resume)  | CLOSED (close)
    CLOSED   -> RESOLVED (resolve YES or NO) | VOID (void)

Shared by all seven methods:

* One transaction. The market row is locked first (`markets.get_for_update`); an unknown
  market raises `NotFoundError` (404).
* A move that `ALLOWED_TRANSITIONS` does not list raises `ConflictError` (409).
* Lock order: the market row, then the user accounts a settlement pays (all at once,
  `accounts.lock_user_accounts`), then whatever `LedgerService.post` locks. Positions
  need no lock of their own: every writer of a market's positions holds its row.
* One `audit_log` row per status change, in the transaction, with `actor_id=actor.id`,
  target `AuditService.target(AuditTarget.MARKET, market.id)`, and `before` and `after`
  holding at least `{"status": ...}` plus what the action changed or was told (tier,
  budget, a reason, the outcome).
* After the commit, best effort: `MarketCache.invalidate_market(market.id)` and
  `LiveQuoteService.publish_status(market)`.
* The result is the market's detail afterwards: `MarketService.detail(actor, market.id)`.
"""

from collections.abc import Mapping
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import MarketStatus
from app.core.identity import AuthenticatedUser
from app.integrations.redis.market_cache import MarketCache
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.markets import MarketDetailResponse
from app.services import UowFactory
from app.services.audit import AuditService
from app.services.ledger import LedgerService
from app.services.live_quotes import LiveQuoteService
from app.services.market_view import MarketView
from app.services.markets import MarketService

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
        2. Apply the edits the request carries (wording, `close_at`, `resolve_by`).
        3. Dates, on the edited values, each a `ValidationFailedError` (422):
           `now < close_at <= now + MAX_MARKET_LIFETIME` and
           `close_at <= resolve_by <= close_at + MAX_RESOLUTION_DELAY_DAYS`.
        4. Budget and b. A fixed tier: `b = settings.pricing.tier_b_micro(tier)`,
           `budget = lmsr.max_loss_micro(b)`. CUSTOM: `budget = to_micro(budget_cr)`,
           `b = lmsr.b_from_budget_micro(budget)`. Pass `precision=
           PRICING_DECIMAL_PRECISION` to both. `budget > MAX_MARKET_BUDGET` raises
           `LimitExceededError` (409) with `details={"limit":
           Limit.MAX_MARKET_BUDGET}`.
        5. Caps, each a `LimitExceededError` whose `details.limit` names the cap:
           `markets.count_by_statuses({OPEN, HALTED}) >= MAX_OPEN_MARKETS`
           (`Limit.MAX_OPEN_MARKETS`); `markets.sum_budget_by_statuses({OPEN,
           HALTED}) + budget > MAX_AGGREGATE_LOSS_BUDGET`
           (`Limit.MAX_AGGREGATE_LOSS_BUDGET`).
        6. Write: `status=OPEN`, `tier`, `budget_micro`, `b_micro`,
           `approved_by=actor.id`, `opened_at=now`, all in the one UPDATE that opens
           the market: once it is OPEN the database refuses to change its budget or b
           (SPEC §5). Create the market's account
           (`accounts.create_market_account`). Queue the version-0 point of the chart:
           `outbox.add(QUOTE_UPDATED, quote_updated_payload(QuotePoint(market.id, now,
           0, EVEN_ODDS_MICRO, 0, 0, None)), now)`. Audit `MARKET_APPROVE`.

        No credit moves: a market starts at q = 0 with an empty account. That account
        never goes below zero; what the market maker loses here, at most the budget,
        is booked to `vis:mm_pnl` when the market settles (A.8).
        """
        raise NotImplementedError

    async def reject(
        self, actor: AuthenticatedUser, market_id: UUID, request: RejectMarketRequest
    ) -> MarketDetailResponse:
        """Turn a proposal down. PROPOSED -> REJECTED.

        Set `status=REJECTED` and `rejection_reason=request.reason`; audit
        `MARKET_REJECT`. A rejected market was never public, so nothing is published.
        """
        raise NotImplementedError

    async def halt(
        self, actor: AuthenticatedUser, market_id: UUID, request: HaltMarketRequest
    ) -> MarketDetailResponse:
        """Suspend trading. OPEN -> HALTED.

        The effective status (`MarketView.effective_status`) must be OPEN: a market
        past `close_at` is already closed to trading and raises `ConflictError`. Audit
        `MARKET_HALT` with the reason.
        """
        raise NotImplementedError

    async def resume(
        self, actor: AuthenticatedUser, market_id: UUID, request: ResumeMarketRequest
    ) -> MarketDetailResponse:
        """Let a halted market trade again. HALTED -> OPEN.

        Allowed past `close_at` too; the market is then stored OPEN and reported
        CLOSED like any other past its deadline. Audit `MARKET_RESUME` with the note.
        """
        raise NotImplementedError

    async def close(
        self, actor: AuthenticatedUser, market_id: UUID, request: CloseMarketRequest
    ) -> MarketDetailResponse:
        """Stop trading for good. OPEN -> CLOSED or HALTED -> CLOSED.

        Works before and after `close_at`. Set `status=CLOSED` and `closed_at=now`;
        audit `MARKET_CLOSE` with the note.
        """
        raise NotImplementedError

    async def resolve(
        self, actor: AuthenticatedUser, market_id: UUID, request: ResolveMarketRequest
    ) -> MarketDetailResponse:
        """Settle a market to YES or NO and pay the winners (SPEC §6, A.5, A.8).

        After locking the market row:
        1. Self-dealing (A.4): `trades.exists_for_user_market(actor.id, market_id)`
           raises `ForbiddenError` (403).
        2. Reach CLOSED. Stored CLOSED: go on. HALTED, or OPEN with `close_at`
           reached: set `status=CLOSED` and `closed_at=now` first, with its own
           `MARKET_CLOSE` audit row. OPEN before `close_at`: `ConflictError` ("close
           the market first"). Any other status: `ConflictError`; this is also what a
           second resolution gets.
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
        6. `status=RESOLVED`. Positions keep their shares as a record. Audit
           `MARKET_RESOLVE` with the outcome and Σ payouts.
        """
        raise NotImplementedError

    async def void(
        self, actor: AuthenticatedUser, market_id: UUID, request: VoidMarketRequest
    ) -> MarketDetailResponse:
        """Cancel a market and refund every net stake (SPEC §6, A.5, A.8).

        Steps 1 and 2 are those of `resolve`: an admin who traded the market may not
        void it either (A.4: a void would refund that admin's own stake), and the
        market is brought to CLOSED the same way. Then:
        3. Insert `Resolution(market_id, outcome=VOID, evidence=request.reason,
           resolved_by=actor.id, created_at=now)`.
        4. Refunds. `positions.list_for_market(market_id)`, grouped by user; each
           user's refund is `MarketView`'s net stake, max(0, Σ cost - Σ proceeds) over
           both outcomes. Lock those users' accounts with
           `accounts.lock_user_accounts`, then read the market account; B is its
           balance.
        5. Post one `MARKET_VOID` entry: each user +refund, market account -B,
           `vis:mm_pnl` +(B - Σ refunds). `LedgerRef.MARKET`, the market's id,
           `void_key(market.id)`. If every line is zero, post nothing.
        6. Set `shares_micro=0` on every position (cost and proceeds stay as a
           record) and the market's `q_yes_micro` and `q_no_micro` to 0;
           `status=VOID`. Audit `MARKET_VOID` with the reason and Σ refunds.
        """
        raise NotImplementedError
