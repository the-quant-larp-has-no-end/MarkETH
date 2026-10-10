"""The admin actions that move a market through its life: `MarketLifecycleService`
(SPEC §5, §6, §12, Appendix A.4, A.5, A.8).

SPEC §12 names three things this file proves: "a second resolution fails",
"settlement pays exactly the winning shares" and "VOID refunds equal net stakes". With
them: who may act at all, which action a status takes, what an approval checks before
it opens a market, and what every action leaves behind (an audit row, dropped lists, a
status event).

Every expected budget, b, payout and refund is what `app.pricing.lmsr` and `MarketView`
make of the rows; every limit is read from the settings a service was built with.
Trades are the factory's buys and the `sell` below, which is their counterpart, so
nothing here waits for the trading service.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.container import Container
from app.core.config import Settings
from app.core.enums import (
    STORED_MARKET_STATUSES,
    AccountKind,
    JournalKind,
    MarketStatus,
    MarketTier,
    OutboxTopic,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, format_cr, to_cr
from app.db.base import Base
from app.integrations.redis.market_cache import MarketCache
from app.integrations.scylla.quote_history import QuotePoint
from app.models import (
    Account,
    AuditLogEntry,
    JournalEntry,
    Market,
    Notice,
    OutboxEvent,
    Position,
    Posting,
    Resolution,
    Trade,
    User,
)
from app.pricing import lmsr
from app.repositories.market import MarketRepository
from app.repositories.trading import TradeRepository
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    ResumeMarketRequest,
    VoidMarketRequest,
)
from app.schemas.common import Limit, RequestModel
from app.schemas.live import StatusEvent
from app.schemas.markets import MarketDetailResponse, TradeBlockReason
from app.services import market_lifecycle
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.ledger import LedgerRef, PostingLine, resolution_key, trade_key, void_key
from app.services.live_quotes import LiveQuoteService
from app.services.market_lifecycle import (
    ALLOWED_TRANSITIONS,
    BUDGET_LIMIT_MESSAGE,
    CLOSE_FIRST_MESSAGE,
    LOSS_BUDGET_LIMIT_MESSAGE,
    OPEN_MARKETS_LIMIT_MESSAGE,
    PROPOSAL_EXPIRED_MESSAGE,
    SELF_DEALING_MESSAGE,
    MarketLifecycleService,
)
from app.services.market_view import EVEN_ODDS_MICRO, UNSETTLED_STATUSES
from app.services.markets import PUBLIC_STATUSES, UNSTORABLE_TEXT_MESSAGE, MarketService
from app.services.notices import NoticeService
from app.services.outbox_events import quote_point_from_payload, quote_updated_payload
from app.services.public_config import FIXED_TIERS
from tests.support.books import assert_books_balance, count_rows, system_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, PortalHeaders, identity_of
from tests.support.settings import load_base_settings
from tests.support.units_of_work import CountedUnitsOfWork

pytestmark = pytest.mark.integration

BASE: Final[Settings] = load_base_settings()
PRECISION: Final[int] = BASE.pricing.decimal_precision
MAX_MARKET_BUDGET: Final[int] = BASE.risk.max_market_budget_micro
LIFETIME: Final[timedelta] = BASE.risk.max_market_lifetime
DELAY: Final[timedelta] = BASE.lifecycle.max_resolution_delay

WALLET: Final[int] = 500 * MICRO
"""What a trader of these tests starts with."""

ADMIN_MARKETS: Final[str] = "/api/admin/markets"
PUBLIC_LIST: Final[str] = MarketCache.list_key(PUBLIC_STATUSES)
"""The cache key of the list a request without `status` gets."""

SECOND: Final[timedelta] = timedelta(seconds=1)
TICK: Final[timedelta] = timedelta(microseconds=1)
DAY: Final[timedelta] = timedelta(days=1)
NUL: Final[str] = "\x00"

RECEIVE_TIMEOUT_SECONDS: Final[float] = 5.0
"""How long a test waits for a published event, before it fails instead of hanging."""

BLOCKED_FOR_SECONDS: Final[float] = 0.3
"""Long enough for a request to reach the row lock it then waits for."""

LINGER_SECONDS: Final[float] = 0.15
"""How long a transaction is kept open by `linger_after`: many times what a parallel
request needs to get a connection and reach the same statement."""

MARKER: Final[Mapping[str, str]] = {"type": "marker"}
"""What a test sends on a market's channel to prove that nothing else is on its way."""

WRITTEN: Final[tuple[type[Base], ...]] = (
    Market,
    Resolution,
    Position,
    Account,
    JournalEntry,
    Posting,
    AuditLogEntry,
    OutboxEvent,
    Notice,
)
"""Every table a lifecycle action can write."""

type Rows = dict[str, list[tuple[object, ...]]]
type Seam = Callable[[User], dict[str, str]]
"""Makes the next request come from a user, and returns the headers to send with it."""


# --- The seven actions -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Action:
    """One admin action, as a test asks for it: the method of the service and the last
    segment of its route have its name."""

    name: str
    request: RequestModel
    target: MarketStatus
    """The status it leaves a market in."""

    starts: tuple[MarketStatus, ...]
    """The stored statuses of the markets it takes (SPEC §6)."""

    audit: AuditAction

    async def by(
        self, service: MarketLifecycleService, actor: AuthenticatedUser, market_id: UUID
    ) -> MarketDetailResponse:
        answer: MarketDetailResponse = await getattr(service, self.name)(
            actor, market_id, self.request
        )
        return answer

    def path(self, market_id: UUID) -> str:
        return f"{ADMIN_MARKETS}/{market_id}/{self.name}"

    @property
    def body(self) -> dict[str, Any]:
        return self.request.model_dump(mode="json", exclude_none=True)

    def __str__(self) -> str:
        return self.name


HALT_REASON: Final[str] = "The source is in doubt."
RESUME_NOTE: Final[str] = "The source is back."
CLOSE_NOTE: Final[str] = "The menu is out early."
EVIDENCE: Final[str] = "The menu of that Friday shows it."
VOID_REASON: Final[str] = "The mensa was closed that day."

APPROVE: Final[Action] = Action(
    "approve",
    ApproveMarketRequest(tier=MarketTier.DEFAULT),
    MarketStatus.OPEN,
    (MarketStatus.PROPOSED,),
    AuditAction.MARKET_APPROVE,
)
REJECT: Final[Action] = Action(
    "reject",
    RejectMarketRequest(reason="It names a private person."),
    MarketStatus.REJECTED,
    (MarketStatus.PROPOSED,),
    AuditAction.MARKET_REJECT,
)
HALT: Final[Action] = Action(
    "halt",
    HaltMarketRequest(reason=HALT_REASON),
    MarketStatus.HALTED,
    (MarketStatus.OPEN,),
    AuditAction.MARKET_HALT,
)
RESUME: Final[Action] = Action(
    "resume",
    ResumeMarketRequest(note=RESUME_NOTE),
    MarketStatus.OPEN,
    (MarketStatus.HALTED,),
    AuditAction.MARKET_RESUME,
)
CLOSE: Final[Action] = Action(
    "close",
    CloseMarketRequest(note=CLOSE_NOTE),
    MarketStatus.CLOSED,
    (MarketStatus.OPEN, MarketStatus.HALTED),
    AuditAction.MARKET_CLOSE,
)
RESOLVE: Final[Action] = Action(
    "resolve",
    ResolveMarketRequest(outcome=Outcome.YES, evidence=EVIDENCE),
    MarketStatus.RESOLVED,
    (MarketStatus.CLOSED, MarketStatus.HALTED),
    AuditAction.MARKET_RESOLVE,
)
VOID: Final[Action] = Action(
    "void",
    VoidMarketRequest(reason=VOID_REASON),
    MarketStatus.VOID,
    (MarketStatus.CLOSED, MarketStatus.HALTED),
    AuditAction.MARKET_VOID,
)

ACTIONS: Final[tuple[Action, ...]] = (APPROVE, REJECT, HALT, RESUME, CLOSE, RESOLVE, VOID)
SETTLEMENTS: Final[tuple[Action, ...]] = (RESOLVE, VOID)
PUBLISHED: Final[tuple[Action, ...]] = (APPROVE, HALT, RESUME, CLOSE, RESOLVE, VOID)
"""The actions on a market that is or becomes public: each drops the cached lists and
publishes a status event. A rejection does neither."""

every_action = pytest.mark.parametrize("action", ACTIONS, ids=str)
both_settlements = pytest.mark.parametrize("action", SETTLEMENTS, ids=str)


def resolve_to(outcome: Outcome) -> ResolveMarketRequest:
    return ResolveMarketRequest(outcome=outcome, evidence=EVIDENCE)


# --- Reading what was written ------------------------------------------------------------


async def everything(container: Container) -> Rows:
    """Every row of every table a lifecycle action can write. Two readings are equal
    exactly when nothing was written in between."""
    found: Rows = {}
    async with container.uow_factory() as uow:
        for model in WRITTEN:
            table = Base.metadata.tables[model.__tablename__]
            rows = await uow.session.execute(select(table).order_by(*table.primary_key.columns))
            found[table.name] = [tuple(row) for row in rows]
    return found


def held_in(market: Market, positions: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    """The rows of `market` out of the `positions` of a reading of `everything`: user,
    market, outcome, shares, cost, proceeds."""
    return [held for held in positions if held[1] == market.id]


async def market_row(container: Container, market_id: UUID) -> Market:
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market_id)
    assert row is not None
    return row


async def resolution_of(container: Container, market: Market) -> Resolution | None:
    async with container.uow_factory() as uow:
        return await uow.resolutions.get_by_market(market.id)


async def positions_of(container: Container, user: User, market: Market) -> Sequence[Position]:
    async with container.uow_factory() as uow:
        return await uow.positions.list_for_user_market(user.id, market.id)


async def positions_in(container: Container, market: Market) -> Sequence[Position]:
    async with container.uow_factory() as uow:
        return await uow.positions.list_for_market(market.id)


async def audit_rows(container: Container) -> Sequence[AuditLogEntry]:
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))).all()


async def market_balance(container: Container, market: Market) -> int:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_market_account(market.id)
    assert account is not None
    return account.balance_micro


async def pnl(container: Container) -> int:
    """The balance of `vis:mm_pnl`, which takes what a settlement leaves over."""
    return await system_balance(container.uow_factory, AccountKind.VIS_MM_PNL)


async def settlement_postings(container: Container, key: str) -> dict[UUID, int] | None:
    """The postings of the journal entry booked under `key`, by account; None when no
    such entry was booked."""
    async with container.uow_factory() as uow:
        entry = await uow.journal.get_entry_by_idempotency_key(key)
        if entry is None:
            return None
        postings = await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
        return {posting.account_id: posting.amount_micro for posting in postings}


async def account_id_of(container: Container, user: User) -> UUID:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id)
    assert account is not None
    return account.id


