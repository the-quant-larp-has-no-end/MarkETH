"""The demo seed, run on a real Postgres and Redis (`scripts/seed.py`, `make seed`).

What it has to be: demo data the application could have written, made through the
services alone; never run in production and never for a bootstrap admin; and safe to
run again, whether the run before it finished or stopped half way.

`tests/unit/test_seed_plan.py` checks the plan against the rules without a database.
Here the plan runs. What a market paid or gave back is what `MarketView` makes of the
stored rows, and every trade is priced again by `app.pricing.lmsr` (`replay`).
"""

import asyncio
import os
import re
import sys
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID, uuid5

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings
from app.core.degrees import is_programme
from app.core.enums import (
    AccountKind,
    CodeStatus,
    JournalKind,
    MarketStatus,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
    VoucherStatus,
)
from app.core.errors import UsernameTakenError
from app.core.identity import PortalIdentity
from app.core.security import hash_code, normalize_code
from app.core.units import format_cr, to_cr
from app.db.base import Base
from app.models import (
    Account,
    AuditLogEntry,
    Code,
    CodeBatch,
    Grant,
    IdempotencyKey,
    JournalEntry,
    Market,
    OutboxEvent,
    Position,
    Posting,
    Resolution,
    Trade,
    User,
    Voucher,
)
from app.pricing import lmsr
from app.schemas.admin.users import SetUserRoleRequest
from app.schemas.me import PortfolioResponse
from app.schemas.profile import SaveProfileRequest
from app.services.audit import AuditAction
from app.services.codes import OWN_BATCH_MESSAGE
from app.services.ledger import resolution_key, void_key
from scripts.production_env import assignments
from scripts.seed import (
    BATCH_LABEL,
    BATCH_SIZE,
    BATCH_VALID_FOR,
    DESK,
    MARKETS,
    SEED_NAMESPACE,
    STUDENTS,
    VOUCHERS,
    Buy,
    DemoMarket,
    Person,
    SeedRefusedError,
    SellBack,
    seed,
)
from tests.support.books import assert_books_balance, count_rows, replay, system_balance
from tests.support.database import BACKEND_ROOT, reset_database
from tests.support.factories import ActAs, Factory, PortalHeaders
from tests.support.settings import EXAMPLE_ENV_FILE

pytestmark = pytest.mark.integration

PEOPLE: Final[tuple[Person, ...]] = (*STUDENTS, DESK)
TRADES_PLANNED: Final[int] = sum(len(market.trades) for market in MARKETS)
OPENED: Final[tuple[DemoMarket, ...]] = tuple(
    market
    for market in MARKETS
    if market.ends not in (MarketStatus.PROPOSED, MarketStatus.REJECTED)
)
"""The demo markets an approval opened."""

PRINTED_CODE: Final[re.Pattern[str]] = re.compile(
    r"^\s+([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})  (\S+)$", re.MULTILINE
)
"""A line of the seed's output that shows a code and the address of its card."""

COMMAND_SECONDS: Final[float] = 120.0
"""How long `python -m scripts.seed` may take before a test gives up on it."""

type Rows = dict[str, list[tuple[object, ...]]]

type Owed = Callable[[Sequence[Position], Resolution | None], int | None]
"""What `MarketView` says a settled market owes the holder of some positions."""


class Interrupted(Exception):
    """What a test lets a service raise to end a run of the seed half way."""


@dataclass(frozen=True, slots=True)
class Interruption:
    """Where a run of the seed dies: at one call of one method of one service."""

    service: str
    """The attribute of the container that holds the service."""

    method: str
    call: int
    """Which call of that method, counted from 1."""

    after: bool
    """Whether that call still did its work before the run died: the row is there and
    the seed never got to hear of it."""

    def __str__(self) -> str:
        return f"{'after' if self.after else 'before'}-{self.service}.{self.method}-{self.call}"


CODES_NEVER_SHOWN: Final[Interruption] = Interruption("codes", "create_batch", 1, after=True)
"""The one place where the next run prints less than an undisturbed one: the batch is
made, and the codes it answered with were never printed. They are not gone: an admin
opens them in the admin panel."""

INTERRUPTIONS: Final[tuple[Interruption, ...]] = (
    # Some of the people are registered.
    Interruption("profile", "save", 4, after=True),
    # A proposal is there that the run never heard of.
    Interruption("markets", "propose", 1, after=True),
    # A market is open and nobody has traded it yet.
    Interruption("market_lifecycle", "approve", 3, after=True),
    # A market has some of its trades.
    Interruption("trading", "trade", 2, after=True),
    # The sale is still to come, and what it sells is what an earlier buy bought.
    Interruption("trading", "trade", 22, after=False),
    # The trades are done and the last step is missing: the halt, the resolution after
    # the close, the void after the close, the rejection.
    Interruption("market_lifecycle", "halt", 1, after=False),
    Interruption("market_lifecycle", "close", 2, after=True),
    Interruption("market_lifecycle", "void", 1, after=False),
    Interruption("market_lifecycle", "reject", 1, after=False),
    # A voucher is issued and its scan never happened; nobody knows its code.
    Interruption("vouchers", "issue", 2, after=True),
    Interruption("codes", "create_batch", 1, after=False),
    CODES_NEVER_SHOWN,
)


