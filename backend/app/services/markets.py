"""Markets as everyone reads them, and how one is proposed (SPEC §6, §9, A.4, A.5).

Reads report the effective status and display prices through `MarketView`. The lists,
which are the same for everybody, are cached in Redis for `MARKET_CACHE_TTL_SECONDS`;
whatever belongs to one viewer (holdings, stake, balance) is always read from Postgres.

One market is read by `detail_in`, inside a transaction somebody else opened. `detail`
opens one for it; a proposal and the seven admin actions call it in the transaction
that makes their change, so their answer exists before the commit (A.5).

Two rules a proposal and its approval share live here as functions, so that
`MarketLifecycleService` applies the very same ones: `check_market_dates` and
`refuse_nul`.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.core.clock import Clock, start_of_day
from app.core.config import Settings
from app.core.enums import MarketStatus
from app.core.errors import (
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.integrations.redis.market_cache import MarketCache
from app.models.market import Market, Resolution
from app.models.trading import Position
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.common import Limit
from app.schemas.markets import (
    DECIDED_BEFORE_CLOSE_MESSAGE,
    MarketDetailResponse,
    MarketListResponse,
    MarketResolution,
    MarketViewer,
    ProposeMarketRequest,
    TradeBlockReason,
)
from app.services import UowFactory
from app.services.market_view import (
    HIDDEN_STATUSES,
    SETTLED_STATUSES,
    UNSETTLED_STATUSES,
    MarketView,
)

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
"""What `GET /markets` lists without a `status` filter (A.5): every status that is not
for admins and the proposer alone."""

PROPOSAL_LIMIT_MESSAGE: Final[str] = (
    "You have reached today's limit of proposals. Try again tomorrow."
)
"""Message of the `LimitExceededError` a proposal beyond
`MAX_PROPOSALS_PER_USER_PER_DAY` gets."""

UNSTORABLE_TEXT_MESSAGE: Final[str] = "This text has a character that cannot be saved."
"""Message of the `ValidationFailedError` that `refuse_nul` raises."""

_NUL: Final[str] = "\x00"
"""The one character Postgres refuses in a text, and so in every wording of a market."""


def check_market_dates(
    settings: Settings, *, now: datetime, close_at: datetime, resolve_by: datetime
) -> None:
    """The date rules of a market, at its proposal and again at its approval (SPEC §6,
    A.5):

        now < close_at <= now + MAX_MARKET_LIFETIME
        close_at <= resolve_by <= close_at + MAX_RESOLUTION_DELAY_DAYS

    The first one that is broken, in this order, raises `ValidationFailedError` (422)
    with the field it is about in `details.field`: `close_at` for the first line,
    `resolve_by` for the second. The message says which rule it was, in the words of
    the form: a client shows it under that field.
    """
    lifetime = settings.risk.max_market_lifetime
    delay = settings.lifecycle.max_resolution_delay
    if close_at <= now:
        raise ValidationFailedError(
            "Trading has to close in the future.", details={"field": "close_at"}
        )
    if close_at > now + lifetime:
        raise ValidationFailedError(
            f"Trading can stay open for at most {_days(lifetime.days)}.",
            details={"field": "close_at"},
        )
    if resolve_by < close_at:
        raise ValidationFailedError(DECIDED_BEFORE_CLOSE_MESSAGE, details={"field": "resolve_by"})
    if resolve_by > close_at + delay:
        raise ValidationFailedError(
            f"A market has to be decided within {_days(delay.days)} after trading closes.",
            details={"field": "resolve_by"},
        )


def refuse_nul(request: BaseModel) -> None:
    """Refuse a request one of whose texts holds a NUL character: `ValidationFailedError`
    (422, `UNSTORABLE_TEXT_MESSAGE`) with the first such field in `details.field`.

    A NUL is valid in JSON and in no column of Postgres, neither a text nor the JSON of
    an audit row. Without this check the wording of a market, a reason or a note with
    one in it would fail in the database and be answered 500.
    """
    for field, value in request:
        if isinstance(value, str) and _NUL in value:
            raise ValidationFailedError(UNSTORABLE_TEXT_MESSAGE, details={"field": field})


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

        1. Rules on the request, each a `ValidationFailedError` (422) naming the field
           in `details.field`. No text may hold a NUL character (`refuse_nul`). Then
           the time rules (`check_market_dates`): `close_at > now`; `close_at <= now +
           MAX_MARKET_LIFETIME`; `resolve_by <= close_at + MAX_RESOLUTION_DELAY_DAYS`.
           (`resolve_by >= close_at` and the accepted usage policy are already
           checked by the request model. Nothing of the acceptance is stored with
           the market: every proposal states it again.)
        2. One transaction. `uow.advisory_lock(f"proposals:{user.id}")`, so two
           proposals of one user cannot both slip under the limit. Then
           `markets.count_created_by_since(user.id, start_of_day(now, APP_TIMEZONE))`:
           at `MAX_PROPOSALS_PER_USER_PER_DAY` or more, `LimitExceededError` (409,
           `PROPOSAL_LIMIT_MESSAGE`) with `details={"limit":
           Limit.MAX_PROPOSALS_PER_USER_PER_DAY}`.
        3. Insert the market: `status=PROPOSED`, the request's wording and dates,
           `tier=None`, `created_by=user.id`, `created_at=now`; budget, b,
           quantities, version and volume stay 0. A proposal says nothing about
           liquidity: the admin who approves it picks the tier.
        4. Build the answer in that transaction: `detail_in(uow, user, market.id)`,
           the proposal as its proposer sees it. Nothing is read once the
           transaction has committed, so a proposal that was filed is never answered
           as one that failed. Sent again after such an answer it would be filed a
           second time and use up one more of the day's limit.

        `now` is read once, before the transaction: it is what the dates are held
        against, the day the limit counts, and the proposal's `created_at`.

        Nothing is cached or published: a proposal is not public.
        """
        refuse_nul(request)
        now = self._clock.now()
        check_market_dates(
            self._settings, now=now, close_at=request.close_at, resolve_by=request.resolve_by
        )

        today = start_of_day(now, self._settings.app.timezone)
        limit = self._settings.lifecycle.max_proposals_per_user_per_day
        async with self._uow_factory() as uow:
            await uow.advisory_lock(f"proposals:{user.id}")
            if await uow.markets.count_created_by_since(user.id, today) >= limit:
                raise LimitExceededError(
                    PROPOSAL_LIMIT_MESSAGE,
                    details={"limit": Limit.MAX_PROPOSALS_PER_USER_PER_DAY},
                )
            market = await uow.markets.add(
                Market(
                    question=request.question,
                    yes_def=request.yes_definition,
                    no_def=request.no_definition,
                    resolution_criteria=request.resolution_criteria,
                    resolution_source=request.resolution_source,
                    status=MarketStatus.PROPOSED,
                    tier=None,
                    close_at=request.close_at,
                    resolve_by=request.resolve_by,
                    created_by=user.id,
                    created_at=now,
                )
            )
            answer = await self.detail_in(uow, user, market.id)

        return answer

    async def list_markets(
        self, viewer: AuthenticatedUser, statuses: Collection[MarketStatus] | None
    ) -> MarketListResponse:
        """Markets by effective status, soonest `close_at` first (`GET /markets`).

        `statuses` is the `status` query, which a request may repeat
        (`?status=OPEN&status=HALTED`); None means it was left out.

        * None: every market whose effective status is one of `PUBLIC_STATUSES`.
        * Otherwise: the markets whose effective status is one of `statuses`. A status
          named twice counts once.
        * PROPOSED, EXPIRED and REJECTED are for admins: a list of any of them reads
          rows only admins and their proposer may see (`HIDDEN_STATUSES`). If one of
          them is among `statuses` and `viewer.is_admin` is false: `ForbiddenError`
          (403), whatever else was asked for with it. Such a list is never cached.

        The effective status differs from the stored one for a market past `close_at`:
        an OPEN one is CLOSED and a PROPOSED one is EXPIRED. So read the stored
        statuses a market of the statuses asked for can have
        (`MarketView.stored_statuses`, `markets.list_by_statuses`), and keep the rows
        whose `MarketView.effective_status` is one that was asked for. A list of
        PROPOSED is therefore the proposals an admin can still decide on, and one
        whose deadline has passed is listed under EXPIRED and nowhere else. `now` is
        read once, so every market of an answer is judged at one instant.

        Caching, for every list that reads no row of `HIDDEN_STATUSES`: the key is
        `MarketCache.list_key(<the statuses asked for; PUBLIC_STATUSES for None>)`,
        which is the same for one set of statuses in any order. A hit is
        `MarketListResponse.model_validate_json(text)`; an entry that does not fit the
        model (one another version of the API left behind) counts as a miss and is
        replaced. A miss reads Postgres (the markets, plus the `resolutions` rows of
        the RESOLVED and VOID ones for `MarketView.summary`, all of them in one query:
        `resolutions.get_many_by_market`) and stores `response.model_dump_json()`.
        The list holds nothing about the viewer, so one entry serves everybody.
        """
        asked = frozenset(PUBLIC_STATUSES if statuses is None else statuses)
        stored = self._view.stored_statuses(asked)
        for_admins = not stored.isdisjoint(HIDDEN_STATUSES)
        if for_admins and not viewer.is_admin:
            raise ForbiddenError

        cacheable = not for_admins
        key = MarketCache.list_key(asked)
        if cacheable:
            cached = await self._cached_list(key)
            if cached is not None:
                return cached

        now = self._clock.now()
        async with self._uow_factory() as uow:
            markets = [
                market
                for market in await uow.markets.list_by_statuses(stored)
                if self._view.effective_status(market, now) in asked
            ]
            settled = [market.id for market in markets if market.status in SETTLED_STATUSES]
            resolutions = await uow.resolutions.get_many_by_market(settled) if settled else {}

        response = MarketListResponse(
            items=[
                self._view.summary(market, now, resolutions.get(market.id)) for market in markets
            ]
        )
        if cacheable:
            await self._cache.set(key, response.model_dump_json())
        return response

    async def detail(self, viewer: AuthenticatedUser, market_id: UUID) -> MarketDetailResponse:
        """One market with its rules, and the viewer's side of it (`GET /markets/{id}`).

        One read transaction around `detail_in`, which says what the answer holds.

        Every answer carries the viewer's side, so none is cached: everything is read
        from Postgres. The statements of the transaction are not one snapshot: a trade
        that commits between two of them shows until the next request.
        """
        async with self._uow_factory() as uow:
            return await self.detail_in(uow, viewer, market_id)

    async def detail_in(
        self, uow: UnitOfWork, viewer: AuthenticatedUser, market_id: UUID
    ) -> MarketDetailResponse:
        """What `viewer` sees of one market, read inside the transaction of `uow`.

        `detail` opens a transaction for it. `propose` and the seven actions of
        `MarketLifecycleService` call it as the last step of the transaction that
        makes their change: the answer of a write is built from what its transaction
        wrote, before the commit, and nothing is read after it (backend/CLAUDE.md,
        "Transactions"). It takes no lock and changes no row.

        The market is read as its row stands in the transaction
        (`markets.get_as_stored`), so an answer built before a commit is the one
        `detail` gives right after it, down to how an instant is written.

        Visibility: an unknown market raises `NotFoundError` (404). So does a PROPOSED
        or REJECTED one unless `viewer.is_admin` or the viewer is its proposer.

        Fields beyond `MarketView.summary`: the wording columns (`yes_def` and
        `no_def` are `yes_definition` and `no_definition`), `void_rule = VOID_RULE`,
        the timestamps, `rejection_reason`, `resolution` from the `resolutions` row
        (`resolved_at` is its `created_at`), and what admins alone are told. The
        `resolutions` row is read, and used, only when the market's own row says
        RESOLVED or VOID.

        For admins only, null to everyone else, the proposer included:

        * `tier`, `b_cr` and `max_loss_cr = budget_micro`, which is how much liquidity
          VIS gave the market (to an admin `tier` is null and the two amounts are 0
          until the approval);
        * `proposer_email`, the proposer's `users.email`;
        * `void_cost_cr = MarketView.void_cost_micro(<the net stakes of the market's
          traders, `positions.net_stake_totals`>, <the balance of its account>)`: what
          VIS would have to add if the market were voided now. It is the figure of
          the market's row on the risk page (`RiskService.snapshot`), from the same
          two readings and the same function. Null to an admin too where there is
          nothing to void: a market that was never opened (`HIDDEN_STATUSES`, an
          expired proposal among them) and one that is settled. So it is set for
          exactly the markets the risk page lists (`UNSETTLED_STATUSES`), and its two
          reads run for nobody but an admin looking at such a market. A market of
          those has its account since its approval; one without is a broken book
          and raises `LookupError` (500).

        `viewer` block, from the viewer's account (`accounts.get_user_account`) and
        their `positions.list_for_user_market`. Every user has an account, because the
        profile transaction creates the two together.

        * `balance_cr`; `holdings = MarketView.holdings(...)`; `cost_cr` and
          `proceeds_cr` summed over both positions; `net_stake_cr =
          MarketView.net_stake_micro(...)`; `stake_limit_cr =
          MarketView.stake_limit_micro`; `stake_left_cr = MarketView.stake_left_micro`.
        * `payout_cr = MarketView.payout_micro(<the positions>, <the resolution>)` and
          `refund_cr = MarketView.refund_micro(...)`: the first is set for a RESOLVED
          market and the second for a VOID one, each null otherwise. They are the
          figures `AccountService.portfolio` reports for the same market.
        * `can_trade` and `cannot_trade_reason`, the first that applies:
          `MARKET_NOT_OPEN` (effective status not OPEN), `OWN_MARKET` (`created_by ==
          viewer.id`); otherwise true and null. A suspended account never gets here:
          the guards of `app/api/deps.py` refuse it.
        * `stake_presets = MarketView.stake_presets(<stake left>, <balance>,
          can_trade)`: one per `TRADE_STAKE_PRESETS_CR`, in that order.
        * `buy_slider = MarketView.buy_slider(<stake left>, <balance>, can_trade)`:
          the whole credits between which a buy passes right now, in steps of
          `TRADE_STAKE_STEP_CR`; null when the viewer cannot buy anything.
        * `sell_presets = MarketView.sell_presets(<the positions>, can_trade)`: half
          and all of each holding, so the client halves and compares nothing (A.11).
        * `can_settle`: `viewer.is_admin and not trades.exists_for_user_market(
          viewer.id, market_id)`. It is the rule by which
          `MarketLifecycleService.resolve` and `void` refuse an admin who traded the
          market (A.4), asked here so that the admin screen can say so before anyone
          tries. The query runs only for an admin; everybody else gets false. The
          market's status plays no part in it.

        The clock is read once, here: the effective status, and with it whether the
        viewer may trade, is the one of that instant.
        """
        now = self._clock.now()
        market = await uow.markets.get_as_stored(market_id)
        if market is None or _hidden_from(viewer, market):
            raise NotFoundError
        resolution = (
            await uow.resolutions.get_by_market(market.id)
            if market.status in SETTLED_STATUSES
            else None
        )
        proposer = await uow.users.get(market.created_by) if viewer.is_admin else None
        account = await uow.accounts.get_user_account(viewer.id)
        positions = await uow.positions.list_for_user_market(viewer.id, market.id)
        can_settle = viewer.is_admin and not await uow.trades.exists_for_user_market(
            viewer.id, market.id
        )
        void_cost_micro = (
            await self._void_cost_micro(uow, market)
            if viewer.is_admin and market.status in UNSETTLED_STATUSES
            else None
        )
        if account is None:
            raise LookupError(f"user {viewer.id} has no ledger account")
        if viewer.is_admin and proposer is None:
            raise LookupError(f"market {market.id} was proposed by nobody")

        summary = self._view.summary(market, now, resolution)
        return MarketDetailResponse(
            **summary.model_dump(),
            yes_definition=market.yes_def,
            no_definition=market.no_def,
            resolution_criteria=market.resolution_criteria,
            resolution_source=market.resolution_source,
            tier=market.tier if viewer.is_admin else None,
            b_cr=to_cr(market.b_micro) if viewer.is_admin else None,
            max_loss_cr=to_cr(market.budget_micro) if viewer.is_admin else None,
            void_cost_cr=_optional_cr(void_cost_micro),
            void_rule=VOID_RULE,
            created_at=market.created_at,
            opened_at=market.opened_at,
            closed_at=market.closed_at,
            rejection_reason=market.rejection_reason,
            resolution=_resolution(resolution),
            proposer_email=None if proposer is None else proposer.email,
            viewer=self._viewer(
                viewer,
                market,
                trading_open=summary.trading_open,
                balance_micro=account.balance_micro,
                positions=positions,
                resolution=resolution,
                can_settle=can_settle,
            ),
        )

    async def _void_cost_micro(self, uow: UnitOfWork, market: Market) -> int:
        """`void_cost_cr` of a market that is not settled, in µCR: its two readings,
        as the risk page takes them, through `MarketView.void_cost_micro`. A market
        nobody holds a position in has no net stakes to refund."""
        account = await uow.accounts.get_market_account(market.id)
        if account is None:
            raise LookupError(f"market {market.id} is {market.status} but has no account")
        net_stakes_micro = await uow.positions.net_stake_totals([market.id])
        return self._view.void_cost_micro(net_stakes_micro.get(market.id, 0), account.balance_micro)

    async def _cached_list(self, key: str) -> MarketListResponse | None:
        """The list cached under `key`; None on a miss, and for an entry that is no
        `MarketListResponse`."""
        cached = await self._cache.get(key)
        if cached is None:
            return None
        try:
            return MarketListResponse.model_validate_json(cached)
        except ValidationError:
            return None

    def _viewer(
        self,
        viewer: AuthenticatedUser,
        market: Market,
        *,
        trading_open: bool,
        balance_micro: int,
        positions: Sequence[Position],
        resolution: Resolution | None,
        can_settle: bool,
    ) -> MarketViewer:
        """The viewer's side of `market`: `positions` are their rows in it, and
        `resolution` is how it ended, None while it has not."""
        reason: TradeBlockReason | None
        if not trading_open:
            reason = TradeBlockReason.MARKET_NOT_OPEN
        elif market.created_by == viewer.id:
            reason = TradeBlockReason.OWN_MARKET
        else:
            reason = None
        can_trade = reason is None

        net_stake_micro = self._view.net_stake_micro(positions)
        stake_left_micro = self._view.stake_left_micro(net_stake_micro)
        return MarketViewer(
            balance_cr=to_cr(balance_micro),
            holdings=self._view.holdings(market, positions, resolution),
            cost_cr=to_cr(sum(position.cost_micro for position in positions)),
            proceeds_cr=to_cr(sum(position.proceeds_micro for position in positions)),
            net_stake_cr=to_cr(net_stake_micro),
            payout_cr=_optional_cr(self._view.payout_micro(positions, resolution)),
            refund_cr=_optional_cr(self._view.refund_micro(positions, resolution)),
            stake_limit_cr=to_cr(self._view.stake_limit_micro),
            stake_left_cr=to_cr(stake_left_micro),
            stake_presets=self._view.stake_presets(stake_left_micro, balance_micro, can_trade),
            buy_slider=self._view.buy_slider(stake_left_micro, balance_micro, can_trade),
            sell_presets=self._view.sell_presets(positions, can_trade),
            can_trade=can_trade,
            cannot_trade_reason=reason,
            can_settle=can_settle,
        )


def _hidden_from(viewer: AuthenticatedUser, market: Market) -> bool:
    """Whether `market` is one `viewer` may not know of: a proposal, whether it is
    still to be decided, expired or rejected, is for admins and its proposer (A.5)."""
    if market.status not in HIDDEN_STATUSES:
        return False
    return not (viewer.is_admin or market.created_by == viewer.id)


def _resolution(resolution: Resolution | None) -> MarketResolution | None:
    if resolution is None:
        return None
    return MarketResolution(
        outcome=resolution.outcome,
        evidence=resolution.evidence,
        resolved_at=resolution.created_at,
    )


def _optional_cr(value_micro: int | None) -> Decimal | None:
    return None if value_micro is None else to_cr(value_micro)


def _days(count: int) -> str:
    return "1 day" if count == 1 else f"{count} days"