async def sell(
    container: Container, user: User, market: Market, outcome: Outcome, shares_micro: int
) -> int:
    """Let `user` sell shares they hold, as a committed trade would (A.4, A.8): the
    counterpart of `Factory.position`. Returns the proceeds lmsr quotes. `market` is
    brought up to the committed state in place."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        market_account = await uow.accounts.get_market_account(market.id)
        position = await uow.positions.get(user.id, market.id, outcome)
        assert row is not None
        assert account is not None
        assert market_account is not None
        assert position is not None
        state = lmsr.MarketState(q_yes=row.q_yes_micro, q_no=row.q_no_micro, b=row.b_micro)
        quote = lmsr.quote_sell_shares(state, outcome, shares_micro, precision=PRECISION)
        trade_id = uuid7()
        if quote.amount_micro:
            await container.ledger.post(
                uow,
                kind=JournalKind.TRADE_SELL,
                ref_type=LedgerRef.TRADE,
                ref_id=trade_id,
                idempotency_key=trade_key(trade_id),
                lines=[
                    PostingLine(market_account.id, -quote.amount_micro),
                    PostingLine(account.id, quote.amount_micro),
                ],
                at=now,
            )
        position.shares_micro -= shares_micro
        position.proceeds_micro += quote.amount_micro
        row.q_yes_micro = quote.after.q_yes
        row.q_no_micro = quote.after.q_no
        row.version += 1
        row.volume_micro += quote.amount_micro
        await uow.trades.add(
            Trade(
                id=trade_id,
                market_id=market.id,
                user_id=user.id,
                side=TradeSide.SELL,
                outcome=outcome,
                shares_micro=shares_micro,
                amount_micro=quote.amount_micro,
                p_yes_before_micro=quote.p_yes_before_micro,
                p_yes_after_micro=quote.p_yes_after_micro,
                market_version=row.version,
                created_at=now,
            )
        )
    market.q_yes_micro = row.q_yes_micro
    market.q_no_micro = row.q_no_micro
    market.version = row.version
    market.volume_micro = row.volume_micro
    return quote.amount_micro


# --- Services, people and the channel ----------------------------------------------------


def lifecycle_with(container: Container, **risk: int) -> MarketLifecycleService:
    """A lifecycle service wired like the container's, with other risk limits
    (`max_open_markets`, `max_market_budget_micro`, `max_aggregate_loss_budget_micro`)."""
    settings = container.settings.model_copy(
        update={"risk": container.settings.risk.model_copy(update=risk)}
    )
    return MarketLifecycleService(
        container.uow_factory,
        settings,
        container.clock,
        container.market_view,
        container.ledger,
        container.audit,
        container.markets,
        container.notices,
        container.market_cache,
        container.live_quotes,
    )


def lifecycle_on(
    container: Container, *, at_most: int | None = None
) -> tuple[MarketLifecycleService, CountedUnitsOfWork]:
    """A lifecycle service wired like the container's, on a unit-of-work factory that
    counts. Every collaborator that could open a unit of work has the same factory, so
    one opened by any of them is counted; with `at_most` the factory hands out that
    many and then stands for a pool without a connection."""
    units = CountedUnitsOfWork(container.uow_factory, at_most=at_most)
    settings, clock, view = container.settings, container.clock, container.market_view
    lifecycle = MarketLifecycleService(
        units,
        settings,
        clock,
        view,
        container.ledger,
        container.audit,
        MarketService(units, settings, clock, view, container.market_cache),
        NoticeService(units, clock),
        container.market_cache,
        LiveQuoteService(units, settings, clock, view, container.quote_bus),
    )
    return lifecycle, units


async def market_for(container: Container, factory: Factory, action: Action) -> Market:
    """A market `action` takes: a proposal for the two that decide on one, and for the
    other five a market four people traded, so that its answer has figures in it."""
    start = action.starts[0]
    if start is MarketStatus.PROPOSED:
        return await factory.market(status=start)
    return (await traded(container, factory, start)).market


def linger_after(monkeypatch: pytest.MonkeyPatch, repository: type, query: str) -> None:
    """Keep every transaction open for a moment after it ran `query` of `repository`.

    Two requests sent at once overlap only if the first is still in its transaction
    when the second gets as far. Left alone, the first is often done before the second
    has its connection, and a missing lock would go unnoticed. With this, a second
    request reaches the same point while the first lingers there, unless a lock keeps
    it waiting.
    """
    original: Callable[..., Awaitable[Any]] = getattr(repository, query)

    async def lingering(self: object, *args: Any, **kwargs: Any) -> Any:
        answer = await original(self, *args, **kwargs)
        await asyncio.sleep(LINGER_SECONDS)
        return answer

    monkeypatch.setattr(repository, query, lingering)


def tier_budget(tier: MarketTier) -> int:
    """The budget approval gives a fixed tier: the loss bound of its b, in µCR."""
    return lmsr.max_loss_micro(BASE.pricing.tier_b_micro(tier), precision=PRECISION)


@pytest.fixture
async def admin(factory: Factory) -> AuthenticatedUser:
    """The first admin of the test settings, who trades nothing."""
    return factory.identity(await factory.admin(0))


@pytest.fixture
def lifecycle(container: Container) -> MarketLifecycleService:
    return container.market_lifecycle


@pytest.fixture(params=["as_user", "portal_headers"])
def acting(request: pytest.FixtureRequest, as_user: ActAs, portal_headers: PortalHeaders) -> Seam:
    """Both ways a test can say who is calling: the `as_user` seam, and the two headers
    of the portal, which go through the real `authenticate_request`. A rule about who
    may do something has to hold on both."""
    if request.param == "portal_headers":
        return portal_headers

    def through_the_seam(user: User) -> dict[str, str]:
        as_user(user)
        return {}

    return through_the_seam


@asynccontextmanager
async def watching(
    container: Container, market_id: UUID
) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    """What is published on a market's channel while the block is open. Straight from
    the bus, so a proposal can be watched too, which no stream is opened for."""
    async with container.quote_bus.subscribe(market_id) as queue:
        yield queue


async def next_message(queue: asyncio.Queue[dict[str, Any]]) -> dict[str, Any]:
    return await asyncio.wait_for(queue.get(), timeout=RECEIVE_TIMEOUT_SECONDS)


async def nothing_more(
    container: Container, market_id: UUID, queue: asyncio.Queue[dict[str, Any]]
) -> None:
    """Whatever was published before this call has arrived: a marker sent now is the
    next message."""
    await container.quote_bus.publish(market_id, dict(MARKER))
    assert await next_message(queue) == MARKER


def status_event(container: Container, market: Market) -> dict[str, Any]:
    """The status event of `market` as it stands now, in the status `MarketView`
    reports, as it travels."""
    view, now = container.market_view, container.clock.now()
    event = StatusEvent(
        type="status",
        market_id=market.id,
        status=view.effective_status(market, now),
        trading_open=view.is_trading_open(market, now),
        ts=now,
    )
    return event.model_dump(mode="json")


def target_of(market: Market) -> str:
    return AuditService.target(AuditTarget.MARKET, market.id)


# --- A market people traded --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Book:
    """A market four people went into, in this order: dave bought 50 YES, alice bought
    80 YES, dave sold his 50 at the price alice had moved it to, bob bought 30 NO, and
    carol bought 20 YES and 10 NO and sold 5 YES again.

    So alice holds YES and bob NO, carol holds both, and dave holds nothing: he took
    more out than he put in.
    """

    market: Market
    alice: User
    bob: User
    carol: User
    dave: User

    @property
    def traders(self) -> tuple[User, ...]:
        return (self.alice, self.bob, self.carol, self.dave)


async def traded(
    container: Container, factory: Factory, status: MarketStatus = MarketStatus.CLOSED
) -> Book:
    alice, bob, carol, dave = [await factory.user(balance_micro=WALLET) for _ in range(4)]
    market = await factory.market(status=status)
    await factory.position(dave, market, Outcome.YES, shares_micro=50 * MICRO)
    await factory.position(alice, market, Outcome.YES, shares_micro=80 * MICRO)
    await sell(container, dave, market, Outcome.YES, 50 * MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=30 * MICRO)
    await factory.position(carol, market, Outcome.YES, shares_micro=20 * MICRO)
    await factory.position(carol, market, Outcome.NO, shares_micro=10 * MICRO)
    await sell(container, carol, market, Outcome.YES, 5 * MICRO)
    return Book(market, alice, bob, carol, dave)


async def balances(factory: Factory, users: Sequence[User]) -> dict[UUID, int]:
    return {user.id: await factory.balance_micro(user) for user in users}


# --- Who may act -------------------------------------------------------------------------


@every_action
async def test_the_service_refuses_whoever_is_no_admin_before_it_reads_anything(
    container: Container, factory: Factory, lifecycle: MarketLifecycleService, action: Action
) -> None:
    """The routes have a guard of their own. The rule also holds for whoever else calls
    the service: 403 even for a market that does not exist, which only a read could
    have told."""
    proposer = await factory.user()
    market = await factory.market(status=action.starts[0], created_by=proposer)
    plain = factory.identity(await factory.user())
    barista = factory.identity(await factory.barista())
    suspended = identity_of(await factory.user(status=UserStatus.SUSPENDED), UserRole.ADMIN)
    before = await everything(container)

    for nobody in (plain, barista, factory.identity(proposer), suspended):
        for market_id in (market.id, uuid7()):
            with pytest.raises(ForbiddenError) as refused:
                await action.by(lifecycle, nobody, market_id)
            assert refused.value.status_code == 403
            assert refused.value.message == ForbiddenError.default_message

    assert await everything(container) == before


@every_action
async def test_an_admin_asking_about_a_market_nobody_knows_gets_404(
    container: Container,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    with pytest.raises(NotFoundError):
        await action.by(lifecycle, admin, uuid7())

    assert await count_rows(container.uow_factory, AuditLogEntry) == 0


@both_settlements
async def test_through_the_route_a_user_a_barista_and_the_proposer_get_403_and_write_nothing(
    client: httpx.AsyncClient,
    acting: Seam,
    container: Container,
    factory: Factory,
    action: Action,
) -> None:
    """ONLY AN ADMIN SETTLES. Holding shares of the market gives no say in it, having
    proposed it gives none, and scanning vouchers gives none."""
    proposer = await factory.user()
    trader = await factory.user(balance_micro=WALLET)
    barista = await factory.barista()
    market = await factory.market(status=MarketStatus.CLOSED, created_by=proposer)
    await factory.position(trader, market, Outcome.YES, shares_micro=60 * MICRO)
    before = await everything(container)

    for nobody in (trader, barista, proposer):
        response = await client.post(
            action.path(market.id), json=action.body, headers=acting(nobody)
        )
        assert response.status_code == 403, response.text
        assert response.json() == {
            "code": "FORBIDDEN",
            "message": ForbiddenError.default_message,
            "details": {},
        }

    assert await everything(container) == before
    assert (await market_row(container, market.id)).status is MarketStatus.CLOSED
    assert await resolution_of(container, market) is None
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_an_admin_who_traded_the_market_gets_the_self_dealing_403_and_a_second_admin_settles(
    client: httpx.AsyncClient,
    acting: Seam,
    container: Container,
    factory: Factory,
    action: Action,
) -> None:
    """A.4: a resolution could pay that admin, and a void would refund their stake."""
    first = await factory.admin(0, balance_micro=WALLET)
    second = await factory.admin(1)
    market = await factory.market(status=MarketStatus.CLOSED)
    await factory.position(first, market, Outcome.YES, shares_micro=60 * MICRO)
    before = await everything(container)

    refused = await client.post(action.path(market.id), json=action.body, headers=acting(first))

    assert refused.status_code == 403, refused.text
    assert refused.json() == {
        "code": "FORBIDDEN",
        "message": SELF_DEALING_MESSAGE,
        "details": {},
    }
    assert await everything(container) == before

    settled = await client.post(action.path(market.id), json=action.body, headers=acting(second))
    again = await client.post(action.path(market.id), json=action.body, headers=acting(second))

    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == action.target
    assert settled.json()["viewer"]["can_settle"] is True
    assert (again.status_code, again.json()["code"]) == (409, "CONFLICT")
    resolution = await resolution_of(container, market)
    assert resolution is not None
    assert resolution.resolved_by == second.id
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_the_service_refuses_an_admin_who_ever_traded_the_market_whatever_they_hold_now(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    action: Action,
) -> None:
    """An admin who sold everything again is still one who traded the market. One who
    traded some other market is not meant."""
    first = await factory.admin(0, balance_micro=WALLET)
    second = await factory.admin(1, balance_micro=WALLET)
    market = await factory.market()
    other = await factory.market()
    await factory.position(first, market, Outcome.YES, shares_micro=60 * MICRO)
    await sell(container, first, market, Outcome.YES, 60 * MICRO)
    await factory.position(second, other, Outcome.NO, shares_micro=10 * MICRO)
    # Closing is no settlement: the admin who traded may still stop the trading.
    await lifecycle.close(factory.identity(first), market.id, CloseMarketRequest())
    before = await everything(container)

    with pytest.raises(ForbiddenError) as refused:
        await action.by(lifecycle, factory.identity(first), market.id)

    assert refused.value.message == SELF_DEALING_MESSAGE
    assert await everything(container) == before
    answer = await action.by(lifecycle, factory.identity(second), market.id)
    assert answer.status is action.target
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_self_dealing_is_told_before_the_state_of_the_market(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    action: Action,
) -> None:
    """The admin who traded an OPEN market is told to leave it to another admin, not to
    close it first: closing would not make the settlement theirs."""
    first = await factory.admin(0, balance_micro=WALLET)
    market = await factory.market()
    await factory.position(first, market, Outcome.YES, shares_micro=60 * MICRO)

    with pytest.raises(ForbiddenError, match=SELF_DEALING_MESSAGE):
        await action.by(lifecycle, factory.identity(first), market.id)
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_an_admin_may_settle_a_market_they_proposed(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    action: Action,
) -> None:
    """A proposer cannot trade their market (A.4), so they hold no stake in it."""
    proposer = await factory.admin(0)
    trader = await factory.user(balance_micro=WALLET)
    market = await factory.market(status=MarketStatus.CLOSED, created_by=proposer)
    await factory.position(trader, market, Outcome.YES, shares_micro=60 * MICRO)

    answer = await action.by(lifecycle, factory.identity(proposer), market.id)

    assert answer.status is action.target
    await assert_books_balance(container.uow_factory)


# --- Which action a status takes ---------------------------------------------------------


def test_the_actions_of_these_tests_are_the_arrows_of_the_state_machine() -> None:
    """Each action takes a market along one arrow of `ALLOWED_TRANSITIONS`, and a
    settlement may take the arrow to CLOSED first. No arrow is nobody's."""
    walked: set[tuple[MarketStatus, MarketStatus]] = set()
    for action in ACTIONS:
        for start in action.starts:
            if action.target in ALLOWED_TRANSITIONS[start]:
                walked.add((start, action.target))
            else:
                assert action in SETTLEMENTS
                assert MarketStatus.CLOSED in ALLOWED_TRANSITIONS[start]
                assert action.target in ALLOWED_TRANSITIONS[MarketStatus.CLOSED]

    assert walked == {
        (start, target) for start, targets in ALLOWED_TRANSITIONS.items() for target in targets
    }