# --- Reading what a run left behind ------------------------------------------------------


def address_of(person: Person, settings: Settings) -> str:
    return f"{person.mailbox}@{settings.identity.allowed_email_domains[0]}"


async def everything(container: Container) -> Rows:
    """Every row of every table. Two readings are equal exactly when nothing was
    written in between."""
    found: Rows = {}
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            rows = await uow.session.execute(select(table).order_by(*table.primary_key.columns))
            found[table.name] = [tuple(row) for row in rows]
    return found


async def rows[ModelT: Base](container: Container, model: type[ModelT]) -> Sequence[ModelT]:
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(model))).all()


async def user_of(container: Container, person: Person) -> User:
    async with container.uow_factory() as uow:
        user = await uow.users.get_by_email(address_of(person, container.settings))
    assert user is not None, f"{person.username} is not registered"
    return user


async def market_of(container: Container, plan: DemoMarket) -> Market:
    (market,) = [row for row in await rows(container, Market) if row.question == plan.question]
    return market


async def demo_state(container: Container) -> dict[str, object]:
    """What a run left behind, without what differs from run to run by chance: ids,
    hashes, instants. Two databases with the same state hold the same demo."""
    async with container.uow_factory() as uow:
        session = uow.session
        users = (await session.scalars(select(User))).all()
        accounts = (await session.scalars(select(Account))).all()
        markets = (await session.scalars(select(Market))).all()
        resolutions = (await session.scalars(select(Resolution))).all()
        positions = (await session.scalars(select(Position))).all()
        trades = (await session.scalars(select(Trade))).all()
        vouchers = (await session.scalars(select(Voucher))).all()
        batches = (await session.scalars(select(CodeBatch))).all()
        codes = (await session.scalars(select(Code))).all()
        grants = (await session.scalars(select(Grant))).all()
        entries = (await session.scalars(select(JournalEntry))).all()
        audited = (await session.scalars(select(AuditLogEntry))).all()
        outbox = (await session.scalars(select(OutboxEvent))).all()
        keys = (await session.scalars(select(IdempotencyKey))).all()

    name = {user.id: user.username for user in users}
    question = {market.id: market.question for market in markets}
    balance = {account.owner_id: account.balance_micro for account in accounts}
    in_market = {account.market_id: account.balance_micro for account in accounts}
    settled = {resolution.market_id: resolution for resolution in resolutions}
    return {
        "people": sorted(
            (
                user.email,
                user.username,
                user.name,
                user.degree_level,
                user.degree_programme,
                user.role,
                user.status,
                balance[user.id],
            )
            for user in users
        ),
        "system accounts": {
            account.kind: account.balance_micro
            for account in accounts
            if account.owner_id is None and account.market_id is None
        },
        "markets": sorted(
            (
                market.question,
                market.status,
                market.tier,
                market.budget_micro,
                market.b_micro,
                market.q_yes_micro,
                market.q_no_micro,
                market.version,
                market.volume_micro,
                market.rejection_reason,
                name[market.created_by],
                name.get(market.approved_by) if market.approved_by else None,
                in_market.get(market.id),
                (
                    (settled[market.id].outcome, settled[market.id].evidence)
                    if market.id in settled
                    else None
                ),
            )
            for market in markets
        ),
        "positions": sorted(
            (
                name[position.user_id],
                question[position.market_id],
                position.outcome,
                position.shares_micro,
                position.cost_micro,
                position.proceeds_micro,
            )
            for position in positions
        ),
        "trades": sorted(
            (
                question[trade.market_id],
                trade.market_version,
                name[trade.user_id],
                trade.side,
                trade.outcome,
                trade.shares_micro,
                trade.amount_micro,
                trade.p_yes_before_micro,
                trade.p_yes_after_micro,
            )
            for trade in trades
        ),
        "vouchers": sorted(
            (
                name[voucher.user_id],
                voucher.product_id,
                voucher.product_name,
                voucher.status,
                voucher.value_micro,
                name[voucher.redeemed_by] if voucher.redeemed_by else None,
            )
            for voucher in vouchers
        ),
        "batches": sorted(
            (
                batch.label,
                batch.count,
                batch.value_micro,
                batch.one_per_user,
                name[batch.created_by],
                batch.revoked_at,
            )
            for batch in batches
        ),
        "codes": Counter(code.status for code in codes),
        "grants": sorted((name[grant.user_id], grant.period) for grant in grants),
        "journal entries": Counter(entry.kind for entry in entries),
        "audit rows": Counter(
            (row.action, name[row.actor_id] if row.actor_id else None) for row in audited
        ),
        "outbox rows": len(outbox),
        "idempotency keys": len(keys),
    }


def printed_codes(output: str) -> list[tuple[str, str]]:
    """The codes a run printed, each with the address of its card."""
    return PRINTED_CODE.findall(output)


_UNDISTURBED: Final[dict[str, object]] = {}


async def undisturbed_demo(
    container: Container, owner_engine: AsyncEngine, flush_redis: Callable[[], Awaitable[None]]
) -> dict[str, object]:
    """The `demo_state` one undisturbed run ends in, on an empty database.

    It is the same in every test: the plan, the settings and the instant the clock
    starts at are. So it is worked out once per session, by a run on the still empty
    database of the first test that asks, which is emptied again afterwards.
    """
    if not _UNDISTURBED:
        await seed(container)
        _UNDISTURBED.update(await demo_state(container))
        await reset_database(owner_engine)
        await flush_redis()
    return _UNDISTURBED


