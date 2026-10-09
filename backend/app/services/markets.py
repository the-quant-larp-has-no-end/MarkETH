"""Markets as everyone reads them, and how one is proposed (SPEC §6, §9, A.4, A.5).

Reads report the effective status and display prices through `MarketView`. The public
answers are cached in Redis for `MARKET_CACHE_TTL_SECONDS`; whatever belongs to one
viewer (holdings, stake, balance) is always read from Postgres.
"""

from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import MarketStatus
from app.core.identity import AuthenticatedUser
from app.integrations.redis.market_cache import MarketCache
from app.schemas.markets import MarketDetailResponse, MarketListResponse, ProposeMarketRequest
from app.services import UowFactory
from app.services.market_view import MarketView

VOID_RULE: Final[str] = (
    "If VIS voids this market, every trader gets back their net stake in it: what they "
    "paid for shares minus what they received for selling, never less than zero. All "
    "positions are cancelled and nobody wins or loses."
)
"""`MarketDetailResponse.void_rule` of every market (SPEC §6, A.5)."""

PUBLIC_STATUSES: Final[tuple[MarketStatus, ...]] = (
    MarketStatus.OPEN,
    MarketStatus.HALTED,
    MarketStatus.CLOSED,
    MarketStatus.RESOLVED,
    MarketStatus.VOID,
)
"""What `GET /markets` lists without a `status` filter (A.5)."""


class MarketService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        cache: MarketCache,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._clock = clock
        self._view = view
        self._cache = cache

    async def propose(
        self, user: AuthenticatedUser, request: ProposeMarketRequest
    ) -> MarketDetailResponse:
        """Create a PROPOSED market (`POST /markets`, SPEC §6).

        1. Time rules, each a `ValidationFailedError` (422) naming the field in
           `details.field`: `close_at > now`; `close_at <= now + MAX_MARKET_LIFETIME`;
           `resolve_by <= close_at + MAX_RESOLUTION_DELAY_DAYS`. (`resolve_by >=
           close_at` and the fixed tier are already checked by the request model.)
        2. One transaction. `uow.advisory_lock(f"proposals:{user.id}")`, so two
           proposals of one user cannot both slip under the limit. Then
           `markets.count_created_by_since(user.id, start_of_day(now, APP_TIMEZONE))`:
           at `MAX_PROPOSALS_PER_USER_PER_DAY` or more, `LimitExceededError` (409).
        3. Insert the market: `status=PROPOSED`, the request's wording and dates,
           `requested_tier`, `tier=None`, `created_by=user.id`, `created_at=now`;
           budget, b, quantities, version and volume stay 0.
        4. Return its detail as `detail(user, market.id)` would.

        Nothing is cached or published: a proposal is not public.
        """
        raise NotImplementedError

    async def list_markets(
        self, viewer: AuthenticatedUser | None, status: MarketStatus | None
    ) -> MarketListResponse:
        """Markets by effective status, soonest `close_at` first (`GET /markets`).

        * `status=None`: every market whose effective status is in `PUBLIC_STATUSES`.
        * PROPOSED or REJECTED: admins only. No viewer raises `UnauthenticatedError`
          (401), a viewer who is not an admin `ForbiddenError` (403). Never cached.
        * Any other status: the markets with that effective status.

        The effective status differs from the stored one only for OPEN markets past
        `close_at`, so OPEN and CLOSED are both read from the stored set
        `{OPEN, CLOSED}` and filtered with `MarketView.effective_status`.

        Caching (public lists only): the key is `MarketCache.list_key(status.value if
        status else None)`. A hit is `MarketListResponse.model_validate_json(text)`. A
        miss reads Postgres (`markets.list_by_statuses`, plus the `resolutions` row of
        each RESOLVED or VOID market for `MarketView.summary`) and stores
        `response.model_dump_json()`. The list holds nothing about the viewer, so one
        entry serves everybody.
        """
        raise NotImplementedError

    async def detail(
        self, viewer: AuthenticatedUser | None, market_id: UUID
    ) -> MarketDetailResponse:
        """One market with its rules, and the viewer's side of it (`GET /markets/{id}`).

        Visibility: an unknown market raises `NotFoundError` (404). So does a PROPOSED
        or REJECTED one unless the viewer is an admin or its proposer.

        Without a viewer the answer is public and cached under
        `MarketCache.market_key(market_id)` (not for PROPOSED or REJECTED), stored as
        `response.model_dump_json()`. With a viewer the cache is skipped and everything
        is read from Postgres in one transaction, so the viewer's numbers and the price
        they are valued at come from the same instant.

        Fields beyond `MarketView.summary`: the wording columns (`yes_def` and
        `no_def` are `yes_definition` and `no_definition`), `requested_tier`, `tier`,
        `b_cr`, `max_loss_cr = budget_micro`, `void_rule = VOID_RULE`, the timestamps,
        `rejection_reason`, `resolution` from the `resolutions` row (`resolved_at` is
        its `created_at`), and `proposer_email`: the proposer's `users.email` when the
        viewer is an admin, else null.

        `viewer` block (null without a viewer), from the viewer's account and their
        `positions.list_for_user_market`:
        * `balance_cr`; `holdings = MarketView.holdings(...)`; `cost_cr` and
          `proceeds_cr` summed over both positions; `net_stake_cr`; `stake_limit_cr =
          MAX_STAKE_PER_USER_MARKET`; `stake_left_cr = MarketView.stake_left_micro`.
        * `can_trade` and `cannot_trade_reason`, the first that applies:
          `ACCOUNT_SUSPENDED` (status not ACTIVE), `MARKET_NOT_OPEN` (effective status
          not OPEN), `OWN_MARKET` (`created_by == viewer.id`); otherwise true and null.
        * `stake_presets`: one per `TRADE_STAKE_PRESETS_CR`, in that order, `allowed`
          when `can_trade` and the amount is at least `MIN_TRADE_CR`, at most
          `stake_left` and at most the balance.
        """
        raise NotImplementedError