@pytest.mark.parametrize("status", STORED_MARKET_STATUSES, ids=str)
@every_action
async def test_an_action_takes_a_market_in_a_status_of_its_own_and_is_409_for_every_other(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
    status: MarketStatus,
) -> None:
    """All seven actions on all seven stored statuses. An action that ends where another
    one does still takes its own markets only: a halted market is resumed, not approved,
    though both end OPEN."""
    market = await factory.market(status=status)
    before = await everything(container)

    if status in action.starts:
        answer = await action.by(lifecycle, admin, market.id)

        assert answer.status is action.target
        assert (await market_row(container, market.id)).status is action.target
        return

    with pytest.raises(ConflictError) as refused:
        await action.by(lifecycle, admin, market.id)

    assert refused.value.status_code == 409
    assert await everything(container) == before


# --- An expired proposal -----------------------------------------------------------------

from_the_deadline_on = pytest.mark.parametrize(
    "late_by", [timedelta(0), TICK, DAY, 400 * DAY], ids=["at", "a-tick", "a-day", "a-year"]
)


@from_the_deadline_on
@pytest.mark.parametrize("action", [APPROVE, REJECT], ids=str)
async def test_an_expired_proposal_can_be_neither_approved_nor_rejected(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
    late_by: timedelta,
) -> None:
    """From the instant of `close_at` on: 409 with a sentence of its own, and nothing
    is written. The row still says PROPOSED and is reported EXPIRED."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))
    clock.set(proposal.close_at + late_by)
    before = await everything(container)

    with pytest.raises(ConflictError) as refused:
        await action.by(lifecycle, admin, proposal.id)

    assert (refused.value.status_code, refused.value.message) == (409, PROPOSAL_EXPIRED_MESSAGE)
    assert refused.value.details == {}
    assert PROPOSAL_EXPIRED_MESSAGE == "This proposal expired: its closing time has passed."
    assert await everything(container) == before
    assert (await market_row(container, proposal.id)).status is MarketStatus.PROPOSED
    assert (await container.markets.detail(admin, proposal.id)).status is MarketStatus.EXPIRED


@pytest.mark.parametrize("action", [APPROVE, REJECT], ids=str)
async def test_a_proposal_is_decided_on_until_the_last_moment_before_its_deadline(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))
    clock.set(proposal.close_at - TICK)

    answer = await action.by(lifecycle, admin, proposal.id)

    assert answer.status is action.target
    assert (await market_row(container, proposal.id)).status is action.target


async def test_no_date_an_approval_carries_reopens_an_expired_proposal(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The expiry is a fact about the proposal, judged before the request is looked
    at: a new deadline in the request does not undo it, and neither does a request
    that would be refused for other reasons be told those first."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))
    clock.set(proposal.close_at)
    before = await everything(container)
    moved = ApproveMarketRequest(
        tier=MarketTier.TIGHT, close_at=clock.now() + DAY, resolve_by=clock.now() + 2 * DAY
    )
    oversized = ApproveMarketRequest(
        tier=MarketTier.CUSTOM, budget_cr=to_cr(MAX_MARKET_BUDGET + 1), close_at=clock.now() - DAY
    )

    for request in (moved, oversized):
        with pytest.raises(ConflictError) as refused:
            await lifecycle.approve(admin, proposal.id, request)
        assert refused.value.message == PROPOSAL_EXPIRED_MESSAGE

    assert await everything(container) == before


@every_action
async def test_no_action_takes_an_expired_proposal(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """The other five do not start from a proposal at all; the two that do are told
    that it expired."""
    expired = await factory.market(status=MarketStatus.PROPOSED, close_in=-DAY)
    before = await everything(container)

    with pytest.raises(ConflictError) as refused:
        await action.by(lifecycle, admin, expired.id)

    told_why = refused.value.message == PROPOSAL_EXPIRED_MESSAGE
    assert told_why == (action in (APPROVE, REJECT))
    assert await everything(container) == before


async def test_a_rejected_proposal_stays_rejected_past_its_deadline(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Only a proposal nobody decided on expires. One that was turned down in time
    keeps saying so, with its reason."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))
    await REJECT.by(lifecycle, admin, proposal.id)

    clock.set(proposal.close_at + DAY)

    detail = await container.markets.detail(admin, proposal.id)
    assert (detail.status, detail.rejection_reason) == (
        MarketStatus.REJECTED,
        "It names a private person.",
    )
    for action in (APPROVE, REJECT):
        with pytest.raises(ConflictError) as refused:
            await action.by(lifecycle, admin, proposal.id)
        assert refused.value.message != PROPOSAL_EXPIRED_MESSAGE


async def test_a_rejection_that_waited_for_the_market_row_is_judged_when_it_got_it(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The clock is read after the lock, as for every other action (A.4): a rejection
    that arrives in time and gets the row after the deadline finds the proposal
    expired."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))

    async with container.uow_factory() as holder:
        await holder.markets.get_for_update(proposal.id)
        rejecting = asyncio.create_task(REJECT.by(lifecycle, admin, proposal.id))
        await asyncio.sleep(BLOCKED_FOR_SECONDS)
        assert not rejecting.done()
        clock.advance(timedelta(hours=2))

    with pytest.raises(ConflictError) as refused:
        await asyncio.wait_for(rejecting, RECEIVE_TIMEOUT_SECONDS)

    assert refused.value.message == PROPOSAL_EXPIRED_MESSAGE
    row = await market_row(container, proposal.id)
    assert (row.status, row.rejection_reason) == (MarketStatus.PROPOSED, None)


@pytest.mark.parametrize("action", [APPROVE, REJECT], ids=str)
async def test_the_routes_answer_409_with_the_sentence_for_an_expired_proposal(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    action: Action,
) -> None:
    as_user(await factory.admin())
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))
    clock.set(proposal.close_at)

    response = await client.post(action.path(proposal.id), json=action.body)

    assert response.status_code == 409
    assert response.json() == {
        "code": "CONFLICT",
        "message": "This proposal expired: its closing time has passed.",
        "details": {},
    }
    assert await count_rows(container.uow_factory, AuditLogEntry) == 0
    assert await count_rows(container.uow_factory, Notice) == 0


# --- Approval: tiers and budgets ---------------------------------------------------------


@pytest.mark.parametrize("tier", FIXED_TIERS)
async def test_approving_a_fixed_tier_opens_the_market_with_the_b_of_the_tier_and_its_loss_bound(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    tier: MarketTier,
) -> None:
    """A.5: the tier fixes b, and the budget is ceil(b · ln 2). The admin picks the
    tier: a proposal names none."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    opened_at = clock.advance(timedelta(hours=3))

    answer = await lifecycle.approve(admin, proposal.id, ApproveMarketRequest(tier=tier))

    row = await market_row(container, proposal.id)
    b_micro = container.settings.pricing.tier_b_micro(tier)
    assert (row.status, row.tier) == (MarketStatus.OPEN, tier)
    assert (row.b_micro, row.budget_micro) == (
        b_micro,
        lmsr.max_loss_micro(b_micro, precision=PRECISION),
    )
    assert (row.approved_by, row.opened_at, row.created_at) == (
        admin.id,
        opened_at,
        proposal.created_at,
    )
    assert (row.q_yes_micro, row.q_no_micro, row.version, row.volume_micro) == (0, 0, 0, 0)
    assert answer == await container.markets.detail(admin, proposal.id)
    assert (answer.status, answer.trading_open, answer.p_yes) == (MarketStatus.OPEN, True, 0.5)
    assert (answer.b_cr, answer.max_loss_cr) == (to_cr(b_micro), to_cr(row.budget_micro))


@pytest.mark.parametrize(
    "budget_micro",
    [1, 50 * MICRO, tier_budget(MarketTier.DEFAULT) - 1, MAX_MARKET_BUDGET],
    ids=["one-micro-credit", "fifty-credits", "just-under-the-default-tier", "the-largest"],
)
async def test_approving_a_custom_budget_derives_the_largest_b_whose_loss_fits_it(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    budget_micro: int,
) -> None:
    """A.5: b = floor(budget / ln 2), so b · ln 2 <= budget <= MAX_MARKET_BUDGET."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    request = ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=to_cr(budget_micro))

    answer = await lifecycle.approve(admin, proposal.id, request)

    row = await market_row(container, proposal.id)
    b_micro = lmsr.b_from_budget_micro(budget_micro, precision=PRECISION)
    assert (row.status, row.tier) == (MarketStatus.OPEN, MarketTier.CUSTOM)
    assert (row.budget_micro, row.b_micro) == (budget_micro, b_micro)
    assert lmsr.max_loss_micro(b_micro, precision=PRECISION) <= budget_micro
    assert lmsr.max_loss_micro(b_micro + 1, precision=PRECISION) > budget_micro
    assert (answer.tier, answer.max_loss_cr) == (MarketTier.CUSTOM, to_cr(budget_micro))


async def test_a_custom_budget_above_max_market_budget_is_409_naming_the_limit(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    before = await everything(container)
    too_much = ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=to_cr(MAX_MARKET_BUDGET + 1))

    with pytest.raises(LimitExceededError) as refused:
        await lifecycle.approve(admin, proposal.id, too_much)

    assert refused.value.status_code == 409
    assert refused.value.details == {"limit": Limit.MAX_MARKET_BUDGET}
    assert refused.value.message == BUDGET_LIMIT_MESSAGE
    assert await everything(container) == before


def first_unquotable_budget() -> int:
    """The smallest budget whose b the quote functions refuse at the digits of the test
    settings, in µCR."""

    def quotable(budget_micro: int) -> bool:
        b_micro = lmsr.b_from_budget_micro(budget_micro, precision=PRECISION)
        return lmsr.is_quotable(b_micro, precision=PRECISION)

    covered, beyond = MAX_MARKET_BUDGET, MAX_MARKET_BUDGET
    while quotable(beyond):
        covered, beyond = beyond, 2 * beyond
    while beyond - covered > 1:
        middle = (covered + beyond) // 2
        if quotable(middle):
            covered = middle
        else:
            beyond = middle
    return beyond


async def test_a_budget_whose_b_the_quote_functions_refuse_opens_no_market_whatever_the_cap_says(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The database keeps b for as long as a market is OPEN, so one opened with a b
    nobody can quote could not be traded and not be repaired. Construction refuses
    settings that allow it; approval asks again for the market in front of it, and this
    test takes the first check away to see the second."""
    monkeypatch.setattr(market_lifecycle, "check_liquidity_settings", lambda settings: None)
    beyond = first_unquotable_budget()
    permissive = lifecycle_with(
        container, max_market_budget_micro=2 * beyond, max_aggregate_loss_budget_micro=4 * beyond
    )
    refused_one = await factory.market(status=MarketStatus.PROPOSED)
    taken_one = await factory.market(status=MarketStatus.PROPOSED)

    with pytest.raises(LimitExceededError) as refused:
        await permissive.approve(
            admin,
            refused_one.id,
            ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=to_cr(beyond)),
        )
    await permissive.approve(
        admin,
        taken_one.id,
        ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=to_cr(beyond - 1)),
    )

    assert refused.value.details == {"limit": Limit.MAX_MARKET_BUDGET}
    assert (await market_row(container, refused_one.id)).status is MarketStatus.PROPOSED
    opened = await market_row(container, taken_one.id)
    assert lmsr.is_quotable(opened.b_micro, precision=PRECISION)
    assert not lmsr.is_quotable(
        lmsr.b_from_budget_micro(beyond, precision=PRECISION), precision=PRECISION
    )


@hypothesis_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    budget_micro=st.one_of(
        st.integers(min_value=1, max_value=MAX_MARKET_BUDGET),
        st.integers(min_value=1, max_value=1000),
        st.integers(min_value=0, max_value=1000).map(lambda below: MAX_MARKET_BUDGET - below),
    )
)
async def test_whatever_budget_an_approval_lets_through_gives_a_market_that_can_be_priced(
    container: Container, factory: Factory, admin: AuthenticatedUser, budget_micro: int
) -> None:
    """For any CUSTOM budget up to MAX_MARKET_BUDGET: the market opens with the b of
    that budget, the database takes it (b · ln 2 <= budget is a CHECK), and lmsr quotes
    a buy on it.

    Every example opens a market of its own, so the two caps are lifted here.
    """
    roomy = lifecycle_with(
        container, max_open_markets=10**6, max_aggregate_loss_budget_micro=10**15
    )
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    request = ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=to_cr(budget_micro))

    await roomy.approve(admin, proposal.id, request)

    row = await market_row(container, proposal.id)
    assert row.b_micro == lmsr.b_from_budget_micro(budget_micro, precision=PRECISION)
    assert row.budget_micro == budget_micro
    state = lmsr.MarketState(q_yes=0, q_no=0, b=row.b_micro)
    quote = lmsr.quote_buy_for_budget(state, Outcome.YES, MICRO, precision=PRECISION)
    assert quote.amount_micro == MICRO


