"""Markets as everyone reads them, and how one is proposed: `MarketService` (SPEC §6,
§9, §10, Appendix A.4, A.5, A.11).

Three things are proven here: what a proposal has to state and how many a day may be
made, which markets a list holds and when it comes from the cache, and what one market
tells the person looking at it, which for its liquidity is nothing unless that person
is an admin.

Every expected status, price, stake and payout is what `MarketView` and
`app.pricing.lmsr` make of the rows; every limit is read from the test settings. Trades
are the factory's buys and the `sell` below, which is their counterpart, so nothing here
waits for the trading service. Markets are settled by `MarketLifecycleService`.
"""

import asyncio
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.clock import local_date
from app.core.config import Settings
from app.core.enums import (
    AccountKind,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.errors import (
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, format_cr, to_cr
from app.integrations.redis.market_cache import MarketCache
from app.models import (
    AuditLogEntry,
    JournalEntry,
    Market,
    OutboxEvent,
    Position,
    Resolution,
    Trade,
    User,
)
from app.pricing import lmsr
from app.repositories.market import MarketRepository
from app.schemas.admin.markets import (
    ApproveMarketRequest,
    CloseMarketRequest,
    HaltMarketRequest,
    RejectMarketRequest,
    ResolveMarketRequest,
    VoidMarketRequest,
)
from app.schemas.common import Limit
from app.schemas.markets import (
    MarketDetailResponse,
    MarketListResponse,
    ProposeMarketRequest,
    SellPortion,
    TradeBlockReason,
)
from app.services.ledger import LedgerRef, PostingLine, trade_key
from app.services.market_view import HIDDEN_STATUSES, SETTLED_STATUSES, UNSETTLED_STATUSES
from app.services.markets import (
    PROPOSAL_LIMIT_MESSAGE,
    PUBLIC_STATUSES,
    UNSTORABLE_TEXT_MESSAGE,
    VOID_RULE,
    MarketService,
)
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, identity_of
from tests.support.settings import load_base_settings
from tests.support.units_of_work import CountedUnitsOfWork

pytestmark = pytest.mark.integration

BASE: Final[Settings] = load_base_settings()
PRECISION: Final[int] = BASE.pricing.decimal_precision
PRESETS: Final[tuple[int, ...]] = tuple(BASE.pricing.stake_presets_micro)
"""TRADE_STAKE_PRESETS_CR in µCR: the buy buttons, in the order they are offered."""

STAKE_STEP: Final[int] = BASE.pricing.stake_step_micro
"""TRADE_STAKE_STEP_CR in µCR: what lies between two positions of the buy slider."""

STAKE_LIMIT: Final[int] = BASE.pricing.max_stake_per_user_market_micro
MIN_SHARES: Final[int] = BASE.pricing.min_trade_shares_micro
DAILY_LIMIT: Final[int] = BASE.lifecycle.max_proposals_per_user_per_day
LIFETIME: Final[timedelta] = BASE.risk.max_market_lifetime
DELAY: Final[timedelta] = BASE.lifecycle.max_resolution_delay

WALLET: Final[int] = 5 * STAKE_LIMIT
"""What a trader of these tests starts with: more than every preset and the stake limit."""

MARKETS: Final[str] = "/api/markets"
PUBLIC_LIST: Final[str] = MarketCache.list_key(PUBLIC_STATUSES)
"""The cache key of the list a request without `status` gets."""

NOTHING: Final[Decimal] = to_cr(0)
"""No credits and no shares, as an amount of the API."""

SECOND: Final[timedelta] = timedelta(seconds=1)
TICK: Final[timedelta] = timedelta(microseconds=1)
NUL: Final[str] = "\x00"

TEXTS_OF_A_PROPOSAL: Final[tuple[str, ...]] = (
    "question",
    "yes_definition",
    "no_definition",
    "resolution_criteria",
    "resolution_source",
)

BLOCKED_FOR_SECONDS: Final[float] = 0.3
"""Long enough for a request to reach the lock it then waits for."""

WAIT_SECONDS: Final[float] = 5.0
"""How long a test waits for what should come at once, before it fails instead of
hanging."""

LINGER_SECONDS: Final[float] = 0.15
"""How long a transaction is kept open by `linger_after`: many times what a parallel
request needs to get a connection and reach the same statement."""

type FlushRedis = Callable[[], Awaitable[None]]


# --- Asking and looking ------------------------------------------------------------------


def proposal(now: datetime, **overrides: Any) -> ProposeMarketRequest:
    """A proposal that breaks no rule at `now`, with `overrides` on top."""
    values: dict[str, Any] = {
        "question": "Will the Polymensa serve Älplermagronen on Friday?",
        "yes_definition": "It is on the menu that day.",
        "no_definition": "It is not on the menu that day.",
        "resolution_criteria": "The menu published for that Friday decides.",
        "resolution_source": "ethz.ch/menu",
        "close_at": now + timedelta(days=7),
        "resolve_by": now + timedelta(days=9),
        "policy_accepted": True,
    }
    return ProposeMarketRequest(**(values | overrides))


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


def listed(
    container: Container, markets: Collection[Market], statuses: Collection[MarketStatus]
) -> list[UUID]:
    """The ids a list of `statuses` owes: the markets whose effective status is one of
    them, soonest `close_at` first."""
    view, now = container.market_view, container.clock.now()
    in_order = sorted(markets, key=lambda market: (market.close_at, market.id))
    return [market.id for market in in_order if view.effective_status(market, now) in statuses]


def ids(answer: MarketListResponse) -> list[UUID]:
    return [item.id for item in answer.items]


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


# --- Proposing ---------------------------------------------------------------------------


async def test_a_proposal_becomes_a_proposed_market_that_states_what_was_asked(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = await factory.user()
    request = proposal(clock.now())

    answer = await container.markets.propose(factory.identity(alice), request)

    row = await market_row(container, answer.id)
    assert row.status is MarketStatus.PROPOSED
    assert (
        row.question,
        row.yes_def,
        row.no_def,
        row.resolution_criteria,
        row.resolution_source,
    ) == (
        request.question,
        request.yes_definition,
        request.no_definition,
        request.resolution_criteria,
        request.resolution_source,
    )
    assert (row.close_at, row.resolve_by) == (request.close_at, request.resolve_by)
    assert (row.created_by, row.created_at) == (alice.id, clock.now())
    # A proposal names no liquidity: the tier, the budget and b come with the approval,
    # and nobody has traded.
    assert (row.tier, row.budget_micro, row.b_micro) == (None, 0, 0)
    assert (row.q_yes_micro, row.q_no_micro, row.version, row.volume_micro) == (0, 0, 0, 0)
    assert (row.approved_by, row.opened_at, row.closed_at, row.rejection_reason) == (
        None,
        None,
        None,
        None,
    )


async def test_the_answer_to_a_proposal_is_its_detail_as_the_proposer_sees_it(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The answer is built in the transaction that files the proposal, and it is what
    the proposer reads of it a moment later: field for field, and character for
    character as it travels, whatever offset the form wrote its two times with."""
    alice = factory.identity(await factory.user(balance_micro=WALLET))
    zurich = container.settings.app.timezone
    close_at = (clock.now() + timedelta(days=7)).astimezone(zurich)
    request = proposal(clock.now(), close_at=close_at, resolve_by=close_at + timedelta(days=2))
    assert request.close_at.utcoffset() == timedelta(hours=2)

    answer = await container.markets.propose(alice, request)

    afterwards = await container.markets.detail(alice, answer.id)
    assert answer == afterwards
    assert answer.model_dump_json() == afterwards.model_dump_json()
    assert answer.close_at.utcoffset() == timedelta(0), "as the database keeps it"
    assert (answer.status, answer.trading_open, answer.volume_cr) == (
        MarketStatus.PROPOSED,
        False,
        NOTHING,
    )
    # How much liquidity the market gets is not the proposer's to know, then or later.
    assert (answer.tier, answer.b_cr, answer.max_loss_cr) == (None, None, None)
    # A market that was never opened has no price of its own.
    assert (answer.p_yes, answer.p_no) == (0.5, 0.5)
    assert answer.viewer.balance_cr == to_cr(WALLET)
    assert answer.viewer.can_trade is False


async def test_a_proposal_opens_exactly_one_unit_of_work(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Filing it and building its answer are one transaction: nothing is read once
    that has committed."""
    alice = factory.identity(await factory.user(balance_micro=WALLET))
    units = CountedUnitsOfWork(container.uow_factory)
    markets = MarketService(
        units, container.settings, container.clock, container.market_view, container.market_cache
    )

    answer = await markets.propose(alice, proposal(clock.now()))

    assert units.opened == 1
    assert answer == await container.markets.detail(alice, answer.id)
    assert await count_rows(container.uow_factory, Market) == 1


async def test_a_proposal_that_was_filed_is_answered_even_with_no_connection_left_afterwards(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool gives the request its one connection and has none after that. A read
    after the commit would be answered 503 for a proposal that was filed, and the
    form's second attempt would file it again and use up the day's limit. No such
    read exists: the request is answered 201, and the proposal exists once."""
    alice = await factory.user(balance_micro=WALLET)
    units = CountedUnitsOfWork(container.uow_factory, at_most=1)
    monkeypatch.setattr(
        container,
        "markets",
        MarketService(
            units,
            container.settings,
            container.clock,
            container.market_view,
            container.market_cache,
        ),
    )
    as_user(alice)

    response = await client.post(MARKETS, json=proposal(clock.now()).model_dump(mode="json"))

    assert response.status_code == 201, response.text
    assert (units.opened, units.refused) == (1, 0)
    assert response.json()["status"] == "PROPOSED"
    assert response.json()["viewer"]["balance_cr"] == format_cr(WALLET)
    row = await market_row(container, UUID(response.json()["id"]))
    assert row.created_by == alice.id
    assert await count_rows(container.uow_factory, Market) == 1
    # The stand-in is dry: reading the proposal back now is what would have failed.
    read = await client.get(f"{MARKETS}/{row.id}")
    assert (read.status_code, read.json()["code"]) == (503, "SERVICE_UNAVAILABLE")
    assert units.refused == 1


async def test_a_proposal_is_answered_whatever_redis_does(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proposal drops no list and publishes nothing, so nothing of Redis stands
    between its commit and its answer, not even an error no adapter expects."""

    async def fails(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Redis failed in a way of its own")

    for command in ("delete", "publish", "hash_get", "hash_set"):
        monkeypatch.setattr(container.redis, command, fails)
    as_user(await factory.user())

    response = await client.post(MARKETS, json=proposal(clock.now()).model_dump(mode="json"))

    assert response.status_code == 201, response.text
    assert await count_rows(container.uow_factory, Market) == 1


async def test_a_proposal_is_not_public_so_it_opens_no_account_and_tells_nobody(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """No market account, no chart point, no audit row, and the cached lists stay."""
    alice = factory.identity(await factory.user())
    await container.markets.list_markets(alice, None)
    cached = await container.market_cache.get(PUBLIC_LIST)
    assert cached is not None

    answer = await container.markets.propose(alice, proposal(clock.now()))

    async with container.uow_factory() as uow:
        assert await uow.accounts.get_market_account(answer.id) is None
    for table in (OutboxEvent, AuditLogEntry, JournalEntry):
        assert await count_rows(container.uow_factory, table) == 0
    assert await container.market_cache.get(PUBLIC_LIST) == cached


@pytest.mark.parametrize(
    ("closes_in", "resolves_after", "field"),
    [
        (timedelta(0), DELAY, "close_at"),
        (-SECOND, timedelta(0), "close_at"),
        (LIFETIME + TICK, timedelta(0), "close_at"),
        (TICK, DELAY + TICK, "resolve_by"),
        (LIFETIME, DELAY + TICK, "resolve_by"),
        (-SECOND, DELAY + SECOND, "close_at"),
    ],
    ids=[
        "closes-now",
        "closes-in-the-past",
        "open-a-moment-longer-than-the-lifetime",
        "decided-a-moment-too-late",
        "at-the-lifetime-and-decided-a-moment-too-late",
        "both-wrong-the-close-is-named",
    ],
)
async def test_a_proposal_whose_dates_break_a_rule_is_422_naming_the_field(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    closes_in: timedelta,
    resolves_after: timedelta,
    field: str,
) -> None:
    alice = factory.identity(await factory.user())
    close_at = clock.now() + closes_in
    request = proposal(clock.now(), close_at=close_at, resolve_by=close_at + resolves_after)

    with pytest.raises(ValidationFailedError) as refused:
        await container.markets.propose(alice, request)

    assert refused.value.status_code == 422
    assert refused.value.details == {"field": field}
    assert await count_rows(container.uow_factory, Market) == 0


@pytest.mark.parametrize(
    ("closes_in", "resolves_after"),
    [(TICK, timedelta(0)), (LIFETIME, DELAY), (LIFETIME, timedelta(0)), (TICK, DELAY)],
    ids=[
        "closes-the-next-instant-and-is-decided-then",
        "at-both-limits",
        "at-the-lifetime",
        "at-the-resolution-delay",
    ],
)
async def test_a_proposal_at_the_edge_of_a_date_rule_is_taken(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    closes_in: timedelta,
    resolves_after: timedelta,
) -> None:
    alice = factory.identity(await factory.user())
    close_at = clock.now() + closes_in
    request = proposal(clock.now(), close_at=close_at, resolve_by=close_at + resolves_after)

    answer = await container.markets.propose(alice, request)

    assert (answer.close_at, answer.resolve_by) == (close_at, close_at + resolves_after)


async def test_the_dates_of_a_proposal_are_instants_whatever_offset_they_are_written_in(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The form sends the two times with the offset of APP_TIMEZONE. A deadline that is
    past is past in every offset, and one that is kept is kept as the instant it is."""
    alice = factory.identity(await factory.user())
    far_east = timezone(timedelta(hours=14))
    far_west = timezone(-timedelta(hours=12))
    # Tomorrow's date on the far-east clock, and still a moment ago.
    just_passed = (clock.now() - SECOND).astimezone(far_east)
    assert just_passed.date() > clock.now().date()
    in_a_week = (clock.now() + timedelta(days=7)).astimezone(far_west)

    with pytest.raises(ValidationFailedError) as refused:
        await container.markets.propose(
            alice, proposal(clock.now(), close_at=just_passed, resolve_by=just_passed)
        )
    answer = await container.markets.propose(
        alice, proposal(clock.now(), close_at=in_a_week, resolve_by=in_a_week)
    )

    assert refused.value.details == {"field": "close_at"}
    assert answer.close_at == clock.now() + timedelta(days=7)
    assert (await market_row(container, answer.id)).close_at == in_a_week


@pytest.mark.parametrize("field", TEXTS_OF_A_PROPOSAL)
async def test_a_text_postgres_cannot_store_is_422_naming_the_field_not_a_failed_insert(
    container: Container, factory: Factory, clock: FrozenClock, field: str
) -> None:
    """A NUL character is valid in JSON and in no text column."""
    alice = factory.identity(await factory.user())
    request = proposal(clock.now(), **{field: f"Will it rain{NUL} tomorrow?"})

    with pytest.raises(ValidationFailedError) as refused:
        await container.markets.propose(alice, request)

    assert refused.value.details == {"field": field}
    assert refused.value.message == UNSTORABLE_TEXT_MESSAGE
    assert await count_rows(container.uow_factory, Market) == 0


async def test_proposals_beyond_the_daily_limit_are_409_naming_the_limit(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = factory.identity(await factory.user())
    bob = factory.identity(await factory.user())
    for _ in range(DAILY_LIMIT):
        await container.markets.propose(alice, proposal(clock.now()))

    with pytest.raises(LimitExceededError) as refused:
        await container.markets.propose(alice, proposal(clock.now()))

    assert refused.value.status_code == 409
    assert refused.value.details == {"limit": Limit.MAX_PROPOSALS_PER_USER_PER_DAY}
    assert refused.value.message == PROPOSAL_LIMIT_MESSAGE
    assert await count_rows(container.uow_factory, Market) == DAILY_LIMIT
    # The limit is each user's own.
    await container.markets.propose(bob, proposal(clock.now()))


async def test_a_proposal_counts_for_its_day_whatever_became_of_it(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Rejected and approved proposals were proposed all the same."""
    alice = factory.identity(await factory.user())
    admin = factory.identity(await factory.admin())
    made = [
        await container.markets.propose(alice, proposal(clock.now())) for _ in range(DAILY_LIMIT)
    ]
    await container.market_lifecycle.reject(
        admin, made[0].id, RejectMarketRequest(reason="Asked before.")
    )
    if DAILY_LIMIT > 1:
        await container.market_lifecycle.approve(
            admin, made[1].id, ApproveMarketRequest(tier=MarketTier.DEFAULT)
        )

    with pytest.raises(LimitExceededError):
        await container.markets.propose(alice, proposal(clock.now()))


async def test_a_proposal_with_bad_dates_is_told_so_even_at_the_daily_limit(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The request is judged before the day is counted."""
    alice = factory.identity(await factory.user())
    for _ in range(DAILY_LIMIT):
        await container.markets.propose(alice, proposal(clock.now()))

    with pytest.raises(ValidationFailedError) as refused:
        await container.markets.propose(alice, proposal(clock.now(), close_at=clock.now()))

    assert refused.value.details == {"field": "close_at"}


async def test_the_proposal_limit_starts_again_at_midnight_in_the_app_timezone(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Two minutes before midnight in Zurich and two minutes after it are one day in
    UTC and two days on the calendar the limit counts."""
    zone = container.settings.app.timezone
    alice = factory.identity(await factory.user())
    before_midnight = datetime(2026, 10, 9, 23, 58, tzinfo=zone)
    clock.set(before_midnight)
    for _ in range(DAILY_LIMIT):
        await container.markets.propose(alice, proposal(clock.now()))
    with pytest.raises(LimitExceededError):
        await container.markets.propose(alice, proposal(clock.now()))

    after_midnight = clock.advance(timedelta(minutes=4))

    assert local_date(after_midnight, zone) > local_date(before_midnight, zone)
    assert after_midnight.astimezone(UTC).date() == before_midnight.astimezone(UTC).date()
    for _ in range(DAILY_LIMIT):
        await container.markets.propose(alice, proposal(clock.now()))
    with pytest.raises(LimitExceededError):
        await container.markets.propose(alice, proposal(clock.now()))
    assert await count_rows(container.uow_factory, Market) == 2 * DAILY_LIMIT


async def test_midnight_in_utc_is_not_the_start_of_a_proposal_day(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Half past one at night in Zurich and an hour later are two days in UTC and one
    day on the calendar the limit counts."""
    zone = container.settings.app.timezone
    alice = factory.identity(await factory.user())
    at_night = datetime(2026, 10, 10, 1, 30, tzinfo=zone)
    clock.set(at_night)
    for _ in range(DAILY_LIMIT):
        await container.markets.propose(alice, proposal(clock.now()))

    an_hour_later = clock.advance(timedelta(hours=1))

    assert local_date(an_hour_later, zone) == local_date(at_night, zone)
    assert an_hour_later.astimezone(UTC).date() > at_night.astimezone(UTC).date()
    with pytest.raises(LimitExceededError):
        await container.markets.propose(alice, proposal(clock.now()))


async def test_parallel_proposals_at_the_limit_admit_only_as_many_as_it_leaves(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each request counts the day's proposals before it inserts its own. Without the
    lock on the user's proposals, requests that overlap would all count the same
    number, so each is kept in its transaction between the count and the insert."""
    alice = factory.identity(await factory.user())
    bob = factory.identity(await factory.user())
    for _ in range(DAILY_LIMIT - 1):
        await container.markets.propose(alice, proposal(clock.now()))
    linger_after(monkeypatch, MarketRepository, "count_created_by_since")

    answers = await asyncio.gather(
        *(container.markets.propose(alice, proposal(clock.now())) for _ in range(3)),
        container.markets.propose(bob, proposal(clock.now())),
        return_exceptions=True,
    )

    taken = [answer for answer in answers if isinstance(answer, MarketDetailResponse)]
    refused = [answer for answer in answers if isinstance(answer, LimitExceededError)]
    # One of alice's three, and bob's: the lock is each user's own.
    assert (len(taken), len(refused)) == (2, 2)
    assert isinstance(answers[-1], MarketDetailResponse)
    assert await count_rows(container.uow_factory, Market) == DAILY_LIMIT + 1


async def test_a_proposal_waits_for_the_users_other_proposal_and_nobody_elses(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The lock that makes the count and the insert one step is the user's own. A
    proposal is dated when it arrived, also when it had to wait."""
    alice = await factory.user()
    bob = factory.identity(await factory.user())
    arrived_at = clock.now()

    async with container.uow_factory() as another_proposal:
        await another_proposal.advisory_lock(f"proposals:{alice.id}")
        waiting = asyncio.create_task(
            container.markets.propose(factory.identity(alice), proposal(arrived_at))
        )
        await container.markets.propose(bob, proposal(arrived_at))
        await asyncio.sleep(BLOCKED_FOR_SECONDS)
        assert not waiting.done()
        clock.advance(timedelta(minutes=5))

    answer = await asyncio.wait_for(waiting, WAIT_SECONDS)

    assert answer.created_at == arrived_at
    assert await count_rows(container.uow_factory, Market) == 2


async def test_the_route_takes_a_proposal_and_answers_201_with_the_market(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    alice = await factory.user(balance_micro=WALLET)
    as_user(alice)
    zone = container.settings.app.timezone
    close_at = (clock.now() + timedelta(days=7)).astimezone(zone)
    body = proposal(clock.now(), close_at=close_at).model_dump(mode="json")
    assert body["close_at"].endswith("+02:00")

    response = await client.post(MARKETS, json=body)

    assert response.status_code == 201
    answer = MarketDetailResponse.model_validate(response.json())
    assert answer == await container.markets.detail(factory.identity(alice), answer.id)
    assert response.json() == (await client.get(f"{MARKETS}/{answer.id}")).json()
    assert response.json()["status"] == "PROPOSED"
    assert response.json()["close_at"] == "2026-10-16T12:00:00Z"
    assert response.json()["viewer"]["balance_cr"] == format_cr(WALLET)


@pytest.mark.parametrize(
    ("field", "written"),
    [
        ("close_at", lambda now: (now - SECOND).isoformat()),
        ("resolve_by", lambda now: (now + timedelta(days=7) + DELAY + SECOND).isoformat()),
        ("question", lambda now: f"Will it rain{NUL} tomorrow?"),
    ],
    ids=["a-deadline-that-has-passed", "decided-too-late", "a-text-with-a-nul"],
)
async def test_the_route_answers_a_rule_of_the_service_with_422_and_the_field(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    field: str,
    written: Callable[[datetime], str],
) -> None:
    as_user(await factory.user())
    body = proposal(clock.now()).model_dump(mode="json") | {field: written(clock.now())}

    response = await client.post(MARKETS, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"] == {"field": field}
    assert response.json()["message"] != ValidationFailedError.default_message
    assert await count_rows(container.uow_factory, Market) == 0


@pytest.mark.parametrize(
    ("field", "written"),
    [
        ("requested_tier", lambda now: "DEFAULT"),
        ("tier", lambda now: "TIGHT"),
        ("budget_cr", lambda now: "50"),
        ("resolve_by", lambda now: (now + timedelta(days=1)).isoformat()),
        ("leverage", lambda now: "10"),
    ],
    ids=[
        "a-tier-it-would-like",
        "the-tier-of-vis",
        "a-budget",
        "decided-before-the-close",
        "a-field-nobody-asked-for",
    ],
)
async def test_the_route_refuses_what_the_request_model_refuses_and_writes_nothing(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    field: str,
    written: Callable[[datetime], str],
) -> None:
    as_user(await factory.user())
    body = proposal(clock.now()).model_dump(mode="json") | {field: written(clock.now())}

    response = await client.post(MARKETS, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"]["errors"]
    assert await count_rows(container.uow_factory, Market) == 0


@pytest.mark.parametrize(
    "liquidity", ["requested_tier", "tier", "b_cr", "budget_cr", "max_loss_cr"]
)
async def test_a_proposal_cannot_say_how_much_liquidity_its_market_should_get(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    liquidity: str,
) -> None:
    """The request has no such field: naming one is refused on it, whoever proposes."""
    body = proposal(clock.now()).model_dump(mode="json") | {liquidity: "DEFAULT"}

    for proposer in (await factory.user(), await factory.admin()):
        as_user(proposer)
        response = await client.post(MARKETS, json=body)

        assert response.status_code == 422, response.text
        assert [
            (error["loc"], error["type"]) for error in response.json()["details"]["errors"]
        ] == [(["body", liquidity], "extra_forbidden")]
    assert await count_rows(container.uow_factory, Market) == 0


@pytest.mark.parametrize(
    "policy",
    [{"policy_accepted": False}, {"policy_accepted": "true"}, {"policy_accepted": 1}, None],
    ids=["declined", "a-word", "a-number", "left-out"],
)
async def test_a_proposal_without_the_usage_policy_accepted_is_422_on_that_field(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    policy: dict[str, object] | None,
) -> None:
    """Every proposal accepts the policy again, with the JSON `true` and nothing that
    merely counts as true. Anything else writes no market and counts against no limit."""
    as_user(await factory.user())
    body = proposal(clock.now()).model_dump(mode="json")
    if policy is None:
        del body["policy_accepted"]
    else:
        body |= policy

    response = await client.post(MARKETS, json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert [error["loc"] for error in response.json()["details"]["errors"]] == [
        ["body", "policy_accepted"]
    ]
    assert await count_rows(container.uow_factory, Market) == 0

    accepted = await client.post(MARKETS, json=body | {"policy_accepted": True})

    assert accepted.status_code == 201, accepted.text
    assert "policy_accepted" not in accepted.json(), "nothing of it is kept with the market"
    assert await count_rows(container.uow_factory, Market) == 1


async def test_the_route_answers_the_daily_limit_with_409_and_the_limit(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    alice = await factory.user()
    as_user(alice)
    body = proposal(clock.now()).model_dump(mode="json")
    for _ in range(DAILY_LIMIT):
        assert (await client.post(MARKETS, json=body)).status_code == 201

    response = await client.post(MARKETS, json=body)

    assert response.status_code == 409
    assert response.json() == {
        "code": "LIMIT_EXCEEDED",
        "message": PROPOSAL_LIMIT_MESSAGE,
        "details": {"limit": "MAX_PROPOSALS_PER_USER_PER_DAY"},
    }
    assert await count_rows(container.uow_factory, Market) == DAILY_LIMIT


# --- Listing -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class World:
    """One market of every kind a list can be asked for. Their deadlines differ, so the
    order of a list is theirs and not the order they were made in."""

    void: Market
    resolved: Market
    proposed: Market
    open: Market
    rejected: Market
    halted: Market
    closed: Market
    closes_first: Market
    """OPEN, with the soonest deadline of the markets that still trade."""

    past_deadline: Market
    """Stored OPEN, and reported CLOSED: its `close_at` has passed (A.4)."""

    expired: Market
    """Stored PROPOSED, and reported EXPIRED: nobody decided on it before its
    `close_at` (A.4)."""

    @property
    def markets(self) -> tuple[Market, ...]:
        return tuple(getattr(self, field.name) for field in fields(self))


@pytest.fixture
async def world(factory: Factory) -> World:
    day = timedelta(days=1)
    return World(
        void=await factory.market(status=MarketStatus.VOID, close_in=5 * day),
        resolved=await factory.market(
            status=MarketStatus.RESOLVED, close_in=4 * day, outcome=ResolutionOutcome.NO
        ),
        proposed=await factory.market(status=MarketStatus.PROPOSED, close_in=6 * day),
        open=await factory.market(close_in=3 * day),
        rejected=await factory.market(status=MarketStatus.REJECTED, close_in=8 * day),
        halted=await factory.market(status=MarketStatus.HALTED, close_in=2 * day),
        closed=await factory.market(status=MarketStatus.CLOSED, close_in=day),
        closes_first=await factory.market(close_in=day / 2),
        past_deadline=await factory.market(close_in=-2 * timedelta(hours=1)),
        expired=await factory.market(
            status=MarketStatus.PROPOSED, close_in=-3 * timedelta(hours=1)
        ),
    )


FOR_ADMINS: Final[frozenset[MarketStatus]] = HIDDEN_STATUSES | {MarketStatus.EXPIRED}
"""The statuses only an admin may list: what a proposal can be reported as."""


@pytest.fixture
async def viewer(factory: Factory) -> AuthenticatedUser:
    """A plain user."""
    return factory.identity(await factory.user())


@pytest.fixture
async def admin(factory: Factory) -> AuthenticatedUser:
    return factory.identity(await factory.admin())


async def test_without_a_status_the_list_holds_every_public_market_soonest_close_first(
    container: Container, world: World, viewer: AuthenticatedUser
) -> None:
    answer = await container.markets.list_markets(viewer, None)

    assert ids(answer) == [
        world.past_deadline.id,
        world.closes_first.id,
        world.closed.id,
        world.halted.id,
        world.open.id,
        world.resolved.id,
        world.void.id,
    ]
    assert ids(answer) == listed(container, world.markets, PUBLIC_STATUSES)


async def test_markets_that_close_at_the_same_instant_are_listed_in_the_order_of_their_ids(
    container: Container, factory: Factory, viewer: AuthenticatedUser
) -> None:
    twins = [await factory.market() for _ in range(4)]
    assert len({market.close_at for market in twins}) == 1

    answer = await container.markets.list_markets(viewer, None)

    assert ids(answer) == sorted(market.id for market in twins)


async def test_a_listed_market_is_the_card_market_view_makes_of_its_row(
    container: Container, factory: Factory, world: World, viewer: AuthenticatedUser
) -> None:
    """The effective status, the display price of its q, volume and version, and for a
    settled market how it ended."""
    trader = await factory.user(balance_micro=WALLET)
    await factory.position(trader, world.open, Outcome.YES, shares_micro=80 * MICRO)
    await factory.position(trader, world.halted, Outcome.NO, shares_micro=30 * MICRO)
    view, now = container.market_view, container.clock.now()

    answer = await container.markets.list_markets(viewer, None)

    cards = {item.id: item for item in answer.items}
    for market in world.markets:
        if market.status in HIDDEN_STATUSES:
            continue
        row = await market_row(container, market.id)
        assert cards[market.id] == view.summary(row, now, await resolution_of(container, market))
    traded = cards[world.open.id]
    assert (traded.p_yes, traded.p_no) == view.probabilities(view.p_yes_micro(world.open))
    assert traded.p_yes > 0.5 > cards[world.halted.id].p_yes
    assert (traded.version, traded.volume_cr) == (1, to_cr(world.open.volume_micro))
    assert (cards[world.resolved.id].outcome, cards[world.void.id].outcome) == (
        ResolutionOutcome.NO,
        ResolutionOutcome.VOID,
    )
    assert all(card.outcome is None for card in answer.items if card.status not in SETTLED_STATUSES)
    late = cards[world.past_deadline.id]
    assert (late.status, late.trading_open) == (MarketStatus.CLOSED, False)
    assert [card.trading_open for card in answer.items] == [
        card.status is MarketStatus.OPEN for card in answer.items
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_market_past_its_deadline_is_listed_as_closed_and_not_as_open(
    container: Container, world: World, viewer: AuthenticatedUser
) -> None:
    opened = await container.markets.list_markets(viewer, [MarketStatus.OPEN])
    closed = await container.markets.list_markets(viewer, [MarketStatus.CLOSED])

    assert ids(opened) == [world.closes_first.id, world.open.id]
    assert ids(closed) == [world.past_deadline.id, world.closed.id]
    assert {item.status for item in closed.items} == {MarketStatus.CLOSED}


async def test_a_market_moves_from_the_open_list_to_the_closed_one_at_its_deadline(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    viewer: AuthenticatedUser,
    flush_redis: FlushRedis,
) -> None:
    market = await factory.market(close_in=timedelta(hours=1))

    async def lists() -> tuple[list[UUID], list[UUID]]:
        await flush_redis()
        opened = await container.markets.list_markets(viewer, [MarketStatus.OPEN])
        closed = await container.markets.list_markets(viewer, [MarketStatus.CLOSED])
        return ids(opened), ids(closed)

    clock.set(market.close_at - TICK)
    assert await lists() == ([market.id], [])
    clock.set(market.close_at)
    assert await lists() == ([], [market.id])
    assert (await market_row(container, market.id)).status is MarketStatus.OPEN


@pytest.mark.parametrize(
    "statuses",
    [
        [MarketStatus.OPEN, MarketStatus.HALTED],
        [MarketStatus.RESOLVED, MarketStatus.VOID],
        [MarketStatus.HALTED],
        [MarketStatus.VOID, MarketStatus.CLOSED, MarketStatus.OPEN],
    ],
    ids=["the-open-chip", "the-resolved-chip", "one-status", "three-in-any-order"],
)
async def test_several_statuses_list_the_markets_of_each_in_one_order(
    container: Container, world: World, viewer: AuthenticatedUser, statuses: list[MarketStatus]
) -> None:
    answer = await container.markets.list_markets(viewer, statuses)

    assert ids(answer) == listed(container, world.markets, statuses)
    assert {item.status for item in answer.items} == set(statuses)


async def test_a_status_named_twice_counts_once(
    container: Container, world: World, viewer: AuthenticatedUser
) -> None:
    twice = [MarketStatus.HALTED, MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.OPEN]

    answer = await container.markets.list_markets(viewer, twice)

    assert ids(answer) == [world.closes_first.id, world.halted.id, world.open.id]


async def test_no_status_at_all_lists_nothing(
    container: Container, world: World, viewer: AuthenticatedUser
) -> None:
    """What the route cannot ask for, another caller of the service can: an empty set
    of statuses is not the same as leaving the filter out."""
    assert (await container.markets.list_markets(viewer, [])).items == []


@pytest.mark.parametrize(
    "statuses",
    [
        [MarketStatus.PROPOSED],
        [MarketStatus.REJECTED],
        [MarketStatus.EXPIRED],
        [MarketStatus.OPEN, MarketStatus.PROPOSED],
        [MarketStatus.OPEN, MarketStatus.EXPIRED],
        list(MarketStatus),
    ],
    ids=[
        "proposed",
        "rejected",
        "expired",
        "proposed-with-a-public-status",
        "expired-with-a-public-status",
        "every-status",
    ],
)
async def test_proposed_expired_and_rejected_markets_are_listed_for_admins_and_403_for_the_rest(
    container: Container,
    factory: Factory,
    world: World,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
    statuses: list[MarketStatus],
) -> None:
    barista = factory.identity(await factory.barista())
    proposer = await factory.user()
    own = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)

    for refused in (viewer, barista, factory.identity(proposer)):
        with pytest.raises(ForbiddenError) as error:
            await container.markets.list_markets(refused, statuses)
        assert error.value.status_code == 403
    answer = await container.markets.list_markets(admin, statuses)

    assert ids(answer) == listed(container, (*world.markets, own), statuses)
    assert {item.status for item in answer.items} == set(statuses)


async def test_a_suspended_admin_is_no_admin_to_the_list(
    container: Container, factory: Factory, world: World
) -> None:
    """A suspended account keeps its address and none of its rights. The guards stop it
    before the service; the service does not rely on that."""
    suspended = identity_of(await factory.user(status=UserStatus.SUSPENDED), UserRole.ADMIN)

    with pytest.raises(ForbiddenError):
        await container.markets.list_markets(suspended, [MarketStatus.PROPOSED])


@hypothesis_settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(statuses=st.lists(st.sampled_from(MarketStatus), max_size=9))
async def test_any_set_of_statuses_lists_exactly_the_markets_in_them_to_whoever_may_ask(
    container: Container,
    world: World,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
    statuses: list[MarketStatus],
) -> None:
    """Whatever is asked for, in any order and however often: an admin gets the markets
    whose effective status was named, soonest close first. A plain user gets the same
    list, or 403 as soon as PROPOSED, EXPIRED or REJECTED is among them. Lists come
    from the cache or from Postgres as it happens; the answer is the same."""
    expected = listed(container, world.markets, statuses)

    assert ids(await container.markets.list_markets(admin, statuses)) == expected
    if FOR_ADMINS.isdisjoint(statuses):
        assert ids(await container.markets.list_markets(viewer, statuses)) == expected
    else:
        with pytest.raises(ForbiddenError):
            await container.markets.list_markets(viewer, statuses)


async def test_the_route_lists_markets_with_amounts_as_strings_and_403_for_a_hidden_status(
    client: httpx.AsyncClient,
    as_user: ActAs,
    container: Container,
    factory: Factory,
    world: World,
) -> None:
    trader = await factory.user(balance_micro=WALLET)
    await factory.position(trader, world.open, Outcome.YES, shares_micro=80 * MICRO)
    as_user(trader)

    everything = await client.get(MARKETS)
    chip = await client.get(MARKETS, params=[("status", "OPEN"), ("status", "HALTED")])
    hidden = await client.get(MARKETS, params=[("status", "OPEN"), ("status", "PROPOSED")])

    assert everything.status_code == 200
    assert MarketListResponse.model_validate(everything.json()) == (
        await container.markets.list_markets(factory.identity(trader), None)
    )
    assert [item["id"] for item in chip.json()["items"]] == [
        str(world.closes_first.id),
        str(world.halted.id),
        str(world.open.id),
    ]
    traded = chip.json()["items"][2]
    assert traded["volume_cr"] == format_cr(world.open.volume_micro)
    assert isinstance(traded["p_yes"], float)
    assert hidden.status_code == 403
    assert (hidden.json()["code"], hidden.json()["details"]) == ("FORBIDDEN", {})

    as_user(await factory.admin())
    for_admins = await client.get(MARKETS, params={"status": "PROPOSED"})
    assert [item["id"] for item in for_admins.json()["items"]] == [str(world.proposed.id)]
    await assert_books_balance(container.uow_factory)


# --- Expired proposals in the lists ------------------------------------------------------


async def test_an_expired_proposal_is_listed_under_expired_and_not_under_proposed(
    container: Container, world: World, admin: AuthenticatedUser
) -> None:
    """The review queue of the admins is the list of PROPOSED: it holds what they can
    still decide on. What nobody decided on in time is found under EXPIRED."""
    queue = await container.markets.list_markets(admin, [MarketStatus.PROPOSED])
    expired = await container.markets.list_markets(admin, [MarketStatus.EXPIRED])
    both = await container.markets.list_markets(
        admin, [MarketStatus.PROPOSED, MarketStatus.EXPIRED]
    )

    assert [(item.id, item.status) for item in queue.items] == [
        (world.proposed.id, MarketStatus.PROPOSED)
    ]
    assert [(item.id, item.status) for item in expired.items] == [
        (world.expired.id, MarketStatus.EXPIRED)
    ]
    assert ids(both) == [world.expired.id, world.proposed.id]
    assert (await market_row(container, world.expired.id)).status is MarketStatus.PROPOSED


async def test_a_proposal_moves_from_the_proposed_list_to_the_expired_one_at_its_deadline(
    container: Container, factory: Factory, clock: FrozenClock, admin: AuthenticatedUser
) -> None:
    """At the instant of `close_at` itself, and without a write: the row says PROPOSED
    before and after."""
    proposal = await factory.market(status=MarketStatus.PROPOSED, close_in=timedelta(hours=1))

    async def lists() -> tuple[list[UUID], list[UUID]]:
        proposed = await container.markets.list_markets(admin, [MarketStatus.PROPOSED])
        expired = await container.markets.list_markets(admin, [MarketStatus.EXPIRED])
        return ids(proposed), ids(expired)

    clock.set(proposal.close_at - TICK)
    assert await lists() == ([proposal.id], [])
    clock.set(proposal.close_at)
    assert await lists() == ([], [proposal.id])
    clock.set(proposal.close_at + 30 * timedelta(days=1))
    assert await lists() == ([], [proposal.id])
    assert (await market_row(container, proposal.id)).status is MarketStatus.PROPOSED


async def test_an_expired_proposal_is_in_no_list_a_user_may_ask_for(
    container: Container, world: World, viewer: AuthenticatedUser
) -> None:
    """Not in the list without a filter, and under no public status."""
    everything = await container.markets.list_markets(viewer, None)
    by_status = [
        await container.markets.list_markets(viewer, [status]) for status in PUBLIC_STATUSES
    ]

    assert world.expired.id not in ids(everything)
    assert all(world.expired.id not in ids(answer) for answer in by_status)
    assert MarketStatus.EXPIRED not in PUBLIC_STATUSES


async def test_the_route_lists_expired_proposals_for_an_admin_and_refuses_everyone_else(
    client: httpx.AsyncClient, as_user: ActAs, factory: Factory, world: World
) -> None:
    """`?status=EXPIRED` is a filter like any other: the admin's list, and 403 for a
    plain user, a barista and the proposer of the expired proposal."""
    proposer = await factory.user()
    own = await factory.market(
        status=MarketStatus.PROPOSED, created_by=proposer, close_in=-timedelta(minutes=1)
    )

    for refused in (await factory.user(), await factory.barista(), proposer):
        as_user(refused)
        response = await client.get(MARKETS, params={"status": "EXPIRED"})
        assert response.status_code == 403
        assert (response.json()["code"], response.json()["details"]) == ("FORBIDDEN", {})

    as_user(await factory.admin())
    expired = await client.get(MARKETS, params={"status": "EXPIRED"})
    queue = await client.get(MARKETS, params={"status": "PROPOSED"})

    assert expired.status_code == 200
    assert [(item["id"], item["status"]) for item in expired.json()["items"]] == [
        (str(world.expired.id), "EXPIRED"),
        (str(own.id), "EXPIRED"),
    ]
    assert [item["trading_open"] for item in expired.json()["items"]] == [False, False]
    assert [item["id"] for item in queue.json()["items"]] == [str(world.proposed.id)]


# --- The list cache ----------------------------------------------------------------------


async def test_a_list_is_served_from_the_cache_until_the_lists_are_dropped(
    container: Container, factory: Factory, viewer: AuthenticatedUser
) -> None:
    """A market written behind the service's back shows what a second request reads:
    not Postgres."""
    first = await factory.market()
    before = await container.markets.list_markets(viewer, None)
    assert await container.market_cache.get(PUBLIC_LIST) == before.model_dump_json()
    second = await factory.market(close_in=timedelta(days=1))

    cached = await container.markets.list_markets(viewer, None)
    await container.market_cache.invalidate_lists()
    after = await container.markets.list_markets(viewer, None)

    assert ids(before) == ids(cached) == [first.id]
    assert cached == before
    assert ids(after) == [second.id, first.id]
    assert await container.market_cache.get(PUBLIC_LIST) == after.model_dump_json()


async def test_one_cached_list_serves_every_viewer_and_every_spelling_of_its_statuses(
    container: Container,
    factory: Factory,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
) -> None:
    """A list holds nothing about who asked, and one set of statuses has one key."""
    first = await factory.market()
    chip = [MarketStatus.OPEN, MarketStatus.HALTED]
    before = await container.markets.list_markets(viewer, chip)
    await factory.market(status=MarketStatus.HALTED)

    for someone, spelled in (
        (admin, chip),
        (viewer, [MarketStatus.HALTED, MarketStatus.OPEN]),
        (admin, [MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.OPEN]),
    ):
        assert await container.markets.list_markets(someone, spelled) == before
    assert ids(before) == [first.id]


async def test_each_set_of_statuses_is_cached_on_its_own_and_all_are_dropped_together(
    container: Container, factory: Factory, viewer: AuthenticatedUser
) -> None:
    halted = await factory.market(status=MarketStatus.HALTED)
    opened = await container.markets.list_markets(viewer, [MarketStatus.OPEN])
    paused = await container.markets.list_markets(viewer, [MarketStatus.HALTED])
    everything = await container.markets.list_markets(viewer, None)
    cache = container.market_cache

    assert (ids(opened), ids(paused), ids(everything)) == ([], [halted.id], [halted.id])
    assert await cache.get(MarketCache.list_key([MarketStatus.OPEN])) == opened.model_dump_json()
    assert await cache.get(MarketCache.list_key([MarketStatus.HALTED])) == paused.model_dump_json()
    assert await cache.get(PUBLIC_LIST) == everything.model_dump_json()

    await cache.invalidate_lists()

    for key in (
        MarketCache.list_key([MarketStatus.OPEN]),
        MarketCache.list_key([MarketStatus.HALTED]),
        PUBLIC_LIST,
    ):
        assert await cache.get(key) is None


@pytest.mark.parametrize(
    "statuses",
    [
        [MarketStatus.PROPOSED],
        [MarketStatus.OPEN, MarketStatus.REJECTED],
        [MarketStatus.EXPIRED],
        [MarketStatus.CLOSED, MarketStatus.EXPIRED],
        list(MarketStatus),
    ],
    ids=[
        "proposed",
        "rejected-with-a-public-status",
        "expired",
        "expired-with-a-public-status",
        "every-status",
    ],
)
async def test_a_list_with_proposed_expired_or_rejected_markets_is_never_cached(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    statuses: list[MarketStatus],
) -> None:
    """Such a list is for admins. Out of a shared cache it could reach somebody else,
    and a proposal just made would be missing from it."""
    hidden = next(status for status in statuses if status in FOR_ADMINS)
    await factory.market()
    before = await container.markets.list_markets(admin, statuses)
    late = await (
        factory.market(status=MarketStatus.PROPOSED, close_in=-timedelta(hours=1))
        if hidden is MarketStatus.EXPIRED
        else factory.market(status=hidden)
    )

    after = await container.markets.list_markets(admin, statuses)

    assert await container.market_cache.get(MarketCache.list_key(statuses)) is None
    assert late.id not in ids(before)
    assert late.id in ids(after)


@pytest.mark.parametrize(
    "left_behind",
    ["", "not json", "[]", '{"items": [{"id": "of-another-version"}]}', '{"items": 7}'],
    ids=["nothing", "no-json", "a-list", "cards-of-another-shape", "no-cards"],
)
async def test_a_cached_entry_that_is_no_list_counts_as_a_miss_and_is_replaced(
    container: Container, factory: Factory, viewer: AuthenticatedUser, left_behind: str
) -> None:
    """Redis decides nothing: an entry another version of the API left behind must not
    fail the list for as long as it lives."""
    market = await factory.market()
    await container.market_cache.set(PUBLIC_LIST, left_behind)

    answer = await container.markets.list_markets(viewer, None)

    assert ids(answer) == [market.id]
    assert await container.market_cache.get(PUBLIC_LIST) == answer.model_dump_json()


async def test_a_change_to_a_market_shows_in_the_lists_at_once_not_a_cache_lifetime_later(
    container: Container, factory: Factory, viewer: AuthenticatedUser, admin: AuthenticatedUser
) -> None:
    """A lifecycle action drops the lists after its commit (A.4)."""
    market = await factory.market()
    chip = [MarketStatus.OPEN]
    assert ids(await container.markets.list_markets(viewer, chip)) == [market.id]
    assert ids(await container.markets.list_markets(viewer, None)) == [market.id]

    await container.market_lifecycle.halt(
        admin, market.id, HaltMarketRequest(reason="The source is in doubt.")
    )

    assert ids(await container.markets.list_markets(viewer, chip)) == []
    (card,) = (await container.markets.list_markets(viewer, None)).items
    assert (card.status, card.trading_open) == (MarketStatus.HALTED, False)


@pytest.mark.redis_down
async def test_without_redis_lists_and_details_come_from_postgres(
    container: Container, factory: Factory
) -> None:
    """SPEC §9: the cache is bypassed, nothing else changes."""
    alice = factory.identity(await factory.user(balance_micro=WALLET))
    market = await factory.market()

    first = await container.markets.list_markets(alice, None)
    late = await factory.market(close_in=timedelta(days=1))
    second = await container.markets.list_markets(alice, None)
    detail = await container.markets.detail(alice, market.id)

    assert (ids(first), ids(second)) == ([market.id], [late.id, market.id])
    assert (detail.id, detail.viewer.balance_cr) == (market.id, to_cr(WALLET))


# --- One market: who may see it ----------------------------------------------------------


async def test_a_market_nobody_knows_is_404(
    container: Container, viewer: AuthenticatedUser, admin: AuthenticatedUser
) -> None:
    for someone in (viewer, admin):
        with pytest.raises(NotFoundError) as refused:
            await container.markets.detail(someone, uuid7())
        assert refused.value.status_code == 404


@pytest.mark.parametrize("status", sorted(HIDDEN_STATUSES))
async def test_a_proposed_or_rejected_market_is_404_to_all_but_its_proposer_and_admins(
    container: Container,
    factory: Factory,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
    status: MarketStatus,
) -> None:
    proposer = await factory.user()
    barista = factory.identity(await factory.barista())
    market = await factory.market(status=status, created_by=proposer)

    for stranger in (viewer, barista):
        with pytest.raises(NotFoundError):
            await container.markets.detail(stranger, market.id)
    own = await container.markets.detail(factory.identity(proposer), market.id)
    seen = await container.markets.detail(admin, market.id)

    assert (own.id, own.status) == (seen.id, seen.status) == (market.id, status)
    assert own.rejection_reason == market.rejection_reason
    assert (own.rejection_reason is not None) == (status is MarketStatus.REJECTED)


async def test_the_detail_reports_a_proposal_as_expired_from_its_deadline_on(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
) -> None:
    """To its proposer and to an admin, from the instant of `close_at` itself. To
    everybody else it stays 404: expiring makes nothing public. The row is not
    touched."""
    proposer = await factory.user()
    proposal = await factory.market(
        status=MarketStatus.PROPOSED, created_by=proposer, close_in=timedelta(hours=1)
    )

    async def reported() -> set[MarketStatus]:
        own = await container.markets.detail(factory.identity(proposer), proposal.id)
        seen = await container.markets.detail(admin, proposal.id)
        with pytest.raises(NotFoundError):
            await container.markets.detail(viewer, proposal.id)
        assert (own.trading_open, own.outcome, own.rejection_reason) == (False, None, None)
        assert (own.viewer.can_trade, own.viewer.cannot_trade_reason) == (
            False,
            TradeBlockReason.MARKET_NOT_OPEN,
        )
        assert (seen.tier, seen.b_cr, seen.max_loss_cr) == (None, NOTHING, NOTHING)
        return {own.status, seen.status}

    clock.set(proposal.close_at - TICK)
    assert await reported() == {MarketStatus.PROPOSED}
    clock.set(proposal.close_at)
    assert await reported() == {MarketStatus.EXPIRED}
    clock.set(proposal.close_at + 90 * timedelta(days=1))
    assert await reported() == {MarketStatus.EXPIRED}
    assert (await market_row(container, proposal.id)).status is MarketStatus.PROPOSED


async def test_the_route_shows_an_expired_proposal_to_its_proposer_and_to_nobody_else(
    client: httpx.AsyncClient, as_user: ActAs, factory: Factory
) -> None:
    proposer = await factory.user()
    expired = await factory.market(
        status=MarketStatus.PROPOSED, created_by=proposer, close_in=-timedelta(seconds=1)
    )

    as_user(proposer)
    own = await client.get(f"{MARKETS}/{expired.id}")
    as_user(await factory.user())
    foreign = await client.get(f"{MARKETS}/{expired.id}")

    assert own.status_code == 200
    assert (own.json()["status"], own.json()["trading_open"]) == ("EXPIRED", False)
    assert foreign.status_code == 404
    assert (foreign.json()["code"], foreign.json()["details"]) == ("NOT_FOUND", {})


@pytest.mark.parametrize("status", PUBLIC_STATUSES)
async def test_every_other_market_is_for_everybody(
    container: Container, factory: Factory, viewer: AuthenticatedUser, status: MarketStatus
) -> None:
    market = await factory.market(status=status)

    assert (await container.markets.detail(viewer, market.id)).status is status


async def test_who_proposed_a_market_is_told_to_admins_only(
    container: Container, factory: Factory, viewer: AuthenticatedUser, admin: AuthenticatedUser
) -> None:
    proposer = await factory.user()
    barista = factory.identity(await factory.barista())
    market = await factory.market(created_by=proposer)

    assert (await container.markets.detail(admin, market.id)).proposer_email == proposer.email
    for someone in (viewer, barista, factory.identity(proposer)):
        assert (await container.markets.detail(someone, market.id)).proposer_email is None


# --- One market: its rules ---------------------------------------------------------------


async def test_a_market_shows_its_wording_and_the_void_rule_before_anyone_trades(
    container: Container, factory: Factory, viewer: AuthenticatedUser
) -> None:
    market = await factory.market(tier=MarketTier.TIGHT)
    view, now = container.market_view, container.clock.now()

    detail = await container.markets.detail(viewer, market.id)

    summary = view.summary(market, now, None)
    assert detail.model_dump(include=set(type(summary).model_fields)) == summary.model_dump()
    assert (
        detail.yes_definition,
        detail.no_definition,
        detail.resolution_criteria,
        detail.resolution_source,
    ) == (market.yes_def, market.no_def, market.resolution_criteria, market.resolution_source)
    assert detail.void_rule == VOID_RULE
    assert (detail.created_at, detail.opened_at, detail.closed_at) == (now, now, None)
    assert (detail.rejection_reason, detail.resolution) == (None, None)


async def test_how_much_liquidity_a_market_has_is_told_to_admins_only(
    container: Container, factory: Factory, viewer: AuthenticatedUser, admin: AuthenticatedUser
) -> None:
    """The tier, b and the loss budget: to an admin by the address and to one by the
    row, and to nobody else, whatever they are to the market."""
    proposer = await factory.user()
    barista = factory.identity(await factory.barista())
    appointed = factory.identity(await factory.user(role=UserRole.ADMIN))
    trader = await factory.user(balance_micro=WALLET)
    market = await factory.market(tier=MarketTier.TIGHT, created_by=proposer)
    await factory.position(trader, market, Outcome.YES, shares_micro=40 * MICRO)
    b_micro = container.settings.pricing.tier_b_micro(MarketTier.TIGHT)

    for one_of_vis in (admin, appointed):
        told = await container.markets.detail(one_of_vis, market.id)
        assert (told.tier, told.b_cr, told.max_loss_cr) == (
            MarketTier.TIGHT,
            to_cr(b_micro),
            to_cr(market.budget_micro),
        )
        assert market.budget_micro == lmsr.max_loss_micro(b_micro, precision=PRECISION)
    for someone in (viewer, barista, factory.identity(proposer), factory.identity(trader)):
        kept = await container.markets.detail(someone, market.id)
        assert (kept.tier, kept.b_cr, kept.max_loss_cr) == (None, None, None)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("status", sorted(HIDDEN_STATUSES))
async def test_a_market_that_was_never_opened_has_no_liquidity_to_tell_an_admin_of(
    container: Container,
    factory: Factory,
    admin: AuthenticatedUser,
    status: MarketStatus,
) -> None:
    """To an admin: no tier yet, and a b and a budget of nothing. To its proposer the
    three are as blank as for any market."""
    proposer = await factory.user()
    market = await factory.market(status=status, created_by=proposer)

    for_the_admin = await container.markets.detail(admin, market.id)
    own = await container.markets.detail(factory.identity(proposer), market.id)

    assert (for_the_admin.tier, for_the_admin.b_cr, for_the_admin.max_loss_cr) == (
        None,
        NOTHING,
        NOTHING,
    )
    assert (own.tier, own.b_cr, own.max_loss_cr) == (None, None, None)


async def sold_out_of_at_a_gain(container: Container, factory: Factory) -> tuple[Market, int]:
    """A market out of which somebody took more than they put in: Alice buys, Bob's
    buy moves the price up, Alice sells at a gain. Returns it with that gain, which is
    what a void would cost VIS: Bob gets his whole stake back, and the account holds
    that minus what Alice took."""
    alice = await factory.user(balance_micro=WALLET)
    bob = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    alice_held = await factory.position(alice, market, Outcome.YES, shares_micro=150 * MICRO)
    await factory.position(bob, market, Outcome.YES, shares_micro=150 * MICRO)
    received_micro = await sell(container, alice, market, Outcome.YES, 150 * MICRO)
    gain_micro = received_micro - alice_held.cost_micro
    assert gain_micro > 0
    return market, gain_micro


async def test_what_voiding_a_market_would_cost_is_told_to_admins_only(
    container: Container, factory: Factory, viewer: AuthenticatedUser, admin: AuthenticatedUser
) -> None:
    """To an admin by the address and to one by the row, and to nobody else: not to a
    trader of the market, not to a barista."""
    market, gain_micro = await sold_out_of_at_a_gain(container, factory)
    barista = factory.identity(await factory.barista())
    appointed = factory.identity(await factory.user(role=UserRole.ADMIN))
    async with container.uow_factory() as uow:
        traders = {held.user_id for held in await uow.positions.list_for_market(market.id)}
        a_trader = await uow.users.get(next(iter(traders)))
    assert a_trader is not None

    for one_of_vis in (admin, appointed):
        told = await container.markets.detail(one_of_vis, market.id)
        assert told.void_cost_cr == to_cr(gain_micro)
    for someone in (viewer, barista, factory.identity(a_trader)):
        kept = await container.markets.detail(someone, market.id)
        assert kept.void_cost_cr is None
    await assert_books_balance(container.uow_factory)


async def test_the_void_cost_of_a_market_is_the_one_its_row_on_the_risk_page_shows(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    """One function gives both (`MarketView.void_cost_micro`), from the same two
    readings: the page an admin voids a market from says what the risk page says."""
    market, gain_micro = await sold_out_of_at_a_gain(container, factory)
    untraded = await factory.market()
    held = await factory.market()
    await factory.position(
        await factory.user(balance_micro=WALLET), held, Outcome.NO, shares_micro=40 * MICRO
    )

    page = await container.risk.snapshot()

    on_the_risk_page = {row.id: row.void_cost_cr for row in page.markets}
    for listed in (market, untraded, held):
        detail = await container.markets.detail(admin, listed.id)
        assert detail.void_cost_cr == on_the_risk_page[listed.id]
    assert on_the_risk_page == {
        market.id: to_cr(gain_micro),
        untraded.id: NOTHING,
        held.id: NOTHING,
    }
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("status", sorted(UNSETTLED_STATUSES))
async def test_a_market_that_is_not_settled_tells_an_admin_its_void_cost_also_when_it_is_nothing(
    container: Container, factory: Factory, admin: AuthenticatedUser, status: MarketStatus
) -> None:
    """Open, halted or closed: such a market can be voided, and the risk page lists it.
    Nothing is not the same as no figure."""
    market = await factory.market(status=status)

    detail = await container.markets.detail(admin, market.id)

    assert detail.void_cost_cr == NOTHING
    assert detail.void_cost_cr is not None


@pytest.mark.parametrize("status", sorted(HIDDEN_STATUSES | SETTLED_STATUSES))
async def test_a_market_with_nothing_to_void_tells_an_admin_no_void_cost(
    container: Container, factory: Factory, admin: AuthenticatedUser, status: MarketStatus
) -> None:
    """One that was never opened has no account and nobody's stake, and one that is
    settled has paid out. The risk page lists neither."""
    market = await factory.market(status=status)

    detail = await container.markets.detail(admin, market.id)

    assert detail.void_cost_cr is None
    assert market.id not in {row.id for row in (await container.risk.snapshot()).markets}


async def test_an_expired_proposal_tells_an_admin_no_void_cost(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    """Its row says PROPOSED: it was never opened."""
    expired = await factory.market(status=MarketStatus.PROPOSED, close_in=-timedelta(days=1))

    detail = await container.markets.detail(admin, expired.id)

    assert (detail.status, detail.void_cost_cr) == (MarketStatus.EXPIRED, None)


async def test_a_voided_market_cost_what_its_detail_said_beforehand_and_says_nothing_after(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    """The figure an admin read before deciding is what `vis:mm_pnl` then added."""
    market, gain_micro = await sold_out_of_at_a_gain(container, factory)
    before = await container.markets.detail(admin, market.id)
    async with container.uow_factory() as uow:
        pnl_before = (await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)).balance_micro

    await container.market_lifecycle.close(admin, market.id, CloseMarketRequest())
    closed = await container.markets.detail(admin, market.id)
    voided = await container.market_lifecycle.void(
        admin, market.id, VoidMarketRequest(reason="The mensa was closed that day.")
    )

    async with container.uow_factory() as uow:
        pnl_after = (await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)).balance_micro
    assert before.void_cost_cr == closed.void_cost_cr == to_cr(gain_micro)
    assert pnl_before - pnl_after == gain_micro
    assert voided.void_cost_cr is None
    await assert_books_balance(container.uow_factory)


async def test_a_market_that_can_be_voided_and_has_no_account_is_a_broken_book_to_an_admin(
    container: Container,
    factory: Factory,
    viewer: AuthenticatedUser,
    admin: AuthenticatedUser,
    owner_engine: AsyncEngine,
) -> None:
    """Approval opens the account with the market. The page fails loudly for whoever
    is shown the figure, as the risk page does, and nobody else is read it for."""
    market = await factory.market()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM accounts WHERE market_id = :id"), {"id": market.id}
        )

    with pytest.raises(LookupError, match="has no account"):
        await container.markets.detail(admin, market.id)
    assert (await container.markets.detail(viewer, market.id)).void_cost_cr is None


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (MarketStatus.RESOLVED, ResolutionOutcome.YES),
        (MarketStatus.RESOLVED, ResolutionOutcome.NO),
        (MarketStatus.VOID, ResolutionOutcome.VOID),
    ],
)
async def test_a_settled_market_shows_how_it_ended(
    container: Container,
    factory: Factory,
    viewer: AuthenticatedUser,
    status: MarketStatus,
    outcome: ResolutionOutcome,
) -> None:
    market = await factory.market(status=status, outcome=outcome)
    resolution = await resolution_of(container, market)
    assert resolution is not None

    detail = await container.markets.detail(viewer, market.id)

    assert detail.outcome is outcome
    assert detail.resolution is not None
    assert (
        detail.resolution.outcome,
        detail.resolution.evidence,
        detail.resolution.resolved_at,
    ) == (outcome, resolution.evidence, resolution.created_at)
    assert detail.closed_at == market.closed_at


# --- One market: the viewer's side -------------------------------------------------------


async def test_the_viewer_block_is_what_market_view_makes_of_their_account_and_positions(
    container: Container, factory: Factory
) -> None:
    """Balance, holdings at the current price, everything paid and received, the net
    stake and what the stake limit leaves."""
    alice = await factory.user(balance_micro=WALLET)
    bob = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=60 * MICRO)
    await factory.position(alice, market, Outcome.NO, shares_micro=20 * MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=90 * MICRO)
    received_micro = await sell(container, alice, market, Outcome.YES, 25 * MICRO)
    view = container.market_view

    detail = await container.markets.detail(factory.identity(alice), market.id)

    rows = await positions_of(container, alice, market)
    paid_micro = sum(row.cost_micro for row in rows)
    block = detail.viewer
    assert block.balance_cr == to_cr(await factory.balance_micro(alice))
    assert block.balance_cr == to_cr(WALLET - paid_micro + received_micro)
    assert block.holdings == view.holdings(market, rows, None)
    assert [(held.outcome, held.shares) for held in block.holdings] == [
        (Outcome.YES, to_cr(35 * MICRO)),
        (Outcome.NO, to_cr(20 * MICRO)),
    ]
    assert (block.cost_cr, block.proceeds_cr) == (to_cr(paid_micro), to_cr(received_micro))
    assert received_micro > 0
    assert block.net_stake_cr == to_cr(view.net_stake_micro(rows))
    assert block.stake_limit_cr == to_cr(view.stake_limit_micro) == to_cr(STAKE_LIMIT)
    assert block.stake_left_cr == to_cr(view.stake_left_micro(view.net_stake_micro(rows)))
    assert 0 < block.stake_left_cr < block.stake_limit_cr
    assert (block.payout_cr, block.refund_cr) == (None, None)
    # Somebody else's trades are not the viewer's: bob moved the price, not the stake.
    assert detail.p_yes == view.probabilities(view.p_yes_micro(market))[0]
    await assert_books_balance(container.uow_factory)


async def test_somebody_who_never_traded_a_market_sees_an_empty_side_and_the_whole_limit(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WALLET)
    trader = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(trader, market, Outcome.YES, shares_micro=60 * MICRO)

    block = (await container.markets.detail(factory.identity(alice), market.id)).viewer

    assert [(held.outcome, held.shares, held.value_cr) for held in block.holdings] == [
        (Outcome.YES, NOTHING, NOTHING),
        (Outcome.NO, NOTHING, NOTHING),
    ]
    assert (block.cost_cr, block.proceeds_cr, block.net_stake_cr) == (NOTHING, NOTHING, NOTHING)
    assert block.stake_left_cr == block.stake_limit_cr == to_cr(STAKE_LIMIT)
    assert block.balance_cr == to_cr(WALLET)
    await assert_books_balance(container.uow_factory)


async def test_a_detail_is_read_from_postgres_every_time(
    container: Container, factory: Factory
) -> None:
    """It carries its viewer's side, so no answer is anybody else's and none is cached:
    a trade shows in the next one."""
    alice = await factory.user(balance_micro=WALLET)
    viewer = factory.identity(alice)
    market = await factory.market()
    await container.markets.list_markets(viewer, None)
    before = await container.markets.detail(viewer, market.id)

    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    after = await container.markets.detail(viewer, market.id)

    assert (before.version, after.version) == (0, 1)
    assert before.viewer.cost_cr == NOTHING < after.viewer.cost_cr
    assert after.viewer.balance_cr == before.viewer.balance_cr - after.viewer.cost_cr
    assert after.p_yes > before.p_yes
    await assert_books_balance(container.uow_factory)


# --- One market: the buy buttons ---------------------------------------------------------


def allowed_stakes(detail: MarketDetailResponse) -> list[bool]:
    assert [preset.amount_cr for preset in detail.viewer.stake_presets] == [
        to_cr(preset) for preset in PRESETS
    ]
    return [preset.allowed for preset in detail.viewer.stake_presets]


async def test_every_stake_preset_is_allowed_to_a_trader_with_the_tokens_and_the_whole_limit(
    container: Container, factory: Factory
) -> None:
    alice = factory.identity(await factory.user(balance_micro=WALLET))
    market = await factory.market()

    detail = await container.markets.detail(alice, market.id)

    assert allowed_stakes(detail) == [preset <= STAKE_LIMIT for preset in PRESETS]
    assert any(allowed_stakes(detail))


async def test_a_stake_preset_the_balance_does_not_cover_is_not_allowed(
    container: Container, factory: Factory
) -> None:
    """A balance of exactly a preset covers it and no larger one."""
    covered = PRESETS[len(PRESETS) // 2]
    alice = factory.identity(await factory.user(balance_micro=covered))
    broke = factory.identity(await factory.user())
    market = await factory.market()

    assert allowed_stakes(await container.markets.detail(alice, market.id)) == [
        preset <= covered for preset in PRESETS
    ]
    assert allowed_stakes(await container.markets.detail(broke, market.id)) == [False] * len(
        PRESETS
    )


async def test_a_stake_preset_above_what_the_stake_limit_leaves_is_not_allowed(
    container: Container, factory: Factory
) -> None:
    """With exactly the smallest preset left, that one is allowed; one micro-credit less
    left and none is."""
    smallest = min(PRESETS)
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(
        alice, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=STAKE_LIMIT - smallest
    )

    exactly = await container.markets.detail(factory.identity(alice), market.id)
    await factory.position(alice, market, Outcome.NO, shares_micro=1, cost_micro=1)
    short = await container.markets.detail(factory.identity(alice), market.id)

    assert exactly.viewer.stake_left_cr == to_cr(smallest)
    assert allowed_stakes(exactly) == [preset <= smallest for preset in PRESETS]
    assert short.viewer.stake_left_cr == to_cr(smallest - 1)
    assert allowed_stakes(short) == [False] * len(PRESETS)
    assert short.viewer.stake_presets == container.market_view.stake_presets(
        smallest - 1, await factory.balance_micro(alice), True
    )
    await assert_books_balance(container.uow_factory)


async def test_selling_gives_stake_back_and_buttons_with_it(
    container: Container, factory: Factory
) -> None:
    """SPEC §4: selling lowers the net stake, so a user can buy again up to the limit."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(
        alice, market, Outcome.YES, shares_micro=150 * MICRO, cost_micro=STAKE_LIMIT
    )
    at_the_limit = await container.markets.detail(factory.identity(alice), market.id)

    received_micro = await sell(container, alice, market, Outcome.YES, 150 * MICRO)
    after = await container.markets.detail(factory.identity(alice), market.id)

    assert at_the_limit.viewer.stake_left_cr == NOTHING
    assert allowed_stakes(at_the_limit) == [False] * len(PRESETS)
    assert after.viewer.stake_left_cr == to_cr(received_micro)
    assert allowed_stakes(after) == [preset <= received_micro for preset in PRESETS]
    assert any(allowed_stakes(after))
    await assert_books_balance(container.uow_factory)


# --- One market: the slider of a buy -----------------------------------------------------


def slider_of(detail: MarketDetailResponse) -> tuple[int, int, int] | None:
    """`(min, max, step)` of the viewer's slider, in whole credits; None for none."""
    slider = detail.viewer.buy_slider
    return None if slider is None else (slider.min, slider.max, slider.step)


async def test_the_buy_slider_runs_from_the_smallest_buy_to_the_stake_limit_for_a_fresh_trader(
    container: Container, factory: Factory
) -> None:
    """In whole credits, a step of TRADE_STAKE_STEP_CR apart, and it is `MarketView`'s."""
    alice = factory.identity(await factory.user(balance_micro=WALLET))
    market = await factory.market()

    detail = await container.markets.detail(alice, market.id)

    assert (STAKE_STEP, STAKE_LIMIT % STAKE_STEP) == (MICRO, 0), "the settings step by one credit"
    assert BASE.pricing.min_trade_micro == MICRO
    assert slider_of(detail) == (1, STAKE_LIMIT // MICRO, 1)
    assert detail.viewer.buy_slider == container.market_view.buy_slider(STAKE_LIMIT, WALLET, True)


async def test_the_buy_slider_ends_where_the_balance_does(
    container: Container, factory: Factory
) -> None:
    """Seventeen and a half credits buy for seventeen; less than the smallest buy, and
    there is no slider."""
    with_some = factory.identity(await factory.user(balance_micro=17 * MICRO + MICRO // 2))
    with_one = factory.identity(await factory.user(balance_micro=MICRO))
    nearly_one = factory.identity(await factory.user(balance_micro=MICRO - 1))
    broke = factory.identity(await factory.user())
    market = await factory.market()

    assert slider_of(await container.markets.detail(with_some, market.id)) == (1, 17, 1)
    assert slider_of(await container.markets.detail(with_one, market.id)) == (1, 1, 1)
    assert slider_of(await container.markets.detail(nearly_one, market.id)) is None
    assert slider_of(await container.markets.detail(broke, market.id)) is None


async def test_the_buy_slider_ends_where_the_stake_limit_does_and_selling_moves_it_back(
    container: Container, factory: Factory
) -> None:
    """With 30.25 credits of the limit left the slider ends at 30, at the limit there is
    none, and what a sale brings back can be staked again, down to a whole credit."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    left = 30 * MICRO + MICRO // 4
    await factory.position(
        alice, market, Outcome.YES, shares_micro=120 * MICRO, cost_micro=STAKE_LIMIT - left
    )
    with_some_left = await container.markets.detail(factory.identity(alice), market.id)
    await factory.position(alice, market, Outcome.NO, shares_micro=30 * MICRO, cost_micro=left)
    at_the_limit = await container.markets.detail(factory.identity(alice), market.id)

    received_micro = await sell(container, alice, market, Outcome.YES, 120 * MICRO)
    after = await container.markets.detail(factory.identity(alice), market.id)

    assert with_some_left.viewer.stake_left_cr == to_cr(left)
    assert slider_of(with_some_left) == (1, 30, 1)
    assert (at_the_limit.viewer.stake_left_cr, slider_of(at_the_limit)) == (NOTHING, None)
    assert after.viewer.stake_left_cr == to_cr(received_micro)
    assert received_micro % MICRO, "what the sale brought back is no whole number of credits"
    assert slider_of(after) == (1, received_micro // MICRO, 1)
    await assert_books_balance(container.uow_factory)


# --- One market: the sell buttons --------------------------------------------------------


async def test_the_sell_presets_are_half_and_all_of_each_holding_yes_first(
    container: Container, factory: Factory
) -> None:
    """Half is rounded down to a millionth of a share, so the client halves nothing."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    odd_micro = 30 * MICRO + 1
    await factory.position(alice, market, Outcome.YES, shares_micro=odd_micro)

    detail = await container.markets.detail(factory.identity(alice), market.id)

    presets = detail.viewer.sell_presets
    assert [(preset.outcome, preset.portion) for preset in presets] == [
        (Outcome.YES, SellPortion.HALF),
        (Outcome.YES, SellPortion.ALL),
        (Outcome.NO, SellPortion.HALF),
        (Outcome.NO, SellPortion.ALL),
    ]
    assert [preset.shares for preset in presets] == [
        to_cr(15 * MICRO),
        to_cr(odd_micro),
        NOTHING,
        NOTHING,
    ]
    assert [preset.allowed for preset in presets] == [True, True, False, False]
    assert presets == container.market_view.sell_presets(
        await positions_of(container, alice, market), True
    )
    await assert_books_balance(container.uow_factory)


async def test_a_sell_preset_below_the_smallest_trade_is_not_allowed(
    container: Container, factory: Factory
) -> None:
    """Of a holding of one and a half smallest trades, all of it can be sold and half
    of it cannot."""
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    holding_micro = MIN_SHARES + MIN_SHARES // 2
    await factory.position(alice, market, Outcome.NO, shares_micro=holding_micro)

    detail = await container.markets.detail(factory.identity(alice), market.id)

    half, everything = detail.viewer.sell_presets[2:]
    assert (half.outcome, half.shares, half.allowed) == (
        Outcome.NO,
        to_cr(holding_micro // 2),
        False,
    )
    assert (everything.shares, everything.allowed) == (to_cr(holding_micro), True)
    await assert_books_balance(container.uow_factory)


# --- One market: may the viewer trade ----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "closes_in", "own", "reason"),
    [
        (MarketStatus.OPEN, timedelta(days=7), False, None),
        (MarketStatus.OPEN, timedelta(days=7), True, TradeBlockReason.OWN_MARKET),
        (MarketStatus.OPEN, -TICK, False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.OPEN, timedelta(0), False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.OPEN, -TICK, True, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.HALTED, timedelta(days=7), False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.HALTED, timedelta(days=7), True, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.CLOSED, timedelta(days=7), False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.RESOLVED, timedelta(days=7), False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.VOID, timedelta(days=7), False, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.PROPOSED, timedelta(days=7), True, TradeBlockReason.MARKET_NOT_OPEN),
        (MarketStatus.REJECTED, timedelta(days=7), True, TradeBlockReason.MARKET_NOT_OPEN),
    ],
    ids=[
        "open",
        "open-and-the-viewer-proposed-it",
        "open-a-moment-past-its-deadline",
        "open-at-its-deadline",
        "past-its-deadline-and-the-viewers-own",
        "halted",
        "halted-and-the-viewers-own",
        "closed",
        "resolved",
        "void",
        "the-viewers-proposal",
        "the-viewers-rejected-proposal",
    ],
)
async def test_whether_the_viewer_may_trade_and_the_first_reason_why_not(
    container: Container,
    factory: Factory,
    status: MarketStatus,
    closes_in: timedelta,
    own: bool,
    reason: TradeBlockReason | None,
) -> None:
    """The market's status is asked before whose market it is. Nothing is offered to
    somebody who may not trade, whatever they hold and have."""
    alice = await factory.user(balance_micro=WALLET)
    proposer = alice if own else await factory.user()
    market = await factory.market(status=status, close_in=closes_in, created_by=proposer)

    block = (await container.markets.detail(factory.identity(alice), market.id)).viewer

    assert (block.can_trade, block.cannot_trade_reason) == (reason is None, reason)
    if reason is None:
        assert block.buy_slider is not None
    else:
        assert not any(preset.allowed for preset in block.stake_presets)
        assert not any(preset.allowed for preset in block.sell_presets)
        assert block.buy_slider is None


async def test_a_halted_market_offers_no_button_to_somebody_with_shares_tokens_and_stake_left(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    trading = await container.markets.detail(factory.identity(alice), market.id)

    await container.market_lifecycle.halt(
        admin, market.id, HaltMarketRequest(reason="The source is in doubt.")
    )
    paused = await container.markets.detail(factory.identity(alice), market.id)

    assert any(preset.allowed for preset in trading.viewer.stake_presets)
    assert trading.viewer.buy_slider is not None
    assert [preset.allowed for preset in trading.viewer.sell_presets[:2]] == [True, True]
    assert paused.viewer.cannot_trade_reason is TradeBlockReason.MARKET_NOT_OPEN
    assert not any(preset.allowed for preset in paused.viewer.stake_presets)
    assert paused.viewer.buy_slider is None
    assert not any(preset.allowed for preset in paused.viewer.sell_presets)
    # What is offered changed; what the viewer holds did not.
    assert [preset.shares for preset in paused.viewer.sell_presets] == [
        preset.shares for preset in trading.viewer.sell_presets
    ]
    assert paused.viewer.stake_left_cr == trading.viewer.stake_left_cr
    await assert_books_balance(container.uow_factory)


# --- One market: what it paid and gave back ----------------------------------------------


@pytest.mark.parametrize("outcome", list(Outcome))
async def test_a_resolved_market_shows_each_viewer_the_shares_they_held_of_the_winning_outcome(
    container: Container, factory: Factory, admin: AuthenticatedUser, outcome: Outcome
) -> None:
    winner = await factory.user(balance_micro=WALLET)
    loser = await factory.user(balance_micro=WALLET)
    both = await factory.user(balance_micro=WALLET)
    onlooker = await factory.user()
    market = await factory.market(status=MarketStatus.CLOSED)
    await factory.position(winner, market, outcome, shares_micro=70 * MICRO)
    await factory.position(loser, market, outcome.opposite, shares_micro=40 * MICRO)
    await factory.position(both, market, outcome, shares_micro=10 * MICRO)
    await factory.position(both, market, outcome.opposite, shares_micro=25 * MICRO)
    unsettled = await container.markets.detail(factory.identity(winner), market.id)
    held_before_micro = await factory.balance_micro(winner)

    await container.market_lifecycle.resolve(
        admin, market.id, ResolveMarketRequest(outcome=outcome, evidence="The menu shows it.")
    )

    view = container.market_view
    resolution = await resolution_of(container, market)
    expected_micro = {winner.id: 70 * MICRO, loser.id: 0, both.id: 10 * MICRO, onlooker.id: 0}
    for user in (winner, loser, both, onlooker):
        block = (await container.markets.detail(factory.identity(user), market.id)).viewer
        rows = await positions_of(container, user, market)
        assert block.payout_cr == to_cr(expected_micro[user.id])
        assert view.payout_micro(rows, resolution) == expected_micro[user.id]
        assert block.refund_cr is None
        # A share of the winning outcome is worth one credit, the other nothing.
        assert sum(held.value_cr for held in block.holdings) == block.payout_cr
        assert block.can_trade is False
    paid = (await container.markets.detail(factory.identity(winner), market.id)).viewer
    assert (unsettled.viewer.payout_cr, unsettled.viewer.refund_cr) == (None, None)
    assert paid.balance_cr == to_cr(held_before_micro + 70 * MICRO)
    await assert_books_balance(container.uow_factory)


async def test_a_void_market_shows_each_viewer_the_net_stake_they_got_back(
    container: Container, factory: Factory, admin: AuthenticatedUser
) -> None:
    """Their shares are gone; cost and proceeds stay, and the refund is read off them."""
    holder = await factory.user(balance_micro=WALLET)
    seller = await factory.user(balance_micro=WALLET)
    onlooker = await factory.user()
    market = await factory.market(status=MarketStatus.CLOSED)
    await factory.position(holder, market, Outcome.YES, shares_micro=70 * MICRO)
    await factory.position(seller, market, Outcome.NO, shares_micro=40 * MICRO)
    await sell(container, seller, market, Outcome.NO, 15 * MICRO)
    view = container.market_view
    stakes_micro = {
        user.id: view.net_stake_micro(await positions_of(container, user, market))
        for user in (holder, seller, onlooker)
    }
    balances_micro = {
        user.id: await factory.balance_micro(user) for user in (holder, seller, onlooker)
    }

    await container.market_lifecycle.void(
        admin, market.id, VoidMarketRequest(reason="The source went away.")
    )

    resolution = await resolution_of(container, market)
    for user in (holder, seller, onlooker):
        block = (await container.markets.detail(factory.identity(user), market.id)).viewer
        rows = await positions_of(container, user, market)
        assert block.refund_cr == to_cr(stakes_micro[user.id])
        assert view.refund_micro(rows, resolution) == stakes_micro[user.id]
        assert block.payout_cr is None
        assert [(held.shares, held.value_cr) for held in block.holdings] == 2 * [(NOTHING, NOTHING)]
        assert block.net_stake_cr == block.refund_cr
        assert block.balance_cr == to_cr(balances_micro[user.id] + stakes_micro[user.id])
    assert stakes_micro[holder.id] > stakes_micro[seller.id] > stakes_micro[onlooker.id] == 0
    await assert_books_balance(container.uow_factory)


# --- One market: may the viewer settle it ------------------------------------------------


@pytest.mark.parametrize(
    "status", [MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED, MarketStatus.RESOLVED]
)
async def test_an_admin_who_never_traded_a_market_may_settle_it_whatever_its_status(
    container: Container, factory: Factory, admin: AuthenticatedUser, status: MarketStatus
) -> None:
    """`can_settle` says who, not when: which action a status allows is SPEC §6."""
    market = await factory.market(status=status)

    assert (await container.markets.detail(admin, market.id)).viewer.can_settle is True


async def test_an_admin_who_traded_a_market_may_not_settle_it_even_after_selling_everything(
    container: Container, factory: Factory
) -> None:
    """A.4: whoever ever traded it, whatever they hold now. Another admin may."""
    first = await factory.admin(0, balance_micro=WALLET)
    second = await factory.admin(1)
    market = await factory.market()
    untouched = await factory.market()
    await factory.position(first, market, Outcome.YES, shares_micro=40 * MICRO)
    holding = await container.markets.detail(factory.identity(first), market.id)

    await sell(container, first, market, Outcome.YES, 40 * MICRO)
    sold_out = await container.markets.detail(factory.identity(first), market.id)

    assert (holding.viewer.can_settle, sold_out.viewer.can_settle) == (False, False)
    assert [held.shares for held in sold_out.viewer.holdings] == [NOTHING, NOTHING]
    assert (await container.markets.detail(factory.identity(second), market.id)).viewer.can_settle
    assert (await container.markets.detail(factory.identity(first), untouched.id)).viewer.can_settle
    await assert_books_balance(container.uow_factory)


async def test_nobody_but_an_admin_may_settle_a_market(
    container: Container, factory: Factory
) -> None:
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    plain = factory.identity(await factory.user())
    barista = factory.identity(await factory.barista())
    suspended = await factory.user(status=UserStatus.SUSPENDED)

    for someone in (
        plain,
        barista,
        factory.identity(proposer),
        identity_of(suspended, UserRole.ADMIN),
    ):
        assert (await container.markets.detail(someone, market.id)).viewer.can_settle is False


# --- One market: through the route -------------------------------------------------------


async def test_the_route_answers_a_market_with_the_viewers_side_and_amounts_as_strings(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WALLET)
    market = await factory.market()
    paid_micro = 30 * MICRO
    await factory.position(
        alice, market, Outcome.YES, shares_micro=50 * MICRO, cost_micro=paid_micro
    )
    as_user(alice)

    response = await client.get(f"{MARKETS}/{market.id}")

    assert response.status_code == 200
    body = response.json()
    assert MarketDetailResponse.model_validate(body) == await container.markets.detail(
        factory.identity(alice), market.id
    )
    assert (body["viewer"]["cost_cr"], body["viewer"]["proceeds_cr"]) == ("30", "0")
    assert body["viewer"]["balance_cr"] == format_cr(WALLET - paid_micro)
    assert body["viewer"]["stake_left_cr"] == format_cr(STAKE_LIMIT - paid_micro)
    assert body["viewer"]["holdings"][0]["shares"] == "50"
    assert body["viewer"]["sell_presets"][0] == {
        "outcome": "YES",
        "portion": "HALF",
        "shares": "25",
        "allowed": True,
    }
    assert (body["viewer"]["payout_cr"], body["viewer"]["refund_cr"]) == (None, None)
    # Whole credits as JSON integers: the stake that is left ends the slider.
    assert body["viewer"]["buy_slider"] == {
        "min": 1,
        "max": (STAKE_LIMIT - paid_micro) // MICRO,
        "step": 1,
    }
    assert all(type(value) is int for value in body["viewer"]["buy_slider"].values())
    assert body["proposer_email"] is None
    # Nothing about its liquidity reaches somebody who is no admin.
    assert (body["tier"], body["b_cr"], body["max_loss_cr"]) == (None, None, None)
    assert "requested_tier" not in body
    assert body["void_rule"] == VOID_RULE
    await assert_books_balance(container.uow_factory)


async def test_the_route_tells_an_admin_the_liquidity_of_a_market_as_strings(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    market = await factory.market(tier=MarketTier.MEDIUM)
    as_user(await factory.admin())

    body = (await client.get(f"{MARKETS}/{market.id}")).json()

    assert (body["tier"], body["b_cr"], body["max_loss_cr"]) == (
        "MEDIUM",
        format_cr(container.settings.pricing.tier_b_micro(MarketTier.MEDIUM)),
        format_cr(market.budget_micro),
    )
    assert "requested_tier" not in body


async def test_the_route_tells_an_admin_the_void_cost_of_a_market_as_the_risk_route_does(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    """A signed decimal string to an admin, the one `GET /admin/risk` carries in the
    market's row, and null to everybody else."""
    market, gain_micro = await sold_out_of_at_a_gain(container, factory)
    as_user(await factory.admin())

    for_the_admin = (await client.get(f"{MARKETS}/{market.id}")).json()
    risk = (await client.get("/api/admin/risk")).json()
    as_user(await factory.user())
    for_a_user = (await client.get(f"{MARKETS}/{market.id}")).json()

    (row,) = risk["markets"]
    assert for_the_admin["void_cost_cr"] == row["void_cost_cr"] == format_cr(gain_micro)
    assert isinstance(for_the_admin["void_cost_cr"], str)
    assert "void_cost_cr" in for_a_user
    assert for_a_user["void_cost_cr"] is None


async def test_the_route_answers_404_for_an_unknown_market_and_for_somebody_elses_proposal(
    client: httpx.AsyncClient, as_user: ActAs, factory: Factory
) -> None:
    proposer = await factory.user()
    hidden = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    as_user(await factory.user())

    for market_id in (uuid7(), hidden.id):
        response = await client.get(f"{MARKETS}/{market_id}")
        assert response.status_code == 404
        assert (response.json()["code"], response.json()["details"]) == ("NOT_FOUND", {})

    as_user(proposer)
    assert (await client.get(f"{MARKETS}/{hidden.id}")).status_code == 200


# --- Whatever was traded -----------------------------------------------------------------

STEP = st.tuples(
    st.sampled_from(Outcome),
    st.booleans(),
    st.integers(min_value=1, max_value=60 * MICRO),
)
"""One step of a trader: the outcome, whether to sell if they hold any of it, and how
many micro-shares."""


@hypothesis_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    steps=st.lists(STEP, max_size=5),
    wallet_micro=st.integers(min_value=0, max_value=3 * STAKE_LIMIT),
)
async def test_whatever_a_viewer_traded_their_side_is_what_market_view_makes_of_the_rows(
    container: Container,
    factory: Factory,
    steps: list[tuple[Outcome, bool, int]],
    wallet_micro: int,
) -> None:
    """For any run of buys and sells and any balance: the block is `MarketView` on the
    viewer's own rows, a stake preset marked allowed is one the balance and the stake
    limit both cover, and a sell preset never offers more than is held.

    Every example has a trader and a market of its own. The factory applies no stake
    limit, so the net stake may lie beyond it, where nothing is left.
    """
    alice = await factory.user(balance_micro=wallet_micro)
    market = await factory.market()
    for outcome, would_sell, shares_micro in steps:
        held = {
            row.outcome: row.shares_micro for row in await positions_of(container, alice, market)
        }
        if would_sell and held.get(outcome):
            await sell(container, alice, market, outcome, min(shares_micro, held[outcome]))
        elif shares_micro <= await factory.balance_micro(alice):
            await factory.position(alice, market, outcome, shares_micro=shares_micro)
    view = container.market_view

    detail = await container.markets.detail(factory.identity(alice), market.id)

    rows = await positions_of(container, alice, market)
    balance_micro = await factory.balance_micro(alice)
    stake_micro = view.net_stake_micro(rows)
    block = detail.viewer
    assert block.balance_cr == to_cr(balance_micro)
    assert block.holdings == view.holdings(market, rows, None)
    assert block.cost_cr == to_cr(sum(row.cost_micro for row in rows))
    assert block.proceeds_cr == to_cr(sum(row.proceeds_micro for row in rows))
    assert block.net_stake_cr == to_cr(stake_micro)
    assert block.stake_left_cr == to_cr(view.stake_left_micro(stake_micro))
    assert block.stake_presets == view.stake_presets(
        view.stake_left_micro(stake_micro), balance_micro, True
    )
    assert block.buy_slider == view.buy_slider(
        view.stake_left_micro(stake_micro), balance_micro, True
    )
    assert block.sell_presets == view.sell_presets(rows, True)
    assert (block.can_trade, block.can_settle) == (True, False)
    for stake in block.stake_presets:
        if stake.allowed:
            assert stake.amount_cr <= block.balance_cr
            assert stake.amount_cr <= block.stake_left_cr
    if block.buy_slider is not None:
        assert 0 < block.buy_slider.min <= block.buy_slider.max
        assert block.buy_slider.max <= block.balance_cr
        assert block.buy_slider.max <= block.stake_left_cr
    shares = {held.outcome: held.shares for held in block.holdings}
    for offer in block.sell_presets:
        assert offer.shares <= shares[offer.outcome]
        assert offer.shares > 0 or not offer.allowed
    assert detail.version == market.version
    await assert_books_balance(container.uow_factory)