# --- Running it --------------------------------------------------------------------------


@pytest.fixture
async def seeded(container: Container, capsys: pytest.CaptureFixture[str]) -> str:
    """The database after one run of the seed; the value is what that run printed."""
    await seed(container)
    return capsys.readouterr().out


@asynccontextmanager
async def built_on(container: Container, settings: Settings) -> AsyncIterator[Container]:
    """The application built on other settings, on the database and with the clock of
    `container`."""
    other = Container.build(settings, clock=container.clock, database=container.database)
    try:
        yield other
    finally:
        await other.stop()


def in_production(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"app": settings.app.model_copy(update={"env": "production"})}
    )


def as_a_bootstrap_admin(person: Person) -> Callable[[Settings], Settings]:
    """Settings in which the address of `person` is one more entry of `ADMIN_EMAILS`."""

    def named(settings: Settings) -> Settings:
        identity = settings.identity
        entries = [*identity.admin_emails, address_of(person, settings)]
        return settings.model_copy(
            update={"identity": identity.model_copy(update={"admin_emails": entries})}
        )

    return named


REFUSED_ON: Final[dict[str, tuple[Callable[[Settings], Settings], str]]] = {
    "production": (in_production, "APP_ENV=production"),
    "a-student-is-a-bootstrap-admin": (
        as_a_bootstrap_admin(STUDENTS[2]),
        f"{STUDENTS[2].mailbox}@",
    ),
    "the-desk-is-a-bootstrap-admin": (as_a_bootstrap_admin(DESK), f"{DESK.mailbox}@"),
}
"""Settings the seed does not run on, and what its refusal names."""

refused_on = pytest.mark.parametrize("why", REFUSED_ON)


@contextmanager
def dying(container: Container, where: Interruption) -> Iterator[None]:
    """While the block is open, the run that reaches `where` dies there."""
    service = getattr(container, where.service)
    real: Callable[..., Awaitable[Any]] = getattr(service, where.method)
    calls = 0

    async def dies(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls < where.call:
            return await real(*args, **kwargs)
        if where.after:
            await real(*args, **kwargs)
        raise Interrupted(str(where))

    setattr(service, where.method, dies)
    try:
        yield
    finally:
        delattr(service, where.method)


@contextmanager
def no_call_of(container: Container, service_name: str, method: str) -> Iterator[None]:
    """While the block is open, a call of that method fails the test."""
    service = getattr(container, service_name)

    async def called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"{service_name}.{method} was called: {args} {kwargs}")

    setattr(service, method, called)
    try:
        yield
    finally:
        delattr(service, method)


# --- Where it does not run ---------------------------------------------------------------


@refused_on
async def test_a_refused_seed_registers_nobody_and_makes_nothing(
    container: Container, capsys: pytest.CaptureFixture[str], why: str
) -> None:
    """In production, and when a demo address is somebody `ADMIN_EMAILS` names: the
    seed is for demo accounts on a stack that is nobody's real one."""
    alter, named = REFUSED_ON[why]
    before = await everything(container)

    async with built_on(container, alter(container.settings)) as refusing:
        with pytest.raises(SeedRefusedError, match=re.escape(named)):
            await seed(refusing)

    assert await everything(container) == before
    assert await count_rows(container.uow_factory, User) == 0
    assert capsys.readouterr().out == ""


@refused_on
@pytest.mark.postgres_down
@pytest.mark.redis_down
async def test_a_refused_seed_has_asked_no_store(container: Container, why: str) -> None:
    """With Postgres and Redis unreachable the refusal is still the refusal, not an
    error of a store: it comes before the first question to either."""
    alter, _ = REFUSED_ON[why]

    async with built_on(container, alter(container.settings)) as refusing:
        with pytest.raises(SeedRefusedError):
            await seed(refusing)