# --- Approval: what it leaves behind -----------------------------------------------------


async def test_approval_opens_the_markets_account_empty_and_moves_no_credit(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """A market starts at q = 0 with an empty account; what it loses is booked when it
    settles (A.8)."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    async with container.uow_factory() as uow:
        assert await uow.accounts.get_market_account(proposal.id) is None

    await lifecycle.approve(admin, proposal.id, ApproveMarketRequest(tier=MarketTier.TIGHT))

    assert await market_balance(container, proposal) == 0
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    assert await pnl(container) == 0
    await assert_books_balance(container.uow_factory)


async def test_approval_queues_the_opening_point_of_the_chart(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Version 0, 50%, no trade: every chart has a start (A.4). The relay delivers it."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    opened_at = clock.advance(timedelta(minutes=10))

    await lifecycle.approve(admin, proposal.id, ApproveMarketRequest(tier=MarketTier.TIGHT))

    async with container.uow_factory() as uow:
        (queued,) = (await uow.session.scalars(select(OutboxEvent))).all()
    opening = QuotePoint(proposal.id, opened_at, 0, EVEN_ODDS_MICRO, 0, 0, None)
    assert queued.topic is OutboxTopic.QUOTE_UPDATED
    assert queued.payload == quote_updated_payload(opening)
    assert quote_point_from_payload(queued.payload) == opening
    assert (queued.created_at, queued.published_at) == (opened_at, None)


async def test_approval_writes_its_audit_row_with_the_tier_the_budget_and_b(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    at = clock.advance(timedelta(minutes=10))

    await lifecycle.approve(admin, proposal.id, ApproveMarketRequest(tier=MarketTier.TIGHT))

    (row,) = await audit_rows(container)
    b_micro = container.settings.pricing.tier_b_micro(MarketTier.TIGHT)
    assert (row.actor_id, row.action, row.target, row.created_at) == (
        admin.id,
        AuditAction.MARKET_APPROVE,
        target_of(proposal),
        at,
    )
    assert row.before == {"status": "PROPOSED", "tier": None, "budget_micro": 0, "b_micro": 0}
    assert row.after == {
        "status": "OPEN",
        "tier": "TIGHT",
        "budget_micro": tier_budget(MarketTier.TIGHT),
        "b_micro": b_micro,
    }


# --- Approval: edits and dates -----------------------------------------------------------


async def test_approval_applies_the_edits_it_carries_and_keeps_what_it_leaves_out(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The admin may reword a proposal and move its two dates (SPEC §6). The audit row
    keeps each changed term as it was and as it is."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    zurich = container.settings.app.timezone
    close_at = (clock.now() + 3 * DAY).astimezone(zurich)
    resolve_by = clock.now() + 5 * DAY
    request = ApproveMarketRequest(
        tier=MarketTier.DEFAULT,
        question="Will the Polymensa serve Älplermagronen this Friday at noon?",
        no_definition="It is missing from the menu at noon.",
        close_at=close_at,
        resolve_by=resolve_by,
    )

    answer = await lifecycle.approve(admin, proposal.id, request)

    row = await market_row(container, proposal.id)
    assert (row.question, row.no_def) == (request.question, request.no_definition)
    assert (row.close_at, row.resolve_by) == (close_at, resolve_by)
    assert (row.yes_def, row.resolution_criteria, row.resolution_source) == (
        proposal.yes_def,
        proposal.resolution_criteria,
        proposal.resolution_source,
    )
    assert (answer.question, answer.close_at) == (request.question, clock.now() + 3 * DAY)
    (audited,) = await audit_rows(container)
    assert audited.before is not None
    assert audited.after is not None
    edited = {"question", "no_definition", "close_at", "resolve_by"}
    assert (
        set(audited.before)
        == set(audited.after)
        == edited
        | {
            "status",
            "tier",
            "budget_micro",
            "b_micro",
        }
    )
    assert (audited.before["question"], audited.after["question"]) == (
        proposal.question,
        request.question,
    )
    assert (audited.before["no_definition"], audited.after["no_definition"]) == (
        proposal.no_def,
        request.no_definition,
    )
    # Instants are kept in UTC, whatever offset the request wrote them in.
    assert (audited.before["close_at"], audited.after["close_at"]) == (
        proposal.close_at.isoformat(),
        (clock.now() + 3 * DAY).isoformat(),
    )
    assert (audited.before["resolve_by"], audited.after["resolve_by"]) == (
        proposal.resolve_by.isoformat(),
        resolve_by.isoformat(),
    )


async def test_an_edit_that_changes_nothing_is_not_audited_as_a_change(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The screen may send a field as it found it, and an instant in another offset is
    the same instant."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    request = ApproveMarketRequest(
        tier=MarketTier.DEFAULT,
        question=proposal.question,
        close_at=proposal.close_at.astimezone(timezone(timedelta(hours=-7))),
    )

    await lifecycle.approve(admin, proposal.id, request)

    (audited,) = await audit_rows(container)
    assert audited.before is not None
    assert audited.after is not None
    assert (
        set(audited.before)
        == set(audited.after)
        == {
            "status",
            "tier",
            "budget_micro",
            "b_micro",
        }
    )
    assert (await market_row(container, proposal.id)).close_at == proposal.close_at


async def test_the_dates_of_a_proposal_are_held_against_the_instant_of_its_approval(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Not against the day it was proposed. A proposal that closes a day further ahead
    than a market may stay open is 422 on the date today, and opens tomorrow as it is."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=LIFETIME + DAY)
    as_proposed = ApproveMarketRequest(tier=MarketTier.TIGHT)
    before = await everything(container)

    with pytest.raises(ValidationFailedError) as refused:
        await lifecycle.approve(admin, proposal.id, as_proposed)

    assert refused.value.status_code == 422
    assert refused.value.details == {"field": "close_at"}
    assert await everything(container) == before
    clock.advance(DAY)
    assert (await lifecycle.approve(admin, proposal.id, as_proposed)).status is MarketStatus.OPEN


@pytest.mark.parametrize(
    ("edits", "field"),
    [
        (lambda now, was: {"close_at": now}, "close_at"),
        (
            lambda now, was: {
                "close_at": now + LIFETIME + TICK,
                "resolve_by": now + LIFETIME + DAY,
            },
            "close_at",
        ),
        (lambda now, was: {"close_at": was.resolve_by + TICK}, "resolve_by"),
        (lambda now, was: {"resolve_by": was.close_at - TICK}, "resolve_by"),
        (lambda now, was: {"resolve_by": was.close_at + DELAY + TICK}, "resolve_by"),
        (lambda now, was: {"close_at": now - DAY, "resolve_by": now + LIFETIME}, "close_at"),
    ],
    ids=[
        "closing-now",
        "open-a-moment-longer-than-the-lifetime",
        "the-close-moved-past-the-resolution-deadline",
        "decided-a-moment-before-the-close",
        "decided-a-moment-too-late",
        "both-wrong-the-close-is-named",
    ],
)
async def test_an_approval_whose_edited_dates_break_a_rule_is_422_naming_the_field(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    edits: Callable[[datetime, Market], dict[str, datetime]],
    field: str,
) -> None:
    """The rules a proposal is held to, on the dates as the admin left them."""
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    before = await everything(container)
    request = ApproveMarketRequest(
        tier=MarketTier.DEFAULT, question="Edited with it.", **edits(clock.now(), proposal)
    )

    with pytest.raises(ValidationFailedError) as refused:
        await lifecycle.approve(admin, proposal.id, request)

    assert refused.value.details == {"field": field}
    assert await everything(container) == before


@pytest.mark.parametrize(
    "edits",
    [
        lambda now: {"close_at": now + TICK, "resolve_by": now + TICK},
        lambda now: {"close_at": now + LIFETIME, "resolve_by": now + LIFETIME + DELAY},
    ],
    ids=["closing-the-next-instant", "at-both-limits"],
)
async def test_an_approval_with_dates_at_the_edge_of_the_rules_opens_the_market(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    edits: Callable[[datetime], dict[str, datetime]],
) -> None:
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    dates = edits(clock.now())

    answer = await lifecycle.approve(
        admin, proposal.id, ApproveMarketRequest(tier=MarketTier.DEFAULT, **dates)
    )

    assert (answer.status, answer.close_at, answer.resolve_by) == (
        MarketStatus.OPEN,
        dates["close_at"],
        dates["resolve_by"],
    )


# --- Approval: the caps ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "closes_in", "counts"),
    [
        (MarketStatus.OPEN, DAY, True),
        (MarketStatus.HALTED, DAY, True),
        (MarketStatus.OPEN, -DAY, True),
        (MarketStatus.PROPOSED, DAY, False),
        (MarketStatus.REJECTED, DAY, False),
        (MarketStatus.CLOSED, DAY, False),
        (MarketStatus.RESOLVED, DAY, False),
        (MarketStatus.VOID, DAY, False),
    ],
    ids=[
        "open",
        "halted",
        "open-past-its-deadline",
        "proposed",
        "rejected",
        "closed",
        "resolved",
        "void",
    ],
)
async def test_max_open_markets_counts_the_markets_stored_open_or_halted(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    status: MarketStatus,
    closes_in: timedelta,
    counts: bool,
) -> None:
    """SPEC §5. A market past its deadline is reported CLOSED and still holds its slot
    until an admin closes or settles it: the cap is about stored statuses."""
    only_one = lifecycle_with(container, max_open_markets=1)
    await factory.market(status=status, close_in=closes_in)
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    before = await everything(container)

    if not counts:
        answer = await APPROVE.by(only_one, admin, proposal.id)
        assert answer.status is MarketStatus.OPEN
        return

    with pytest.raises(LimitExceededError) as refused:
        await APPROVE.by(only_one, admin, proposal.id)

    assert refused.value.status_code == 409
    assert refused.value.details == {"limit": Limit.MAX_OPEN_MARKETS}
    assert refused.value.message == OPEN_MARKETS_LIMIT_MESSAGE
    assert await everything(container) == before


async def test_closing_a_market_frees_its_slot_under_max_open_markets(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    only_one = lifecycle_with(container, max_open_markets=1)
    running = await factory.market()
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    with pytest.raises(LimitExceededError):
        await APPROVE.by(only_one, admin, proposal.id)

    await CLOSE.by(only_one, admin, running.id)

    assert (await APPROVE.by(only_one, admin, proposal.id)).status is MarketStatus.OPEN


@pytest.mark.parametrize("status", [MarketStatus.OPEN, MarketStatus.HALTED])
async def test_the_budgets_of_open_and_halted_markets_plus_this_one_must_fit_the_aggregate_cap(
    container: Container, factory: Factory, admin: AuthenticatedUser, status: MarketStatus
) -> None:
    """SPEC §5: exactly at MAX_AGGREGATE_LOSS_BUDGET passes, one micro-credit above is
    409. Settled and closed markets have left the sum."""
    running = await factory.market(status=status, tier=MarketTier.MEDIUM)
    await factory.market(status=MarketStatus.CLOSED, tier=MarketTier.MEDIUM)
    await factory.market(status=MarketStatus.RESOLVED, tier=MarketTier.MEDIUM)
    fits_exactly = running.budget_micro + tier_budget(MarketTier.TIGHT)
    too_tight = lifecycle_with(container, max_aggregate_loss_budget_micro=fits_exactly - 1)
    exact = lifecycle_with(container, max_aggregate_loss_budget_micro=fits_exactly)
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    before = await everything(container)
    request = ApproveMarketRequest(tier=MarketTier.TIGHT)

    with pytest.raises(LimitExceededError) as refused:
        await too_tight.approve(admin, proposal.id, request)

    assert refused.value.status_code == 409
    assert refused.value.details == {"limit": Limit.MAX_AGGREGATE_LOSS_BUDGET}
    assert refused.value.message == LOSS_BUDGET_LIMIT_MESSAGE
    assert await everything(container) == before
    assert (await exact.approve(admin, proposal.id, request)).status is MarketStatus.OPEN


async def test_an_approval_that_breaks_several_rules_is_told_the_first_dates_budget_then_caps(
    container: Container, factory: Factory, clock: FrozenClock, admin: AuthenticatedUser
) -> None:
    await factory.market()
    full = lifecycle_with(container, max_open_markets=1, max_aggregate_loss_budget_micro=1)
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    oversized = to_cr(MAX_MARKET_BUDGET + 1)

    with pytest.raises(ValidationFailedError):
        await full.approve(
            admin,
            proposal.id,
            ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=oversized, close_at=clock.now()),
        )
    with pytest.raises(LimitExceededError) as budget:
        await full.approve(
            admin, proposal.id, ApproveMarketRequest(tier=MarketTier.CUSTOM, budget_cr=oversized)
        )
    with pytest.raises(LimitExceededError) as slots:
        await full.approve(admin, proposal.id, ApproveMarketRequest(tier=MarketTier.TIGHT))

    assert budget.value.details == {"limit": Limit.MAX_MARKET_BUDGET}
    assert slots.value.details == {"limit": Limit.MAX_OPEN_MARKETS}


async def test_an_approval_that_runs_into_a_cap_leaves_the_proposal_as_it_was_edits_included(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    """The edits reach the database before the caps are counted; the rollback takes
    them back with everything else."""
    await factory.market()
    full = lifecycle_with(container, max_open_markets=1)
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    before = await everything(container)
    request = ApproveMarketRequest(
        tier=MarketTier.TIGHT, question="Reworded, and then there was no room."
    )

    with pytest.raises(LimitExceededError):
        await full.approve(admin, proposal.id, request)

    assert await everything(container) == before
    assert (await market_row(container, proposal.id)).question == proposal.question


@pytest.mark.parametrize(
    ("risk", "limit"),
    [
        ({"max_open_markets": 1}, Limit.MAX_OPEN_MARKETS),
        (
            {"max_aggregate_loss_budget_micro": tier_budget(MarketTier.DEFAULT)},
            Limit.MAX_AGGREGATE_LOSS_BUDGET,
        ),
    ],
    ids=["one-slot", "room-for-one-budget"],
)
async def test_two_parallel_approvals_cannot_both_pass_a_cap(
    container: Container,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    risk: dict[str, int],
    limit: Limit,
) -> None:
    """Each approval counts the open markets and adds up their budgets before it opens
    its own. They hold different market rows, so without the approval lock two that
    overlap would both count none (A.5). Each is kept in its transaction between the
    counting and the opening."""
    tight = lifecycle_with(container, **risk)
    first = factory.identity(await factory.admin(0))
    second = factory.identity(await factory.admin(1))
    proposals = [await factory.market(status=MarketStatus.PROPOSED) for _ in range(2)]
    linger_after(monkeypatch, MarketRepository, "sum_budget_by_statuses")

    answers = await asyncio.gather(
        APPROVE.by(tight, first, proposals[0].id),
        APPROVE.by(tight, second, proposals[1].id),
        return_exceptions=True,
    )

    opened = [answer for answer in answers if isinstance(answer, MarketDetailResponse)]
    refused = [answer for answer in answers if isinstance(answer, LimitExceededError)]
    assert (len(opened), len(refused)) == (1, 1)
    assert refused[0].details == {"limit": limit}
    statuses = sorted([(await market_row(container, market.id)).status for market in proposals])
    assert statuses == [MarketStatus.OPEN, MarketStatus.PROPOSED]
    assert await count_rows(container.uow_factory, AuditLogEntry) == 1
    assert await count_rows(container.uow_factory, OutboxEvent) == 1


async def test_an_approval_waits_for_the_one_before_it_and_is_judged_and_dated_at_its_turn(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Approvals check the caps one at a time (A.5), so one may have to wait. It opens
    its market at the moment it gets its turn: that is `opened_at`, the start of the
    chart and the instant its deadline is held against. A proposal whose `close_at`
    passed while its approval waited has expired by then."""
    in_time = await factory.market(status=MarketStatus.PROPOSED)
    too_late = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))

    async with container.uow_factory() as another_approval:
        await another_approval.advisory_lock(market_lifecycle.APPROVAL_LOCK)
        opening = asyncio.create_task(APPROVE.by(lifecycle, admin, in_time.id))
        expiring = asyncio.create_task(APPROVE.by(lifecycle, admin, too_late.id))
        await asyncio.sleep(BLOCKED_FOR_SECONDS)
        assert not opening.done()
        assert not expiring.done()
        turn_at = clock.advance(timedelta(hours=2))

    opened = await asyncio.wait_for(opening, RECEIVE_TIMEOUT_SECONDS)
    with pytest.raises(ConflictError) as refused:
        await asyncio.wait_for(expiring, RECEIVE_TIMEOUT_SECONDS)

    assert refused.value.message == PROPOSAL_EXPIRED_MESSAGE
    assert opened.opened_at == turn_at
    async with container.uow_factory() as uow:
        (queued,) = (await uow.session.scalars(select(OutboxEvent))).all()
    assert quote_point_from_payload(queued.payload).ts == turn_at
    assert (await market_row(container, too_late.id)).status is MarketStatus.PROPOSED


# --- Reject ------------------------------------------------------------------------------


async def test_rejecting_a_proposal_keeps_the_reason_for_its_proposer_and_writes_its_audit_row(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    proposer = await factory.user()
    stranger = factory.identity(await factory.user())
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    at = clock.advance(timedelta(minutes=10))
    request = RejectMarketRequest(reason="It asks about a named person's grade.")

    answer = await lifecycle.reject(admin, proposal.id, request)

    row = await market_row(container, proposal.id)
    assert (row.status, row.rejection_reason) == (MarketStatus.REJECTED, request.reason)
    assert (row.tier, row.b_micro, row.budget_micro, row.opened_at, row.approved_by) == (
        None,
        0,
        0,
        None,
        None,
    )
    assert answer == await container.markets.detail(admin, proposal.id)
    own = await container.markets.detail(factory.identity(proposer), proposal.id)
    assert (own.status, own.rejection_reason) == (MarketStatus.REJECTED, request.reason)
    with pytest.raises(NotFoundError):
        await container.markets.detail(stranger, proposal.id)
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action, audited.target, audited.created_at) == (
        admin.id,
        AuditAction.MARKET_REJECT,
        target_of(proposal),
        at,
    )
    assert audited.before == {"status": "PROPOSED"}
    assert audited.after == {"status": "REJECTED", "reason": request.reason}
    # A market that was never opened has no account and no chart.
    async with container.uow_factory() as uow:
        assert await uow.accounts.get_market_account(proposal.id) is None
    assert await count_rows(container.uow_factory, OutboxEvent) == 0