async def test_a_suspended_demo_account_stops_the_seed_before_anybody_is_registered(
    container: Container, factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    """The seed acts for nobody the API would refuse, and it finds that out before it
    registers the first of the others."""
    suspended = STUDENTS[3]
    address = address_of(suspended, container.settings)
    await factory.user(email=address, username=suspended.username, status=UserStatus.SUSPENDED)
    before = await everything(container)

    with pytest.raises(SeedRefusedError, match=f"{re.escape(address)} is suspended"):
        await seed(container)

    assert await everything(container) == before
    assert printed_codes(capsys.readouterr().out) == []
    await assert_books_balance(container.uow_factory)


# --- People --------------------------------------------------------------------------------


async def test_the_seed_registers_its_people_with_a_profile_an_account_and_the_signup_grant(
    seeded: str, container: Container
) -> None:
    settings = container.settings
    grant = settings.grants.signup_grant_micro
    users = {user.email: user for user in await rows(container, User)}

    assert sorted(users) == sorted(address_of(person, settings) for person in PEOPLE)
    for person in PEOPLE:
        user = users[address_of(person, settings)]
        assert (user.username, user.name) == (person.username, person.name)
        assert (user.degree_level, user.degree_programme) == (
            person.degree_level,
            person.degree_programme,
        )
        assert user.username == user.username.lower()
        assert is_programme(user.degree_level, user.degree_programme)
        assert (user.role, user.status) == (UserRole.USER, UserStatus.ACTIVE)
        assert user.policy_accepted_at == user.created_at
        async with container.uow_factory() as uow:
            assert await uow.accounts.get_user_account(user.id) is not None
            assert await uow.grants.list_periods(user.id) == ["SIGNUP"]
    entries = Counter(entry.kind for entry in await rows(container, JournalEntry))
    assert entries[JournalKind.SIGNUP_GRANT] == len(PEOPLE)
    # The desk trades nothing and buys nothing: it still has the whole grant.
    async with container.uow_factory() as uow:
        desk_account = await uow.accounts.get_user_account(users[address_of(DESK, settings)].id)
    assert desk_account is not None
    assert desk_account.balance_micro == grant
    await assert_books_balance(container.uow_factory)


async def test_the_seed_registers_no_bootstrap_admin_and_gives_nobody_a_role(
    seeded: str, container: Container
) -> None:
    """Not one of `ADMIN_EMAILS`: those are real people's, and they sign in through
    the portal. And every row it leaves says USER, the desk's included."""
    identity = container.settings.identity
    roles = container.email_policy
    registered = await rows(container, User)

    assert identity.admin_emails
    assert {user.email for user in registered}.isdisjoint(identity.admin_emails)
    assert {user.role for user in registered} == {UserRole.USER}
    assert {roles.effective_role(user.email, user.role) for user in registered} == {UserRole.USER}
    await assert_books_balance(container.uow_factory)


async def test_the_admin_role_of_the_desk_is_written_nowhere(
    seeded: str,
    container: Container,
    client: httpx.AsyncClient,
    portal_headers: PortalHeaders,
) -> None:
    """The script gave the desk the admin role for its own calls. Its row says USER,
    so whoever signs in as the desk afterwards is a plain user: the real headers lead
    to no admin route, to no barista route and to the role USER."""
    desk = await user_of(container, DESK)
    assert desk.role is UserRole.USER
    headers = portal_headers(desk)
    market = await market_of(container, MARKETS[0])

    me = await client.get("/api/me", headers=headers)
    risk = await client.get("/api/admin/risk", headers=headers)
    proposals = await client.get("/api/markets", params={"status": "PROPOSED"}, headers=headers)
    halt = await client.post(
        f"/api/admin/markets/{market.id}/halt", json={"reason": "As the desk."}, headers=headers
    )
    scan = await client.post(
        "/api/barista/vouchers/redeem", json={"code": "ABCD-EFGH"}, headers=headers
    )

    assert (me.status_code, me.json()["role"]) == (200, "USER")
    for refused in (risk, proposals, halt, scan):
        assert (refused.status_code, refused.json()["code"]) == (403, "FORBIDDEN"), refused.text
    assert (await market_of(container, MARKETS[0])).status is MarketStatus.OPEN
    await assert_books_balance(container.uow_factory)


# --- Markets -------------------------------------------------------------------------------


async def test_the_seed_leaves_every_market_in_the_state_its_plan_names(
    seeded: str, container: Container
) -> None:
    """Open in each tier, one paused, one closed, one resolved, one voided, one still
    proposed and one rejected, each proposed by its student and opened by the desk."""
    desk = await user_of(container, DESK)
    markets = {market.question: market for market in await rows(container, Market)}
    resolutions = {row.market_id: row for row in await rows(container, Resolution)}
    precision = container.settings.pricing.decimal_precision

    assert sorted(markets) == sorted(plan.question for plan in MARKETS)
    for plan in MARKETS:
        market = markets[plan.question]
        proposer = await user_of(container, STUDENTS[plan.proposer])
        assert market.status is plan.ends, plan.slug
        assert market.created_by == proposer.id
        if plan in OPENED:
            # The tier is the desk's choice at the approval; the proposal named none.
            assert plan.tier is not None
            b_micro = container.settings.pricing.tier_b_micro(plan.tier)
            assert (market.tier, market.b_micro) == (plan.tier, b_micro)
            assert market.budget_micro == lmsr.max_loss_micro(b_micro, precision=precision)
            assert market.approved_by == desk.id
            assert market.version == len(plan.trades)
        else:
            assert (market.tier, market.b_micro, market.approved_by) == (None, 0, None)
            assert market.version == 0
        expected_reason = plan.remark if plan.ends is MarketStatus.REJECTED else None
        assert market.rejection_reason == expected_reason

    resolved = markets[
        next(plan for plan in MARKETS if plan.ends is MarketStatus.RESOLVED).question
    ]
    voided = markets[next(plan for plan in MARKETS if plan.ends is MarketStatus.VOID).question]
    assert set(resolutions) == {resolved.id, voided.id}
    assert resolutions[resolved.id].outcome is ResolutionOutcome.YES
    assert resolutions[voided.id].outcome is ResolutionOutcome.VOID
    assert {row.resolved_by for row in resolutions.values()} == {desk.id}
    await assert_books_balance(container.uow_factory)


async def test_every_admin_action_of_the_seed_is_audited_as_the_desk(
    seeded: str, container: Container
) -> None:
    """The actions went through `MarketLifecycleService` and `CodeService`, which write
    the audit row with the change."""
    desk = await user_of(container, DESK)
    audited = await rows(container, AuditLogEntry)
    ends = Counter(plan.ends for plan in MARKETS)
    closed = ends[MarketStatus.CLOSED] + ends[MarketStatus.RESOLVED] + ends[MarketStatus.VOID]

    assert {row.actor_id for row in audited} == {desk.id}
    assert Counter(row.action for row in audited) == {
        AuditAction.MARKET_APPROVE: len(OPENED),
        AuditAction.MARKET_REJECT: ends[MarketStatus.REJECTED],
        AuditAction.MARKET_HALT: ends[MarketStatus.HALTED],
        AuditAction.MARKET_CLOSE: closed,
        AuditAction.MARKET_RESOLVE: ends[MarketStatus.RESOLVED],
        AuditAction.MARKET_VOID: ends[MarketStatus.VOID],
        AuditAction.CODE_BATCH_CREATE: 1,
    }
    await assert_books_balance(container.uow_factory)


# --- Trades --------------------------------------------------------------------------------


async def test_every_trade_of_the_seed_is_a_trade_of_the_trading_service(
    seeded: str, container: Container
) -> None:
    """One row per planned trade, by the student of the plan, under the key of its
    place in the plan, with the stored answer a replay returns and the outbox row the
    chart is fed from."""
    trades = {trade.id: trade for trade in await rows(container, Trade)}
    keys = {(key.user_id, key.key): key for key in await rows(container, IdempotencyKey)}
    outbox = await rows(container, OutboxEvent)

    assert len(trades) == len(keys) == TRADES_PLANNED
    for plan in OPENED:
        market = await market_of(container, plan)
        for place, step in enumerate(plan.trades):
            trader = await user_of(container, STUDENTS[step.student])
            claimed = keys[(trader.id, str(uuid5(SEED_NAMESPACE, f"{plan.slug}/{place}")))]
            assert claimed.response_json is not None
            trade = trades[UUID(claimed.response_json["trade_id"])]
            assert (trade.market_id, trade.user_id) == (market.id, trader.id)
            assert trade.market_version == place + 1
            assert trade.side is (TradeSide.BUY if isinstance(step, Buy) else TradeSide.SELL)
            assert trade.user_id != market.created_by
    # A point per trade, and the opening point of every market that was approved.
    assert len(outbox) == TRADES_PLANNED + len(OPENED)
    await assert_books_balance(container.uow_factory)


async def test_the_trades_of_every_demo_market_were_priced_one_after_another(
    seeded: str, container: Container
) -> None:
    """Priced again by lmsr from an empty market, each trade costs or returns what was
    recorded, and the state they end in is the market's own. A voided market holds
    nothing afterwards; its trades still reproduce."""
    precision = container.settings.pricing.decimal_precision
    for plan in OPENED:
        market = await market_of(container, plan)
        async with container.uow_factory() as uow:
            trades = await uow.trades.list_for_market(market.id, limit=1000)

        end = replay(
            lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro), trades, precision=precision
        )

        assert len(trades) == len(plan.trades)
        if plan.ends is MarketStatus.VOID:
            assert (market.q_yes_micro, market.q_no_micro) == (0, 0)
        else:
            assert (end.q_yes, end.q_no) == (market.q_yes_micro, market.q_no_micro)
    await assert_books_balance(container.uow_factory)


async def test_the_buys_cost_what_their_buttons_say_and_the_sale_returns_the_shares_of_its_buy(
    seeded: str, container: Container
) -> None:
    presets = container.settings.pricing.stake_presets_micro
    sales = 0
    for plan in OPENED:
        market = await market_of(container, plan)
        async with container.uow_factory() as uow:
            trades = await uow.trades.list_for_market(market.id, limit=1000)
        for trade, step in zip(trades, plan.trades, strict=True):
            if isinstance(step, Buy):
                assert trade.outcome is step.outcome
                assert trade.amount_micro == presets[step.preset]
            else:
                assert isinstance(step, SellBack)
                bought = trades[step.bought_in]
                assert (trade.outcome, trade.shares_micro) == (bought.outcome, bought.shares_micro)
                sales += 1

    assert sales == 1
    assert len({trade.user_id for trade in await rows(container, Trade)}) == len(STUDENTS)
    await assert_books_balance(container.uow_factory)


# --- Settlements -------------------------------------------------------------------------


async def test_the_resolved_demo_market_paid_its_winning_shares_and_the_voided_one_its_stakes(
    seeded: str, container: Container
) -> None:
    """The one entry of each settlement credits every trader what `MarketView` makes of
    their positions: a credit per winning share, and the net stake."""
    view = container.market_view
    owner = {account.id: account.owner_id for account in await rows(container, Account)}
    settlements: tuple[tuple[MarketStatus, Callable[[UUID], str], Owed], ...] = (
        (MarketStatus.RESOLVED, resolution_key, view.payout_micro),
        (MarketStatus.VOID, void_key, view.refund_micro),
    )
    for ends, key_of, due in settlements:
        market = await market_of(container, next(plan for plan in MARKETS if plan.ends is ends))
        async with container.uow_factory() as uow:
            resolution = await uow.resolutions.get_by_market(market.id)
            positions = await uow.positions.list_for_market(market.id)
            entry = await uow.journal.get_entry_by_idempotency_key(key_of(market.id))
            assert entry is not None
            postings = (
                await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
            ).all()
        credited = {
            owner[posting.account_id]: posting.amount_micro
            for posting in postings
            if owner[posting.account_id] is not None
        }
        owed = {
            trader: due([row for row in positions if row.user_id == trader], resolution)
            for trader in {position.user_id for position in positions}
        }

        assert credited == {trader: amount for trader, amount in owed.items() if amount}
        assert credited, f"the {ends} demo market pays somebody"
        assert sum(posting.amount_micro for posting in postings) == 0
    await assert_books_balance(container.uow_factory)