async def test_rejecting_a_proposal_drops_no_list_and_publishes_nothing(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """A proposal was never in a list and nobody is watching it."""
    await factory.market()
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    await container.markets.list_markets(admin, None)
    cached = await container.market_cache.get(PUBLIC_LIST)
    assert cached is not None

    async with watching(container, proposal.id) as queue:
        await REJECT.by(lifecycle, admin, proposal.id)
        await nothing_more(container, proposal.id, queue)

    assert await container.market_cache.get(PUBLIC_LIST) == cached


# --- Halt, resume, close -----------------------------------------------------------------


async def test_halting_a_market_stops_its_trading_and_resuming_it_starts_it_again(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    viewer = factory.identity(alice)

    halted_at = clock.advance(timedelta(minutes=10))
    halted = await HALT.by(lifecycle, admin, market.id)
    paused = await container.markets.detail(viewer, market.id)
    resumed_at = clock.advance(timedelta(minutes=10))
    resumed = await RESUME.by(lifecycle, admin, market.id)
    trading = await container.markets.detail(viewer, market.id)

    assert (halted.status, halted.trading_open) == (MarketStatus.HALTED, False)
    assert (paused.viewer.can_trade, paused.viewer.cannot_trade_reason) == (
        False,
        TradeBlockReason.MARKET_NOT_OPEN,
    )
    assert (resumed.status, resumed.trading_open) == (MarketStatus.OPEN, True)
    assert (trading.viewer.can_trade, trading.viewer.cannot_trade_reason) == (True, None)
    row = await market_row(container, market.id)
    # Neither changes anything but the status: b, budget, q and the deadline stay.
    assert (row.b_micro, row.budget_micro, row.q_yes_micro, row.close_at, row.closed_at) == (
        market.b_micro,
        market.budget_micro,
        40 * MICRO,
        market.close_at,
        None,
    )
    halt_row, resume_row = await audit_rows(container)
    assert (halt_row.action, halt_row.actor_id, halt_row.target, halt_row.created_at) == (
        AuditAction.MARKET_HALT,
        admin.id,
        target_of(market),
        halted_at,
    )
    assert (halt_row.before, halt_row.after) == (
        {"status": "OPEN"},
        {"status": "HALTED", "reason": HALT_REASON},
    )
    assert (resume_row.action, resume_row.actor_id, resume_row.created_at) == (
        AuditAction.MARKET_RESUME,
        admin.id,
        resumed_at,
    )
    assert (resume_row.before, resume_row.after) == (
        {"status": "HALTED"},
        {"status": "OPEN", "note": RESUME_NOTE},
    )
    await assert_books_balance(container.uow_factory)


async def test_a_market_can_be_halted_until_its_deadline_and_not_from_it_on(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Past `close_at` a market is closed to trading already (A.4): 409, though it is
    stored OPEN."""
    late = await factory.market(close_in=timedelta(hours=1))
    in_time = await factory.market(close_in=timedelta(hours=1))
    before = await everything(container)

    clock.set(late.close_at)
    with pytest.raises(ConflictError) as refused:
        await HALT.by(lifecycle, admin, late.id)
    assert refused.value.status_code == 409
    assert await everything(container) == before

    clock.set(in_time.close_at - TICK)
    assert (await HALT.by(lifecycle, admin, in_time.id)).status is MarketStatus.HALTED


async def test_a_market_resumed_past_its_deadline_is_stored_open_and_reported_closed(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    market = await factory.market(status=MarketStatus.HALTED, close_in=timedelta(hours=1))
    clock.set(market.close_at + SECOND)

    async with watching(container, market.id) as queue:
        answer = await lifecycle.resume(admin, market.id, ResumeMarketRequest())
        published = await next_message(queue)

    row = await market_row(container, market.id)
    assert row.status is MarketStatus.OPEN
    assert (answer.status, answer.trading_open) == (MarketStatus.CLOSED, False)
    assert (published["status"], published["trading_open"]) == ("CLOSED", False)
    (audited,) = await audit_rows(container)
    assert audited.after == {"status": "OPEN", "note": None}


@pytest.mark.parametrize(
    ("status", "closes_in"),
    [
        (MarketStatus.OPEN, DAY),
        (MarketStatus.OPEN, -DAY),
        (MarketStatus.HALTED, DAY),
        (MarketStatus.HALTED, -DAY),
    ],
    ids=["open", "open-past-its-deadline", "halted", "halted-past-its-deadline"],
)
async def test_closing_a_market_stops_its_trading_for_good_before_and_after_its_deadline(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    status: MarketStatus,
    closes_in: timedelta,
) -> None:
    market = await factory.market(status=status, close_in=closes_in)
    closed_at = clock.advance(timedelta(minutes=10))

    answer = await CLOSE.by(lifecycle, admin, market.id)

    row = await market_row(container, market.id)
    assert (row.status, row.closed_at) == (MarketStatus.CLOSED, closed_at)
    assert (answer.status, answer.trading_open, answer.closed_at) == (
        MarketStatus.CLOSED,
        False,
        closed_at,
    )
    # The deadline the market was given stays what it was.
    assert row.close_at == market.close_at
    (audited,) = await audit_rows(container)
    assert (audited.action, audited.actor_id, audited.target, audited.created_at) == (
        AuditAction.MARKET_CLOSE,
        admin.id,
        target_of(market),
        closed_at,
    )
    assert audited.before == {"status": status.value}
    assert audited.after == {"status": "CLOSED", "note": CLOSE_NOTE}
    for action in (RESUME, HALT, CLOSE):
        with pytest.raises(ConflictError):
            await action.by(lifecycle, admin, market.id)


async def test_an_action_that_waited_for_the_market_row_is_judged_and_dated_when_it_got_it(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The clock is read after the lock (A.4). A halt that arrives in time and gets the
    row after the deadline is refused, and a close is dated when it happened."""
    halted_late = await factory.market(close_in=timedelta(hours=1))
    closed_late = await factory.market(close_in=timedelta(hours=1))

    async with container.uow_factory() as holder:
        await holder.markets.get_for_update(halted_late.id)
        await holder.markets.get_for_update(closed_late.id)
        halting = asyncio.create_task(HALT.by(lifecycle, admin, halted_late.id))
        closing = asyncio.create_task(CLOSE.by(lifecycle, admin, closed_late.id))
        await asyncio.sleep(BLOCKED_FOR_SECONDS)
        assert not halting.done()
        assert not closing.done()
        granted_at = clock.advance(timedelta(hours=2))

    with pytest.raises(ConflictError):
        await asyncio.wait_for(halting, RECEIVE_TIMEOUT_SECONDS)
    closed = await asyncio.wait_for(closing, RECEIVE_TIMEOUT_SECONDS)

    assert closed.closed_at == granted_at
    assert (await market_row(container, halted_late.id)).status is MarketStatus.OPEN


# --- Resolve -----------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", list(Outcome))
async def test_resolving_pays_each_winner_exactly_their_winning_shares_and_vis_takes_the_rest(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    outcome: Outcome,
) -> None:
    """SPEC §12 and A.8: one entry credits every holder of the winning outcome one
    credit per share, empties the market's account and gives `vis:mm_pnl` the
    difference, whichever sign it has."""
    book = await traded(container, factory)
    held_micro = {
        Outcome.YES: {book.alice.id: 80 * MICRO, book.carol.id: 15 * MICRO},
        Outcome.NO: {book.bob.id: 30 * MICRO, book.carol.id: 10 * MICRO},
    }[outcome]
    before = await balances(factory, book.traders)
    in_the_account = await market_balance(container, book.market)
    pnl_before = await pnl(container)
    resolved_at = clock.advance(timedelta(hours=1))

    answer = await lifecycle.resolve(admin, book.market.id, resolve_to(outcome))

    paid_micro = sum(held_micro.values())
    after = await balances(factory, book.traders)
    assert {user.id: after[user.id] - before[user.id] for user in book.traders} == {
        user.id: held_micro.get(user.id, 0) for user in book.traders
    }
    view = container.market_view
    resolution = await resolution_of(container, book.market)
    for user in book.traders:
        rows = await positions_of(container, user, book.market)
        assert view.payout_micro(rows, resolution) == held_micro.get(user.id, 0)
    # Every share of the winning outcome that is out there was paid, and no other.
    row = await market_row(container, book.market.id)
    assert paid_micro == (row.q_yes_micro if outcome is Outcome.YES else row.q_no_micro)
    assert await market_balance(container, book.market) == 0
    assert await pnl(container) - pnl_before == in_the_account - paid_micro
    # VIS loses at most the budget in a market, whichever outcome wins (SPEC §4).
    assert paid_micro - in_the_account <= row.budget_micro
    assert (answer.status, answer.outcome) == (
        MarketStatus.RESOLVED,
        ResolutionOutcome(outcome.value),
    )
    assert answer == await container.markets.detail(admin, book.market.id)
    assert (row.status, row.closed_at) == (MarketStatus.RESOLVED, book.market.closed_at)
    assert resolution is not None
    assert (
        resolution.outcome,
        resolution.evidence,
        resolution.resolved_by,
        resolution.created_at,
    ) == (ResolutionOutcome(outcome.value), EVIDENCE, admin.id, resolved_at)
    await assert_books_balance(container.uow_factory)


async def test_the_two_outcomes_of_one_book_leave_vis_on_either_side_of_zero(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """The remainder goes to `vis:mm_pnl`: a gain when the lightly held outcome wins,
    a loss when the heavily held one does."""
    heavy = await traded(container, factory)
    light = await traded(container, factory)
    assert heavy.market.q_yes_micro > heavy.market.q_no_micro
    start = await pnl(container)

    await lifecycle.resolve(admin, heavy.market.id, resolve_to(Outcome.YES))
    after_the_loss = await pnl(container)
    await lifecycle.resolve(admin, light.market.id, resolve_to(Outcome.NO))
    after_the_gain = await pnl(container)

    assert after_the_loss - start < 0 < after_the_gain - after_the_loss
    await assert_books_balance(container.uow_factory)


async def test_a_resolution_is_one_journal_entry_with_the_postings_of_appendix_a8(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    book = await traded(container, factory)
    in_the_account = await market_balance(container, book.market)
    resolved_at = clock.advance(timedelta(hours=1))

    await lifecycle.resolve(admin, book.market.id, resolve_to(Outcome.YES))

    async with container.uow_factory() as uow:
        entry = await uow.journal.get_entry_by_idempotency_key(resolution_key(book.market.id))
        market_account = await uow.accounts.get_market_account(book.market.id)
        pnl_account = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
        settlements = await uow.session.scalars(
            select(JournalEntry).where(JournalEntry.kind == JournalKind.MARKET_RESOLUTION)
        )
        assert len(settlements.all()) == 1
    assert entry is not None
    assert market_account is not None
    assert (entry.kind, entry.ref_type, entry.ref_id, entry.created_at) == (
        JournalKind.MARKET_RESOLUTION,
        LedgerRef.MARKET,
        book.market.id,
        resolved_at,
    )
    # Winners +shares, market -B, vis:mm_pnl +(B - payouts). Bob lost and dave holds
    # nothing: neither has a line.
    assert await settlement_postings(container, resolution_key(book.market.id)) == {
        await account_id_of(container, book.alice): 80 * MICRO,
        await account_id_of(container, book.carol): 15 * MICRO,
        market_account.id: -in_the_account,
        pnl_account.id: in_the_account - 95 * MICRO,
    }
    await assert_books_balance(container.uow_factory)


async def test_resolving_touches_no_position_and_no_quantity(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Each position keeps its shares, cost and proceeds: the portfolio, the market
    page and the leaderboard read a payout from the shares of the winning outcome."""
    book = await traded(container, factory)
    before = await everything(container)

    await lifecycle.resolve(admin, book.market.id, resolve_to(Outcome.NO))

    after = await everything(container)
    assert after["positions"] == before["positions"]
    row = await market_row(container, book.market.id)
    assert (row.q_yes_micro, row.q_no_micro, row.version, row.volume_micro) == (
        book.market.q_yes_micro,
        book.market.q_no_micro,
        book.market.version,
        book.market.volume_micro,
    )
    await assert_books_balance(container.uow_factory)


async def test_resolving_writes_its_audit_row_with_the_outcome_and_what_was_paid(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    book = await traded(container, factory)
    resolved_at = clock.advance(timedelta(hours=1))

    await lifecycle.resolve(admin, book.market.id, resolve_to(Outcome.NO))

    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action, audited.target, audited.created_at) == (
        admin.id,
        AuditAction.MARKET_RESOLVE,
        target_of(book.market),
        resolved_at,
    )
    assert audited.before == {"status": "CLOSED"}
    assert audited.after == {"status": "RESOLVED", "outcome": "NO", "payouts_micro": 40 * MICRO}
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_settling_a_market_nobody_traded_books_nothing(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """A.8: an entry all of whose postings would be zero is not booked at all."""
    market = await factory.market(status=MarketStatus.CLOSED)

    answer = await action.by(lifecycle, admin, market.id)

    assert answer.status is action.target
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    assert await count_rows(container.uow_factory, Posting) == 0
    assert await pnl(container) == 0
    assert await resolution_of(container, market) is not None
    (audited,) = await audit_rows(container)
    assert audited.after is not None
    paid = "payouts_micro" if action is RESOLVE else "refunds_micro"
    assert audited.after[paid] == 0
    await assert_books_balance(container.uow_factory)


async def test_what_is_left_in_the_account_of_a_market_nobody_holds_goes_to_vis(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """Everybody sold again, so nobody is paid. What the account still holds is the
    dust that rounding left with the market maker (golden rule 5), and it is VIS's."""
    alice = await factory.user(balance_micro=WALLET)
    bob = await factory.user(balance_micro=WALLET)
    market = await factory.market(status=MarketStatus.CLOSED)
    await factory.position(alice, market, Outcome.YES, shares_micro=70 * MICRO)
    await factory.position(bob, market, Outcome.YES, shares_micro=50 * MICRO)
    await sell(container, alice, market, Outcome.YES, 70 * MICRO)
    await sell(container, bob, market, Outcome.YES, 50 * MICRO)
    left_over = await market_balance(container, market)
    before = await balances(factory, (alice, bob))
    assert left_over > 0

    await lifecycle.resolve(admin, market.id, resolve_to(Outcome.YES))

    assert await balances(factory, (alice, bob)) == before
    assert await market_balance(container, market) == 0
    assert await pnl(container) == left_over
    postings = await settlement_postings(container, resolution_key(market.id))
    assert postings is not None
    assert sorted(postings.values()) == [-left_over, left_over]
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_an_open_market_before_its_deadline_must_be_closed_first(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """A.4: 409 with the sentence that says what to do, up to the last moment before
    `close_at`. Closed, the market settles."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market(close_in=timedelta(hours=1))
    await factory.position(alice, market, Outcome.YES, shares_micro=60 * MICRO)
    clock.set(market.close_at - TICK)
    before = await everything(container)

    with pytest.raises(ConflictError) as refused:
        await action.by(lifecycle, admin, market.id)

    assert refused.value.status_code == 409
    assert refused.value.message == CLOSE_FIRST_MESSAGE
    assert await everything(container) == before
    await CLOSE.by(lifecycle, admin, market.id)
    assert (await action.by(lifecycle, admin, market.id)).status is action.target
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("status", "past_the_deadline_by"),
    [
        (MarketStatus.HALTED, None),
        (MarketStatus.OPEN, timedelta(0)),
        (MarketStatus.OPEN, DAY),
        (MarketStatus.HALTED, DAY),
    ],
    ids=["halted", "open-at-its-deadline", "open-past-its-deadline", "halted-past-its-deadline"],
)
@both_settlements
async def test_a_halted_or_past_deadline_market_is_closed_in_the_settlement_with_its_own_audit_row(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
    status: MarketStatus,
    past_the_deadline_by: timedelta | None,
) -> None:
    """A.4: no second request is needed, and the log shows both steps."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market(status=status, close_in=timedelta(hours=1))
    await factory.position(alice, market, Outcome.YES, shares_micro=60 * MICRO)
    if past_the_deadline_by is None:
        settled_at = clock.advance(timedelta(minutes=10))
    else:
        clock.set(market.close_at + past_the_deadline_by)
        settled_at = clock.now()

    answer = await action.by(lifecycle, admin, market.id)

    row = await market_row(container, market.id)
    assert (row.status, row.closed_at) == (action.target, settled_at)
    assert (answer.status, answer.closed_at) == (action.target, settled_at)
    closing, settling = await audit_rows(container)
    assert (closing.action, closing.actor_id, closing.target, closing.created_at) == (
        AuditAction.MARKET_CLOSE,
        admin.id,
        target_of(market),
        settled_at,
    )
    assert (closing.before, closing.after) == (
        {"status": status.value},
        {"status": "CLOSED", "note": None},
    )
    assert (settling.action, settling.actor_id, settling.created_at) == (
        action.audit,
        admin.id,
        settled_at,
    )
    assert settling.before == {"status": "CLOSED"}
    await assert_books_balance(container.uow_factory)


async def test_a_second_resolution_is_409_and_books_nothing(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """SPEC §12: "a second resolution fails", whatever it says and whoever sends it."""
    second = factory.identity(await factory.admin(1))
    book = await traded(container, factory)
    await lifecycle.resolve(admin, book.market.id, resolve_to(Outcome.YES))
    before = await everything(container)

    for actor, request in (
        (admin, resolve_to(Outcome.YES)),
        (admin, resolve_to(Outcome.NO)),
        (second, resolve_to(Outcome.NO)),
    ):
        with pytest.raises(ConflictError) as refused:
            await lifecycle.resolve(actor, book.market.id, request)
        assert refused.value.status_code == 409
    with pytest.raises(ConflictError):
        await VOID.by(lifecycle, second, book.market.id)

    assert await everything(container) == before
    await assert_books_balance(container.uow_factory)


async def test_a_voided_market_cannot_be_resolved_or_voided_again(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    book = await traded(container, factory)
    await VOID.by(lifecycle, admin, book.market.id)
    before = await everything(container)

    for action in (RESOLVE, VOID):
        with pytest.raises(ConflictError):
            await action.by(lifecycle, admin, book.market.id)

    assert await everything(container) == before
    await assert_books_balance(container.uow_factory)


async def test_two_admins_resolving_at_once_settle_the_market_once(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One gets the row first and settles; the other waits for it and finds the market
    RESOLVED under the lock. The winners are paid once, as the first to get the row
    decided. The first is kept in its transaction right after it got the row."""
    first = factory.identity(await factory.admin(0))
    second = factory.identity(await factory.admin(1))
    book = await traded(container, factory)
    before = await balances(factory, book.traders)
    linger_after(monkeypatch, TradeRepository, "exists_for_user_market")

    answers = await asyncio.gather(
        lifecycle.resolve(first, book.market.id, resolve_to(Outcome.YES)),
        lifecycle.resolve(second, book.market.id, resolve_to(Outcome.NO)),
        return_exceptions=True,
    )

    settled = [answer for answer in answers if isinstance(answer, MarketDetailResponse)]
    refused = [answer for answer in answers if isinstance(answer, ConflictError)]
    assert (len(settled), len(refused)) == (1, 1)
    resolution = await resolution_of(container, book.market)
    assert resolution is not None
    assert settled[0].outcome is resolution.outcome
    assert await count_rows(container.uow_factory, Resolution) == 1
    assert await count_rows(container.uow_factory, AuditLogEntry) == 1
    view = container.market_view
    after = await balances(factory, book.traders)
    for user in book.traders:
        rows = await positions_of(container, user, book.market)
        assert after[user.id] - before[user.id] == view.payout_micro(rows, resolution)
    await assert_books_balance(container.uow_factory)


# --- Void --------------------------------------------------------------------------------


async def test_voiding_refunds_every_net_stake_and_zeroes_every_share(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """SPEC §12 and A.5: each user gets max(0, cost - proceeds) back, over both
    outcomes. Shares and q go to zero; cost and proceeds stay as the record."""
    book = await traded(container, factory)
    view = container.market_view
    stakes_micro = {
        user.id: view.net_stake_micro(await positions_of(container, user, book.market))
        for user in book.traders
    }
    kept = [
        (row.user_id, row.outcome, row.cost_micro, row.proceeds_micro)
        for row in await positions_in(container, book.market)
    ]
    before = await balances(factory, book.traders)
    voided_at = clock.advance(timedelta(hours=1))

    answer = await VOID.by(lifecycle, admin, book.market.id)

    after = await balances(factory, book.traders)
    assert {user.id: after[user.id] - before[user.id] for user in book.traders} == stakes_micro
    # Alice, bob and carol have a stake; dave took more out than he put in.
    assert all(stakes_micro[user.id] > 0 for user in (book.alice, book.bob, book.carol))
    assert stakes_micro[book.dave.id] == 0
    rows = await positions_in(container, book.market)
    assert [row.shares_micro for row in rows] == [0] * len(kept)
    assert [(row.user_id, row.outcome, row.cost_micro, row.proceeds_micro) for row in rows] == kept
    row = await market_row(container, book.market.id)
    assert (row.status, row.q_yes_micro, row.q_no_micro) == (MarketStatus.VOID, 0, 0)
    assert (row.version, row.volume_micro, row.b_micro, row.budget_micro) == (
        book.market.version,
        book.market.volume_micro,
        book.market.b_micro,
        book.market.budget_micro,
    )
    resolution = await resolution_of(container, book.market)
    assert resolution is not None
    assert (
        resolution.outcome,
        resolution.evidence,
        resolution.resolved_by,
        resolution.created_at,
    ) == (ResolutionOutcome.VOID, VOID_REASON, admin.id, voided_at)
    for user in book.traders:
        held = await positions_of(container, user, book.market)
        assert view.refund_micro(held, resolution) == stakes_micro[user.id]
    assert (answer.status, answer.outcome) == (MarketStatus.VOID, ResolutionOutcome.VOID)
    assert answer == await container.markets.detail(admin, book.market.id)
    await assert_books_balance(container.uow_factory)


async def test_a_void_does_not_take_back_what_a_seller_took_out_so_vis_pays_exactly_that(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    """A.8: the refunds add up to more than the market's account holds, by what traders
    who sold above their cost gained. `vis:mm_pnl` adds it, and it never gains by a
    void."""
    book = await traded(container, factory)
    (sold_out,) = await positions_of(container, book.dave, book.market)
    gain_micro = sold_out.proceeds_micro - sold_out.cost_micro
    in_the_account = await market_balance(container, book.market)
    pnl_before = await pnl(container)
    dave_before = await factory.balance_micro(book.dave)
    assert gain_micro > 0

    await VOID.by(lifecycle, admin, book.market.id)

    assert await pnl(container) - pnl_before == -gain_micro
    assert await market_balance(container, book.market) == 0
    assert await factory.balance_micro(book.dave) == dave_before
    postings = await settlement_postings(container, void_key(book.market.id))
    assert postings is not None
    refunded_micro = sum(amount for amount in postings.values() if amount > 0)
    assert refunded_micro == in_the_account + gain_micro
    await assert_books_balance(container.uow_factory)


async def test_a_void_is_one_journal_entry_with_the_postings_of_appendix_a8_and_its_audit_row(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
) -> None:
    book = await traded(container, factory)
    view = container.market_view
    stakes_micro = {
        user.id: view.net_stake_micro(await positions_of(container, user, book.market))
        for user in book.traders
    }
    in_the_account = await market_balance(container, book.market)
    voided_at = clock.advance(timedelta(hours=1))

    await VOID.by(lifecycle, admin, book.market.id)

    async with container.uow_factory() as uow:
        entry = await uow.journal.get_entry_by_idempotency_key(void_key(book.market.id))
        market_account = await uow.accounts.get_market_account(book.market.id)
        pnl_account = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
    assert entry is not None
    assert market_account is not None
    assert (entry.kind, entry.ref_type, entry.ref_id, entry.created_at) == (
        JournalKind.MARKET_VOID,
        LedgerRef.MARKET,
        book.market.id,
        voided_at,
    )
    refunds_micro = sum(stakes_micro.values())
    # Each user +net stake, market -B, vis:mm_pnl +(B - refunds). Dave has no line.
    assert await settlement_postings(container, void_key(book.market.id)) == {
        await account_id_of(container, book.alice): stakes_micro[book.alice.id],
        await account_id_of(container, book.bob): stakes_micro[book.bob.id],
        await account_id_of(container, book.carol): stakes_micro[book.carol.id],
        market_account.id: -in_the_account,
        pnl_account.id: in_the_account - refunds_micro,
    }
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action, audited.target, audited.created_at) == (
        admin.id,
        AuditAction.MARKET_VOID,
        target_of(book.market),
        voided_at,
    )
    assert audited.before == {"status": "CLOSED"}
    assert audited.after == {
        "status": "VOID",
        "reason": VOID_REASON,
        "refunds_micro": refunds_micro,
    }
    await assert_books_balance(container.uow_factory)


# --- Whatever was traded -----------------------------------------------------------------

STEP = st.tuples(
    st.integers(min_value=0, max_value=2),
    st.sampled_from(Outcome),
    st.booleans(),
    st.integers(min_value=1, max_value=120 * MICRO),
)
"""One step of a random book: which of three traders, the outcome, whether to sell if
they hold any of it, and how many micro-shares."""


@hypothesis_settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(steps=st.lists(STEP, max_size=8), ending=st.sampled_from(ResolutionOutcome))
async def test_whatever_was_traded_a_settlement_pays_what_is_owed_and_vis_takes_the_rest(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    steps: list[tuple[int, Outcome, bool, int]],
    ending: ResolutionOutcome,
) -> None:
    """For any run of buys and sells, settled YES, NO or VOID: every trader's balance
    grows by exactly what `MarketView` says the market owes them, the market's account
    ends empty, `vis:mm_pnl` moves by what the account held minus what was paid, and
    the books balance. Resolved, the payouts are the shares out of the winning outcome,
    VIS loses at most the budget, and no position changes. Voided, every share is gone,
    cost and proceeds stay, and VIS gains nothing.

    Every example has traders and a market of its own; the accounts of VIS are compared
    by how far the example moved them.
    """
    traders = [await factory.user(balance_micro=WALLET) for _ in range(3)]
    market = await factory.market(status=MarketStatus.CLOSED)
    for who, outcome, would_sell, shares_micro in steps:
        user = traders[who]
        held = {
            row.outcome: row.shares_micro for row in await positions_of(container, user, market)
        }
        if would_sell and held.get(outcome):
            await sell(container, user, market, outcome, min(shares_micro, held[outcome]))
        elif shares_micro <= await factory.balance_micro(user):
            await factory.position(user, market, outcome, shares_micro=shares_micro)
    view = container.market_view
    stakes_micro = {
        user.id: view.net_stake_micro(await positions_of(container, user, market))
        for user in traders
    }
    before = await balances(factory, traders)
    positions_before = (await everything(container))["positions"]
    in_the_account = await market_balance(container, market)
    pnl_before = await pnl(container)
    entries_before = await count_rows(container.uow_factory, JournalEntry)

    if ending is ResolutionOutcome.VOID:
        await VOID.by(lifecycle, admin, market.id)
    else:
        await lifecycle.resolve(admin, market.id, resolve_to(Outcome(ending.value)))

    resolution = await resolution_of(container, market)
    assert resolution is not None
    after = await balances(factory, traders)
    owed_micro: dict[UUID, int] = {}
    for user in traders:
        rows = await positions_of(container, user, market)
        owed = (
            view.refund_micro(rows, resolution)
            if ending is ResolutionOutcome.VOID
            else view.payout_micro(rows, resolution)
        )
        assert owed is not None
        owed_micro[user.id] = owed
        assert after[user.id] - before[user.id] == owed
    paid_micro = sum(owed_micro.values())
    row = await market_row(container, market.id)
    assert await market_balance(container, market) == 0
    assert await pnl(container) - pnl_before == in_the_account - paid_micro
    booked = await count_rows(container.uow_factory, JournalEntry) - entries_before
    assert booked == (1 if in_the_account or paid_micro else 0)
    if ending is ResolutionOutcome.VOID:
        assert owed_micro == stakes_micro
        assert paid_micro >= in_the_account
        assert (row.q_yes_micro, row.q_no_micro) == (0, 0)
        left = held_in(market, (await everything(container))["positions"])
        assert [shares for *_, shares, _cost, _proceeds in left] == [0] * len(left)
        assert [held[-2:] for held in left] == [
            held[-2:] for held in held_in(market, positions_before)
        ]
    else:
        won = row.q_yes_micro if ending is ResolutionOutcome.YES else row.q_no_micro
        assert paid_micro == won
        assert paid_micro - in_the_account <= row.budget_micro
        assert held_in(market, (await everything(container))["positions"]) == (
            held_in(market, positions_before)
        )
    await assert_books_balance(container.uow_factory)


# --- What every action leaves behind -----------------------------------------------------


async def test_every_step_of_a_markets_life_leaves_its_audit_row_in_order(
    container: Container, factory: Factory, clock: FrozenClock, lifecycle: MarketLifecycleService
) -> None:
    """Proposed by a user, then approved, halted, resumed, closed and resolved by two
    admins taking turns: one row per status change, by whoever made it, when they did."""
    first = factory.identity(await factory.admin(0))
    second = factory.identity(await factory.admin(1))
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    turns = [
        (APPROVE, first),
        (HALT, second),
        (RESUME, first),
        (CLOSE, second),
        (RESOLVE, first),
    ]
    expected: list[tuple[str, UUID, str, str, str, datetime]] = []
    status = MarketStatus.PROPOSED

    for action, actor in turns:
        at = clock.advance(timedelta(minutes=7))
        answer = await action.by(lifecycle, actor, proposal.id)
        expected.append((action.audit, actor.id, target_of(proposal), status, action.target, at))
        status = answer.status

    rows = await audit_rows(container)
    assert [
        (
            row.action,
            row.actor_id,
            row.target,
            (row.before or {})["status"],
            (row.after or {})["status"],
            row.created_at,
        )
        for row in rows
    ] == expected
    assert status is MarketStatus.RESOLVED


@pytest.mark.parametrize("action", PUBLISHED, ids=str)
async def test_an_action_on_a_public_market_drops_the_cached_lists_and_publishes_one_status_event(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """After the commit (A.4): open phones refetch, and the lists they refetch are not
    the ones cached before."""
    await factory.market()
    market = await factory.market(status=action.starts[0])
    await container.markets.list_markets(admin, None)
    assert await container.market_cache.get(PUBLIC_LIST) is not None

    async with watching(container, market.id) as queue:
        await action.by(lifecycle, admin, market.id)
        published = await next_message(queue)
        await nothing_more(container, market.id, queue)

    row = await market_row(container, market.id)
    assert published == status_event(container, row)
    assert (published["status"], published["trading_open"]) == (
        action.target,
        action.target is MarketStatus.OPEN,
    )
    assert await container.market_cache.get(PUBLIC_LIST) is None


@both_settlements
async def test_a_settlement_that_closes_the_market_on_its_way_publishes_the_end_alone(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    market = await factory.market(status=MarketStatus.HALTED)

    async with watching(container, market.id) as queue:
        await action.by(lifecycle, admin, market.id)
        published = await next_message(queue)
        await nothing_more(container, market.id, queue)

    assert published["status"] == action.target


@pytest.mark.redis_down
@pytest.mark.parametrize("action", [APPROVE, HALT, RESOLVE], ids=str)
async def test_without_redis_an_action_commits_and_answers_all_the_same(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """SPEC §9: the cache and the live event are a convenience; Postgres decided."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market(status=action.starts[0])
    won_micro = 0
    if action is RESOLVE:
        won_micro = 60 * MICRO
        await factory.position(alice, market, Outcome.YES, shares_micro=won_micro)
    held_before = await factory.balance_micro(alice)

    answer = await action.by(lifecycle, admin, market.id)

    assert answer.status is action.target
    assert (await market_row(container, market.id)).status is action.target
    assert await factory.balance_micro(alice) == held_before + won_micro
    assert len(await audit_rows(container)) == 1
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("action", "field"),
    [
        (APPROVE, "yes_definition"),
        (REJECT, "reason"),
        (HALT, "reason"),
        (RESUME, "note"),
        (CLOSE, "note"),
        (RESOLVE, "evidence"),
        (VOID, "reason"),
    ],
    ids=str,
)
async def test_a_text_postgres_cannot_store_is_422_naming_the_field_before_anything_is_read(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
    field: str,
) -> None:
    """A NUL character is valid in JSON and in no text or JSON column: without the
    check, the reason of a rejection would fail in the database as a 500."""
    market = await factory.market(status=action.starts[0])
    before = await everything(container)
    spoiled = action.request.model_copy(update={field: f"Because{NUL} of this."})
    method = getattr(lifecycle, action.name)

    for market_id in (market.id, uuid7()):
        with pytest.raises(ValidationFailedError) as refused:
            await method(admin, market_id, spoiled)
        assert refused.value.status_code == 422
        assert refused.value.details == {"field": field}
        assert refused.value.message == UNSTORABLE_TEXT_MESSAGE

    assert await everything(container) == before


# --- The answer of an action -------------------------------------------------------------


@every_action
async def test_the_answer_of_an_action_is_the_detail_the_market_gives_right_afterwards(
    container: Container,
    factory: Factory,
    lifecycle: MarketLifecycleService,
    admin: AuthenticatedUser,
    action: Action,
) -> None:
    """The answer is built inside the transaction that makes the change (A.5), and it is
    what a reader gets from the committed rows a moment later: field for field, and
    character for character as it travels."""
    market = await market_for(container, factory, action)

    answer = await action.by(lifecycle, admin, market.id)

    afterwards = await container.markets.detail(admin, market.id)
    assert answer == afterwards
    assert answer.model_dump_json() == afterwards.model_dump_json()
    assert answer.status is action.target
    assert (answer.proposer_email, answer.viewer.can_settle) == (afterwards.proposer_email, True)
    assert (answer.void_cost_cr is None) == (action.target not in UNSETTLED_STATUSES)
    await assert_books_balance(container.uow_factory)


async def test_an_approval_answers_the_dates_it_edited_as_every_later_reader_gets_them(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    """The approval dialog sends its two times at an offset. The answer is built before
    the commit, from the rows of the transaction, so it spells them as the database
    keeps them and as the market's own page reads them afterwards: in UTC."""
    as_user(await factory.admin())
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    kolkata = timezone(timedelta(hours=5, minutes=30))
    body = APPROVE.body | {
        "close_at": (clock.now() + 3 * DAY).astimezone(kolkata).isoformat(),
        "resolve_by": (clock.now() + 5 * DAY).astimezone(kolkata).isoformat(),
        "question": "Will the Polymensa serve Älplermagronen next Friday?",
    }
    assert body["close_at"] == "2026-10-12T17:30:00+05:30"

    response = await client.post(APPROVE.path(proposal.id), json=body)
    read = await client.get(f"/api/markets/{proposal.id}")

    assert response.status_code == 200, response.text
    assert response.json() == read.json()
    assert (response.json()["close_at"], response.json()["resolve_by"]) == (
        "2026-10-12T12:00:00Z",
        "2026-10-14T12:00:00Z",
    )
    assert response.json()["question"] == body["question"]
    assert (await market_row(container, proposal.id)).close_at == clock.now() + 3 * DAY


@every_action
async def test_an_action_opens_exactly_one_unit_of_work(
    container: Container, factory: Factory, admin: AuthenticatedUser, action: Action
) -> None:
    """Its change and its answer are one transaction. The detail it returns is read
    before the commit, and neither the notice of an approval nor what follows the
    commit opens a unit of work of its own."""
    market = await market_for(container, factory, action)
    counted, units = lifecycle_on(container)

    answer = await action.by(counted, admin, market.id)

    assert units.opened == 1
    assert answer.status is action.target
    assert (await market_row(container, market.id)).status is action.target
    await assert_books_balance(container.uow_factory)


@every_action
async def test_an_action_that_took_effect_is_answered_even_with_no_connection_left_afterwards(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    action: Action,
) -> None:
    """The pool gives the request its one connection and has none after that. A read
    after the commit would be answered 503 for a change that stands, and the admin
    who sent a resolution again would get 409 for a market that paid out the first
    time. No such read exists: the request is answered 200."""
    acting_admin = await factory.admin()
    market = await market_for(container, factory, action)
    counted, units = lifecycle_on(container, at_most=1)
    monkeypatch.setattr(container, "market_lifecycle", counted)
    as_user(acting_admin)

    response = await client.post(action.path(market.id), json=action.body)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == action.target
    assert (units.opened, units.refused) == (1, 0)
    assert (await market_row(container, market.id)).status is action.target
    (audited,) = [row for row in await audit_rows(container) if row.action == action.audit]
    assert audited.actor_id == acting_admin.id
    # The stand-in is dry: whoever asks it for a unit of work now gets none.
    with pytest.raises(PoolTimeoutError):
        units()
    await assert_books_balance(container.uow_factory)


async def after_the_commit_fails(*args: object, **kwargs: object) -> None:
    raise RuntimeError("this step after the commit failed")


def logged_by_the_lifecycle(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The errors the lifecycle service itself logged, whatever else was logged."""
    return [
        record
        for record in caplog.records
        if record.name == market_lifecycle.__name__ and record.levelno >= logging.ERROR
    ]


@pytest.mark.parametrize("action", PUBLISHED, ids=str)
async def test_an_action_whose_cached_lists_cannot_be_dropped_answers_and_still_tells_open_phones(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    action: Action,
) -> None:
    """The change stands and its answer is built: what goes wrong after the commit is
    logged, the request is answered 200, and the next step still runs."""
    as_user(await factory.admin())
    market = await factory.market(status=action.starts[0])
    monkeypatch.setattr(container.market_cache, "invalidate_lists", after_the_commit_fails)

    with caplog.at_level(logging.ERROR, logger=market_lifecycle.__name__):
        async with watching(container, market.id) as queue:
            response = await client.post(action.path(market.id), json=action.body)
            published = await next_message(queue)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == action.target
    row = await market_row(container, market.id)
    assert row.status is action.target
    assert published == status_event(container, row)
    (logged,) = logged_by_the_lifecycle(caplog)
    assert str(market.id) in logged.getMessage()
    assert logged.exc_info is not None
    assert isinstance(logged.exc_info[1], RuntimeError)


@pytest.mark.parametrize("action", PUBLISHED, ids=str)
async def test_an_action_whose_status_event_cannot_be_published_answers_and_still_drops_the_lists(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    action: Action,
) -> None:
    acting_admin = await factory.admin()
    as_user(acting_admin)
    await factory.market()
    market = await factory.market(status=action.starts[0])
    await container.markets.list_markets(factory.identity(acting_admin), None)
    assert await container.market_cache.get(PUBLIC_LIST) is not None
    monkeypatch.setattr(container.live_quotes, "publish_status", after_the_commit_fails)

    with caplog.at_level(logging.ERROR, logger=market_lifecycle.__name__):
        response = await client.post(action.path(market.id), json=action.body)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == action.target
    assert (await market_row(container, market.id)).status is action.target
    assert await container.market_cache.get(PUBLIC_LIST) is None
    (logged,) = logged_by_the_lifecycle(caplog)
    assert str(market.id) in logged.getMessage()


@every_action
async def test_an_action_is_answered_when_redis_fails_in_a_way_no_adapter_expects(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    action: Action,
) -> None:
    """The adapters turn an unreachable Redis into a miss and a dropped event. An error
    of any other kind from underneath them still fails no action that has committed."""
    acting_admin = await factory.admin()
    as_user(acting_admin)
    market = await market_for(container, factory, action)
    for command in ("delete", "publish"):
        monkeypatch.setattr(container.redis, command, after_the_commit_fails)

    response = await client.post(action.path(market.id), json=action.body)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == action.target
    assert (await market_row(container, market.id)).status is action.target
    assert MarketDetailResponse.model_validate(response.json()) == (
        await container.markets.detail(factory.identity(acting_admin), market.id)
    )
    await assert_books_balance(container.uow_factory)


# --- Through the routes ------------------------------------------------------------------


@every_action
async def test_each_action_has_its_route_which_answers_the_market_as_it_is_afterwards(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    action: Action,
) -> None:
    acting_admin = await factory.admin()
    market = await factory.market(status=action.starts[0])
    as_user(acting_admin)

    response = await client.post(action.path(market.id), json=action.body)
    read = await client.get(f"/api/markets/{market.id}")

    assert response.status_code == 200, response.text
    assert MarketDetailResponse.model_validate(response.json()) == (
        await container.markets.detail(factory.identity(acting_admin), market.id)
    )
    assert response.json() == read.json(), "the answer is the market's own page"
    assert response.json()["status"] == action.target
    assert response.json()["proposer_email"] is not None
    (audited,) = await audit_rows(container)
    assert (audited.action, audited.actor_id) == (action.audit, acting_admin.id)


@every_action
async def test_a_route_answers_404_for_an_unknown_market_and_409_for_a_status_it_does_not_take(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    action: Action,
) -> None:
    as_user(await factory.admin())
    wrong = next(status for status in MarketStatus if status not in action.starts)
    if action in SETTLEMENTS:
        wrong = MarketStatus.RESOLVED
    market = await factory.market(status=wrong)

    unknown = await client.post(action.path(uuid7()), json=action.body)
    refused = await client.post(action.path(market.id), json=action.body)

    assert unknown.status_code == 404
    assert (unknown.json()["code"], unknown.json()["details"]) == ("NOT_FOUND", {})
    assert refused.status_code == 409
    assert (refused.json()["code"], refused.json()["details"]) == ("CONFLICT", {})
    assert await count_rows(container.uow_factory, AuditLogEntry) == 0


async def test_the_approve_route_answers_a_limit_with_409_naming_it_and_dates_with_422(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_user(await factory.admin())
    await factory.market()
    proposal = await factory.market(status=MarketStatus.PROPOSED)
    path = APPROVE.path(proposal.id)

    oversized = await client.post(
        path, json={"tier": "CUSTOM", "budget_cr": format_cr(MAX_MARKET_BUDGET + 1)}
    )
    in_the_past = await client.post(
        path, json={"tier": "TIGHT", "close_at": (clock.now() - SECOND).isoformat()}
    )
    monkeypatch.setattr(
        container, "market_lifecycle", lifecycle_with(container, max_open_markets=1)
    )
    no_room = await client.post(path, json={"tier": "TIGHT"})

    assert oversized.status_code == 409
    assert oversized.json() == {
        "code": "LIMIT_EXCEEDED",
        "message": BUDGET_LIMIT_MESSAGE,
        "details": {"limit": "MAX_MARKET_BUDGET"},
    }
    assert in_the_past.status_code == 422
    assert (in_the_past.json()["code"], in_the_past.json()["details"]) == (
        "VALIDATION_ERROR",
        {"field": "close_at"},
    )
    assert no_room.status_code == 409
    assert no_room.json() == {
        "code": "LIMIT_EXCEEDED",
        "message": OPEN_MARKETS_LIMIT_MESSAGE,
        "details": {"limit": "MAX_OPEN_MARKETS"},
    }
    assert (await market_row(container, proposal.id)).status is MarketStatus.PROPOSED


async def test_the_resolve_route_pays_the_winners_and_a_second_request_is_409(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    """SPEC §12, acceptance: "Resolving pays winners exactly their winning shares; a
    second resolution is rejected." """
    book = await traded(container, factory)
    before = await balances(factory, book.traders)
    as_user(await factory.admin())
    body = resolve_to(Outcome.YES).model_dump(mode="json")

    first = await client.post(RESOLVE.path(book.market.id), json=body)
    second = await client.post(RESOLVE.path(book.market.id), json=body | {"outcome": "NO"})

    assert first.status_code == 200, first.text
    assert (first.json()["status"], first.json()["outcome"]) == ("RESOLVED", "YES")
    assert first.json()["resolution"]["evidence"] == EVIDENCE
    assert (second.status_code, second.json()["code"]) == (409, "CONFLICT")
    after = await balances(factory, book.traders)
    assert {user.id: after[user.id] - before[user.id] for user in book.traders} == {
        book.alice.id: 80 * MICRO,
        book.bob.id: 0,
        book.carol.id: 15 * MICRO,
        book.dave.id: 0,
    }
    as_user(book.alice)
    seen = (await client.get(f"/api/markets/{book.market.id}")).json()
    assert (seen["viewer"]["payout_cr"], seen["viewer"]["refund_cr"]) == ("80", None)
    await assert_books_balance(container.uow_factory)


async def test_the_void_route_refunds_the_stakes_and_the_market_page_says_what_came_back(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    book = await traded(container, factory)
    stake_micro = container.market_view.net_stake_micro(
        await positions_of(container, book.alice, book.market)
    )
    held_before = await factory.balance_micro(book.alice)
    as_user(await factory.admin())

    response = await client.post(VOID.path(book.market.id), json=VOID.body)

    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["outcome"]) == ("VOID", "VOID")
    assert response.json()["resolution"]["evidence"] == VOID_REASON
    as_user(book.alice)
    seen = (await client.get(f"/api/markets/{book.market.id}")).json()
    assert (seen["viewer"]["refund_cr"], seen["viewer"]["payout_cr"]) == (
        format_cr(stake_micro),
        None,
    )
    assert seen["viewer"]["balance_cr"] == format_cr(held_before + stake_micro)
    assert [held["shares"] for held in seen["viewer"]["holdings"]] == ["0", "0"]
    await assert_books_balance(container.uow_factory)