async def test_every_screen_of_every_demo_account_is_answered(
    seeded: str,
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """Whatever the seed left behind, the API can show: every read of every account
    answers 200, and an admin's screens do too."""
    public = [await market_of(container, plan) for plan in OPENED]
    for person in PEOPLE:
        as_user(await user_of(container, person))
        paths = [
            "/api/me",
            "/api/me/positions",
            "/api/me/trades",
            "/api/me/ledger",
            "/api/me/proposals",
            "/api/me/vouchers",
            "/api/vouchers/products",
            "/api/markets",
            "/api/leaderboard",
            "/api/leaderboard?sort=win_rate&order=asc",
            *(f"/api/markets/{market.id}" for market in public),
            *(f"/api/markets/{market.id}/history" for market in public),
        ]
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 200, f"{person.username} {path}: {response.text}"

    as_user(await factory.admin())
    for path in (
        "/api/admin/risk",
        "/api/admin/code-batches",
        "/api/admin/users",
        "/api/admin/users?q=demo&role=USER&status=ACTIVE",
        "/api/markets?status=PROPOSED&status=REJECTED",
    ):
        response = await client.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
    await assert_books_balance(container.uow_factory)


async def test_a_demo_portfolio_shows_the_payout_and_the_refund_of_the_settled_markets(
    seeded: str, container: Container, client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """Chiara is in the resolved market, which she won, and in the voided one."""
    chiara = await user_of(container, STUDENTS[2])
    view = container.market_view
    as_user(chiara)

    portfolio = PortfolioResponse.model_validate((await client.get("/api/me/positions")).json())

    by_status = {entry.market.status: entry for entry in portfolio.items}
    for status, figure in (
        (MarketStatus.RESOLVED, "payout_cr"),
        (MarketStatus.VOID, "refund_cr"),
    ):
        entry = by_status[status]
        async with container.uow_factory() as uow:
            positions = await uow.positions.list_for_user_market(chiara.id, entry.market.id)
            resolution = await uow.resolutions.get_by_market(entry.market.id)
        due = view.payout_micro if status is MarketStatus.RESOLVED else view.refund_micro
        amount_micro = due(positions, resolution)
        assert amount_micro is not None
        assert amount_micro > 0
        assert getattr(entry, figure) == to_cr(amount_micro)
    await assert_books_balance(container.uow_factory)


# --- Vouchers ------------------------------------------------------------------------------


async def test_the_seed_issues_a_voucher_per_plan_and_scans_one_at_the_desk(
    seeded: str, container: Container
) -> None:
    products = container.settings.vouchers.products
    desk = await user_of(container, DESK)
    vouchers = {voucher.user_id: voucher for voucher in await rows(container, Voucher)}

    assert len(vouchers) == len(VOUCHERS)
    for place, plan in enumerate(VOUCHERS):
        product = products[place % len(products)]
        voucher = vouchers[(await user_of(container, STUDENTS[plan.student])).id]
        assert (voucher.product_id, voucher.product_name) == (product.id, product.name)
        assert voucher.value_micro == product.price_micro
        if plan.scanned:
            assert (voucher.status, voucher.redeemed_by) == (VoucherStatus.REDEEMED, desk.id)
        else:
            assert (voucher.status, voucher.redeemed_by) == (VoucherStatus.ACTIVE, None)
    waiting = sum(v.value_micro for v in vouchers.values() if v.status is VoucherStatus.ACTIVE)
    scanned = sum(v.value_micro for v in vouchers.values() if v.status is VoucherStatus.REDEEMED)
    assert (
        await system_balance(container.uow_factory, AccountKind.VIS_REDEMPTION_PENDING) == waiting
    )
    assert await system_balance(container.uow_factory, AccountKind.VIS_REDEEMED) == scanned
    await assert_books_balance(container.uow_factory)


# --- Codes ---------------------------------------------------------------------------------


async def test_the_seed_prints_the_codes_of_its_batch_and_each_is_kept_with_its_row(
    seeded: str, container: Container
) -> None:
    """Under its hash, and beside it for as long as it can be redeemed."""
    settings = container.settings
    desk = await user_of(container, DESK)
    (batch,) = await rows(container, CodeBatch)
    stored = await rows(container, Code)
    printed = printed_codes(seeded)

    assert (batch.label, batch.count, batch.created_by) == (BATCH_LABEL, BATCH_SIZE, desk.id)
    assert batch.value_micro == settings.codes.default_value_micro
    assert batch.expires_at == container.clock.now() + BATCH_VALID_FOR
    assert batch.one_per_user
    assert len(printed) == len(set(printed)) == BATCH_SIZE
    for code, card in printed:
        assert card == f"{settings.app.public_base_url}/wallet?code={code}"
    kept = {
        hash_code(plain): plain for plain in (normalize_code(code) or "" for code, _ in printed)
    }
    assert kept == {row.code_hash: row.code for row in stored}
    assert {row.status for row in stored} == {CodeStatus.ACTIVE}
    assert f"worth {format_cr(batch.value_micro)} CR each" in seeded
    await assert_books_balance(container.uow_factory)


async def test_an_admin_opens_the_codes_of_the_demo_batch_again_and_the_log_says_who(
    seeded: str,
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """What `make seed` printed is what the admin panel shows: the codes of a second
    look are those of the first, with their cards' links, and the look is audited."""
    (batch,) = await rows(container, CodeBatch)
    admin = await factory.admin()
    as_user(admin)

    opened = await client.get(f"/api/admin/code-batches/{batch.id}/codes")

    assert opened.status_code == 200, opened.text
    assert [(read["code"], read["redeem_url"]) for read in opened.json()["codes"]] == (
        printed_codes(seeded)
    )
    reads = [
        row
        for row in await rows(container, AuditLogEntry)
        if row.action == AuditAction.CODE_BATCH_READ_CODES
    ]
    assert [(row.actor_id, row.target, row.after) for row in reads] == [
        (admin.id, f"code_batch:{batch.id}", {"code_count": BATCH_SIZE})
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_printed_code_is_redeemed_by_a_student_and_refused_to_the_desk_that_made_it(
    seeded: str,
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    (first, _), (second, _) = printed_codes(seeded)[:2]
    finn = await user_of(container, STUDENTS[5])
    before = await factory.balance_micro(finn)
    value = container.settings.codes.default_value_micro

    as_user(finn)
    redeemed = await client.post("/api/codes/redeem", json={"code": first})
    as_user(await user_of(container, DESK))
    own = await client.post("/api/codes/redeem", json={"code": second})

    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json() == {
        "value_cr": format_cr(value),
        "balance_cr": format_cr(before + value),
    }
    assert (own.status_code, own.json()["message"]) == (403, OWN_BATCH_MESSAGE)
    assert await factory.balance_micro(finn) == before + value
    await assert_books_balance(container.uow_factory)


# --- Running it again ------------------------------------------------------------------------


async def test_a_second_run_finds_everything_and_writes_nothing(
    seeded: str, container: Container, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a row more and not a row changed, in any table. It sends no trade either,
    not even one that would be answered from its key: a trade request counts against
    `MAX_ORDERS_PER_MINUTE` whatever it is answered."""
    after_the_first = await everything(container)

    with (
        no_call_of(container, "trading", "trade"),
        no_call_of(container, "profile", "save"),
        no_call_of(container, "codes", "batch_codes"),
    ):
        await seed(container)
    again = capsys.readouterr().out

    assert await everything(container) == after_the_first
    # It opens the codes of its batch for nobody: that is an admin's to do, and audited.
    assert printed_codes(again) == []
    assert f'The batch "{BATCH_LABEL}" is already there. An admin opens' in again
    assert again.count("already there") == len(PEOPLE) + len(VOUCHERS) + 1
    assert "registered" not in again
    assert "issued" not in again
    for plan in MARKETS:
        assert f"{plan.ends:<9} {plan.question}" in again
    await assert_books_balance(container.uow_factory)


async def test_a_role_somebody_gave_a_demo_account_is_left_as_it_is_by_the_next_run(
    seeded: str, container: Container, factory: Factory, capsys: pytest.CaptureFixture[str]
) -> None:
    """On a development stack an admin tries the panel on the demo accounts: a student
    becomes a barista, the desk an admin. The seed still runs, finds everything, and
    writes nothing: not a role either."""
    admin = factory.identity(await factory.admin())
    ben, desk = await user_of(container, STUDENTS[1]), await user_of(container, DESK)
    await container.admin_users.set_role(admin, ben.id, SetUserRoleRequest(role=UserRole.BARISTA))
    await container.admin_users.set_role(admin, desk.id, SetUserRoleRequest(role=UserRole.ADMIN))
    after_the_panel = await everything(container)
    capsys.readouterr()

    await seed(container)

    assert await everything(container) == after_the_panel
    assert (await user_of(container, STUDENTS[1])).role is UserRole.BARISTA
    assert (await user_of(container, DESK)).role is UserRole.ADMIN
    assert printed_codes(capsys.readouterr().out) == []
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("where", INTERRUPTIONS, ids=str)
async def test_a_run_that_stopped_half_way_is_finished_by_the_next_one(
    container: Container,
    owner_engine: AsyncEngine,
    flush_redis: Callable[[], Awaitable[None]],
    capsys: pytest.CaptureFixture[str],
    where: Interruption,
) -> None:
    """Wherever a run dies, before a call or right after it did its work, the next run
    ends in the state a single undisturbed run ends in: nothing is missing and nothing
    is there twice, down to every balance."""
    undisturbed = await undisturbed_demo(container, owner_engine, flush_redis)
    capsys.readouterr()

    with dying(container, where), pytest.raises(Interrupted):
        await seed(container)
    await assert_books_balance(container.uow_factory)
    capsys.readouterr()
    await seed(container)
    finished = capsys.readouterr().out

    assert await demo_state(container) == undisturbed
    assert len(printed_codes(finished)) == (0 if where == CODES_NEVER_SHOWN else BATCH_SIZE)
    settled = await everything(container)
    await seed(container)
    assert await everything(container) == settled
    await assert_books_balance(container.uow_factory)


@pytest.mark.redis_down
async def test_the_seed_needs_neither_redis_nor_scylla(
    container: Container, capsys: pytest.CaptureFixture[str]
) -> None:
    """`make seed` on a stack whose Redis is still starting: every trade is counted by
    the limiter of the process instead, and nothing else the seed does asks Redis.
    ScyllaDB is a closed port in every test of this file, so the outbox rows wait."""
    await seed(container)

    assert len(printed_codes(capsys.readouterr().out)) == BATCH_SIZE
    assert Counter(market.status for market in await rows(container, Market)) == Counter(
        plan.ends for plan in MARKETS
    )
    assert await count_rows(container.uow_factory, Trade) == TRADES_PLANNED
    assert await count_rows(container.uow_factory, Voucher) == len(VOUCHERS)
    assert {row.published_at for row in await rows(container, OutboxEvent)} == {None}
    await assert_books_balance(container.uow_factory)


async def test_the_seed_stops_at_what_a_service_refuses_and_goes_on_once_that_is_out_of_the_way(
    container: Container, factory: Factory
) -> None:
    """Nothing a service refuses is caught. Here somebody else holds the username of a
    demo student: the run stops with the service's own error, and after that person
    took another name the next run finishes the demo."""
    taken = STUDENTS[2].username
    somebody = await factory.user(username=taken)

    with pytest.raises(UsernameTakenError):
        await seed(container)

    registered = {user.username for user in await rows(container, User)}
    assert registered == {STUDENTS[0].username, STUDENTS[1].username, taken}
    assert await count_rows(container.uow_factory, Market) == 0

    identity = PortalIdentity(email=somebody.email, name=somebody.name, role=UserRole.USER)
    await container.profile.save(
        identity,
        SaveProfileRequest(
            username="somebody_else",
            degree_level=somebody.degree_level,
            degree_programme=somebody.degree_programme,
        ),
    )
    await seed(container)

    assert await count_rows(container.uow_factory, User) == len(PEOPLE) + 1
    assert await count_rows(container.uow_factory, Market) == len(MARKETS)
    assert await count_rows(container.uow_factory, Trade) == TRADES_PLANNED
    await assert_books_balance(container.uow_factory)


# --- The command ---------------------------------------------------------------------------


def command_environment(settings: Settings, **more: str) -> dict[str, str]:
    """The environment of `python -m scripts.seed` in the api container: every variable
    of `.example.env`, with the stores of this test run in place of the stack's.

    The process reads the environment before any `.env` file, and this names every
    variable, so whatever `.env` the machine has plays no part.
    """
    written = assignments(EXAMPLE_ENV_FILE.read_text(encoding="utf-8"))
    example = {
        name: value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
        for name, value in written.items()
    }
    return {
        **os.environ,
        **example,
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "POSTGRES_HOST": settings.postgres.host,
        "POSTGRES_PORT": str(settings.postgres.port),
        "REDIS_HOST": settings.redis.host,
        "REDIS_PORT": str(settings.redis.port),
        "SCYLLA_HOSTS": ",".join(settings.scylla.hosts),
        "SCYLLA_PORT": str(settings.scylla.port),
        **more,
    }


async def run_the_command(environment: dict[str, str]) -> tuple[int, str, str]:
    """`python -m scripts.seed` as `make seed` runs it: its exit status, what it wrote
    to stdout and what it wrote to stderr."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "scripts.seed",
        cwd=BACKEND_ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(COMMAND_SECONDS):
            out, err = await process.communicate()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode is not None
    return process.returncode, out.decode(), err.decode()


async def test_the_command_seeds_the_stack_prints_the_codes_and_can_be_run_again(
    container: Container, settings: Settings
) -> None:
    """`make seed`, twice: the first run exits with 0 and shows the codes, the second
    exits with 0 too, shows none and leaves every table as it was."""
    environment = command_environment(settings)

    status, out, err = await run_the_command(environment)

    assert status == 0, err
    assert len(printed_codes(out)) == BATCH_SIZE
    assert "Traceback" not in err
    assert await count_rows(container.uow_factory, User) == len(PEOPLE)
    assert await count_rows(container.uow_factory, Trade) == TRADES_PLANNED
    statuses = Counter(market.status for market in await rows(container, Market))
    assert statuses == Counter(plan.ends for plan in MARKETS)
    after_the_first = await everything(container)

    status, out, err = await run_the_command(environment)

    assert status == 0, err
    assert printed_codes(out) == []
    assert await everything(container) == after_the_first
    await assert_books_balance(container.uow_factory)


async def test_the_command_refuses_production_with_a_message_and_a_failing_status(
    container: Container, settings: Settings
) -> None:
    status, out, err = await run_the_command(command_environment(settings, APP_ENV="production"))

    assert status == 1
    assert out == ""
    assert "APP_ENV=production" in err
    assert "Traceback" not in err
    assert await count_rows(container.uow_factory, User) == 0
    assert await count_rows(container.uow_factory, Market) == 0
    await assert_books_balance(container.uow_factory)
