"""Lazy upkeep: what one run does, when a run starts, and what a failed run means to the
request that started it (SPEC §2, Appendix A.3).

`UserUpkeepService` is the application's own, on the real Postgres. The two services it
calls are stood in for: `RecordedGrants` and `RecordedVouchers` remember that they were
called and then do, inside the transaction they were handed, whatever work a test gives
them. So each test says what its run meets: nothing owed, a credit to book, a lock
somebody else holds, a connection that goes away, a bug.

* `run` is one transaction that locks the user's account first, books grants for an
  ACTIVE user only and refunds for everybody.
* `run_if_due` starts a run at most once per user, process and
  UPKEEP_INTERVAL_SECONDS.
* A run that fails: a lock that was not granted in time is skipped and the next request
  tries again; a lost connection is raised; anything else is logged and swallowed.

Which months are owed and which vouchers have expired is not asked here: that is
`GrantService.grant_owed_months` and `VoucherService.expire_due`, and theirs to prove.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Final
from uuid import UUID, uuid7

import pytest
from hypothesis import HealthCheck, example, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.clock import local_date, month_period
from app.core.config import Settings
from app.core.enums import SIGNUP_GRANT_PERIOD, AccountKind, JournalKind, UserStatus
from app.core.errors import InsufficientBalanceError
from app.core.units import MICRO
from app.db.database import Database, is_lock_timeout, is_unavailable
from app.models import Account, JournalEntry, Posting, User
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.grants import GrantService
from app.services.ledger import LedgerRef, PostingLine, monthly_grant_key
from app.services.upkeep import UserUpkeepService
from app.services.vouchers import VoucherService
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import DEFAULT_DEGREE_LEVEL, DEFAULT_DEGREE_PROGRAMME, Factory
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

INTERVAL: Final[timedelta] = load_base_settings().identity.upkeep_interval
"""UPKEEP_INTERVAL_SECONDS of the test settings."""

TICK: Final[timedelta] = timedelta(microseconds=1)
"""The smallest step a clock can take."""

NO_TIME: Final[timedelta] = timedelta(0)
"""Between two requests that arrive at the same instant."""

IMPATIENT_MS: Final[int] = 200
"""A POSTGRES_LOCK_TIMEOUT_MS short enough to sit through in a test."""

OWED_MICRO: Final[int] = 5 * MICRO
"""What `booking_what_is_owed` books: an amount of the test's own, not a tunable."""

UPKEEP_LOGGER: Final[str] = "app.services.upkeep"

GRANTS: Final[str] = "grants"
VOUCHERS: Final[str] = "vouchers"
STEPS: Final[tuple[str, ...]] = (GRANTS, VOUCHERS)
"""The two steps of a run, in the order an ACTIVE user's run takes them."""

WAITING_FOR_A_LOCK: Final[str] = (
    "SELECT count(*) FROM pg_stat_activity"
    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
)
LOCK_WAIT_SECONDS: Final[float] = 5.0
"""How long a test waits for a run to get where it is expected, inside a step or in
front of a lock, before it fails instead of waiting on."""

type Work = Callable[[UnitOfWork, Account], Awaitable[None]]
"""What a stand-in does when upkeep calls it. It gets what the real service would get:
the transaction of the run and the user's account, which the run has locked."""


# --- The two services upkeep calls, stood in for ---------------------------------------


async def nothing_owed(uow: UnitOfWork, account: Account) -> None:
    """The work of a service that finds nothing to book."""


@dataclass(frozen=True, slots=True)
class Call:
    """One call a run made to one of the two services."""

    service: str
    uow: UnitOfWork
    account: Account
    user: User | None
    """The row `grant_owed_months` was given. `expire_due` takes none."""


class RecordedGrants(GrantService):
    """Stands in for `GrantService` where upkeep calls it: remembers the call, then does
    the work a test gave it. Nothing is owed until a test says otherwise. It is built
    with none of what the real service is built from, so the one method upkeep calls
    is the one method it has to offer."""

    def __init__(self, calls: list[Call]) -> None:
        self._calls = calls
        self.work: Work = nothing_owed

    async def grant_owed_months(self, uow: UnitOfWork, user: User, account: Account) -> int:
        self._calls.append(Call(GRANTS, uow, account, user))
        await self.work(uow, account)
        return 0


class RecordedVouchers(VoucherService):
    """Stands in for `VoucherService` where upkeep calls it, like `RecordedGrants`."""

    def __init__(self, calls: list[Call]) -> None:
        self._calls = calls
        self.work: Work = nothing_owed

    async def expire_due(self, uow: UnitOfWork, account: Account) -> int:
        self._calls.append(Call(VOUCHERS, uow, account, None))
        await self.work(uow, account)
        return 0


class Collaborators:
    """Both stand-ins, and every call a run made to either, in the order it made them."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.grants = RecordedGrants(self.calls)
        self.vouchers = RecordedVouchers(self.calls)

    @property
    def asked(self) -> list[str]:
        """Which service was called, call by call."""
        return [call.service for call in self.calls]

    def give(self, step: str, work: Work) -> None:
        """Give the stand-in of one step its work."""
        stand_in = self.grants if step == GRANTS else self.vouchers
        stand_in.work = work


class CountedUnits:
    """A `UowFactory` that counts the units of work it hands out. Upkeep opens one per
    run and none otherwise, so this is the number of runs that were started."""

    def __init__(self, uow_factory: UowFactory) -> None:
        self._uow_factory = uow_factory
        self.opened = 0

    def __call__(self) -> UnitOfWork:
        self.opened += 1
        return self._uow_factory()


def upkeep_on(
    uow_factory: UowFactory, container: Container, collaborators: Collaborators
) -> UserUpkeepService:
    """The upkeep service as the container builds it, around the stand-ins. Each one
    built is a process of its own: it has its own record of who was looked after when."""
    return UserUpkeepService(
        uow_factory,
        container.settings,
        container.clock,
        collaborators.grants,
        collaborators.vouchers,
    )


@pytest.fixture
def collaborators() -> Collaborators:
    return Collaborators()


@pytest.fixture
def units(container: Container) -> CountedUnits:
    return CountedUnits(container.uow_factory)


@pytest.fixture
def upkeep(
    units: CountedUnits, container: Container, collaborators: Collaborators
) -> UserUpkeepService:
    return upkeep_on(units, container, collaborators)


@pytest.fixture
async def impatient_units(settings: Settings) -> AsyncIterator[CountedUnits]:
    """Units of work on a connection pool of its own, whose statements give up on a
    lock after `IMPATIENT_MS`. The database is the one every other fixture uses."""
    postgres = settings.postgres.model_copy(update={"lock_timeout_ms": IMPATIENT_MS})
    database = Database(postgres.app_url, postgres)
    yield CountedUnits(lambda: UnitOfWork(database.session_factory))
    await database.dispose()


# --- Work for a stand-in ---------------------------------------------------------------


def raising(make: Callable[[], Exception]) -> Work:
    """Work that fails with an error of its own, made by `make`, every time it is done."""

    async def fail(uow: UnitOfWork, account: Account) -> None:
        raise make()

    return fail


def failing_with(error: Exception) -> Work:
    """Work that fails with `error` itself."""
    return raising(lambda: error)


def booking_what_is_owed(container: Container) -> Work:
    """Work that books `OWED_MICRO` to the account as the grant of the clock's month:
    the grant row, the journal entry from `vis:issuance` and the link between them,
    which is what the real service writes for one owed month."""
    period = month_period(local_date(container.clock.now(), container.settings.app.timezone))

    async def book(uow: UnitOfWork, account: Account) -> None:
        assert account.owner_id is not None
        now = container.clock.now()
        grant = await uow.grants.insert_if_absent(account.owner_id, period, now)
        assert grant is not None, "the grant of this month is booked once in a test"
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        entry = await container.ledger.post(
            uow,
            kind=JournalKind.MONTHLY_GRANT,
            ref_type=LedgerRef.GRANT,
            ref_id=grant.id,
            idempotency_key=monthly_grant_key(account.owner_id, period),
            lines=[PostingLine(issuance.id, -OWED_MICRO), PostingLine(account.id, OWED_MICRO)],
            at=now,
        )
        await uow.grants.set_entry(grant.id, entry.id)

    return book


def losing_the_connection(owner_engine: AsyncEngine) -> Work:
    """Work during which Postgres drops the connection of the run, as a restart would."""

    async def lose(uow: UnitOfWork, account: Account) -> None:
        backend = await uow.session.scalar(text("SELECT pg_backend_pid()"))
        async with owner_engine.connect() as admin:
            await admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})
        await uow.accounts.total_balance()

    return lose


async def leave_an_unbalanced_entry(uow: UnitOfWork, account: Account) -> None:
    """A bug only the commit finds: an entry with one posting, written past
    `LedgerService`. Postgres refuses the transaction when it ends."""
    entry = await uow.journal.add_entry(
        JournalEntry(
            kind=JournalKind.MONTHLY_GRANT,
            ref_type=LedgerRef.GRANT,
            ref_id=uuid7(),
            idempotency_key=f"test:unbalanced:{uuid7()}",
            created_at=TEST_NOW,
        )
    )
    await uow.journal.add_postings(
        [Posting(entry_id=entry.id, account_id=account.id, amount_micro=OWED_MICRO)]
    )


# --- Errors as the driver and SQLAlchemy report them -------------------------------------


class DriverError(Exception):
    """Stands in for the driver's error, which carries Postgres' SQLSTATE."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


def reported(sqlstate: str | None, error: type[DBAPIError] = DBAPIError) -> DBAPIError:
    """An error Postgres answered a statement with, on a connection that is whole."""
    return error("SELECT 1", {}, DriverError(sqlstate))


def lost_connection(sqlstate: str | None = None) -> DBAPIError:
    return InterfaceError("SELECT 1", {}, DriverError(sqlstate), connection_invalidated=True)


class Bug(Exception):
    """Stands in for whatever nobody foresaw."""


LOCK_TIMEOUTS = [
    pytest.param(reported("55P03"), id="lock-not-granted-in-time"),
    pytest.param(reported("55P03", OperationalError), id="lock-not-granted-in-time-operational"),
]
"""A lock another transaction held for longer than POSTGRES_LOCK_TIMEOUT_MS."""

LOST_CONNECTIONS = [
    pytest.param(ConnectionRefusedError("connect call failed"), id="refused"),
    pytest.param(ConnectionResetError("reset by peer"), id="reset"),
    pytest.param(TimeoutError("connect timed out"), id="connect-timeout"),
    pytest.param(PoolTimeoutError("QueuePool limit reached"), id="pool-exhausted"),
    pytest.param(lost_connection(), id="connection-lost"),
    pytest.param(lost_connection("55P03"), id="connection-lost-while-waiting-for-a-lock"),
    pytest.param(reported("57P01"), id="admin-shutdown"),
    pytest.param(reported("08006"), id="connection-failure"),
]
"""Postgres cannot be reached or does not serve."""

OTHER_FAILURES = [
    pytest.param(Bug("nobody foresaw this"), id="bug"),
    pytest.param(InsufficientBalanceError(), id="app-error"),
    pytest.param(reported("23505", IntegrityError), id="unique-violation"),
    pytest.param(reported("40P01"), id="deadlock"),
    pytest.param(reported("57014"), id="statement-cancelled"),
    pytest.param(reported(None), id="no-sqlstate"),
]
"""Everything else, an answer of a reachable Postgres included."""


# --- Looking at the database from outside ------------------------------------------------


async def is_locked(owner_engine: AsyncEngine, account_id: UUID) -> bool:
    """Whether some transaction holds the row lock of this account right now."""
    async with owner_engine.connect() as probe:
        try:
            await probe.execute(
                text("SELECT id FROM accounts WHERE id = :id FOR NO KEY UPDATE NOWAIT"),
                {"id": account_id},
            )
        except DBAPIError as refused:
            # NOWAIT is refused at once, with the SQLSTATE of a lock timeout.
            if not is_lock_timeout(refused):
                raise
            return True
    return False


@asynccontextmanager
async def holding(owner_engine: AsyncEngine, row: str, **parameters: object) -> AsyncIterator[None]:
    """Another transaction holding the lock of the account `row` selects, until the
    block ends."""
    async with owner_engine.connect() as holder:
        await holder.execute(text(f"SELECT id FROM accounts WHERE {row} FOR UPDATE"), parameters)
        yield


async def until_a_transaction_waits_for_a_lock(owner_engine: AsyncEngine) -> None:
    """Return once Postgres reports a session that waits for a lock."""
    async with asyncio.timeout(LOCK_WAIT_SECONDS):
        while True:
            async with owner_engine.connect() as observer:
                if await observer.scalar(text(WAITING_FOR_A_LOCK)):
                    return
            await asyncio.sleep(0.01)


async def account_id_of(container: Container, user: User) -> UUID:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id)
    assert account is not None
    return account.id


async def periods_granted(container: Container, user: User) -> list[str]:
    async with container.uow_factory() as uow:
        return list(await uow.grants.list_periods(user.id))


def upkeep_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """What the upkeep service itself logged."""
    return [record for record in caplog.records if record.name == UPKEEP_LOGGER]


def unsaved_user() -> User:
    """A user row that is in no database, for a test whose Postgres is down."""
    return User(
        id=uuid7(),
        email="nobody@ethz.ch",
        name="No Body",
        username="nobody",
        degree_level=DEFAULT_DEGREE_LEVEL,
        degree_programme=DEFAULT_DEGREE_PROGRAMME,
        status=UserStatus.ACTIVE,
        created_at=TEST_NOW,
    )


# --- One run -----------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(UserStatus))
async def test_a_run_refunds_for_every_status_and_books_grants_for_an_active_user_only(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    factory: Factory,
    status: UserStatus,
) -> None:
    """A refund is owed whatever the status; a grant is not handed to a suspended
    account. Grants come first."""
    user = await factory.user(status=status)

    await upkeep.run(user)

    if status is UserStatus.ACTIVE:
        assert collaborators.asked == [GRANTS, VOUCHERS]
    else:
        assert collaborators.asked == [VOUCHERS]


async def test_a_run_is_one_transaction_on_the_users_own_account(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
) -> None:
    await factory.user()  # somebody else, whose account is not the one
    user = await factory.user()

    await upkeep.run(user)

    grants, vouchers = collaborators.calls
    assert units.opened == 1
    assert grants.uow is vouchers.uow
    assert grants.user is user
    assert grants.account is vouchers.account
    assert (grants.account.kind, grants.account.owner_id) == (AccountKind.USER, user.id)


async def test_a_run_holds_the_lock_on_the_users_account_from_before_its_first_step_to_its_end(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    container: Container,
    factory: Factory,
    owner_engine: AsyncEngine,
) -> None:
    """The account is the first lock: both services are promised that their caller
    holds it. Nobody else's account is touched, and the commit gives the lock back."""
    user, somebody_else = await factory.user(), await factory.user()
    own_id = await account_id_of(container, user)
    others_id = await account_id_of(container, somebody_else)
    seen: list[tuple[str, bool, bool]] = []

    def probing(step: str) -> Work:
        async def probe(uow: UnitOfWork, account: Account) -> None:
            assert account.id == own_id
            own_is_locked = await is_locked(owner_engine, own_id)
            seen.append((step, own_is_locked, await is_locked(owner_engine, others_id)))

        return probe

    for step in STEPS:
        collaborators.give(step, probing(step))
    assert not await is_locked(owner_engine, own_id)

    await upkeep.run(user)

    assert seen == [(GRANTS, True, False), (VOUCHERS, True, False)]
    assert not await is_locked(owner_engine, own_id)


async def test_a_run_commits_what_its_steps_booked(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    container: Container,
    factory: Factory,
) -> None:
    user = await factory.user()
    collaborators.grants.work = booking_what_is_owed(container)

    await upkeep.run(user)

    assert await factory.balance_micro(user) == OWED_MICRO
    assert await count_rows(container.uow_factory, JournalEntry) == 1
    assert len(await periods_granted(container, user)) == 2
    await assert_books_balance(container.uow_factory)


async def test_what_one_step_booked_is_gone_when_the_next_step_fails(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    container: Container,
    factory: Factory,
) -> None:
    """Grants and refunds are booked together or not at all, and `run` hands the
    failure on: what it means to a request is `run_if_due`'s to say."""
    user = await factory.user()
    collaborators.grants.work = booking_what_is_owed(container)
    failure = Bug("the refund failed")
    collaborators.vouchers.work = failing_with(failure)

    with pytest.raises(Bug) as raised:
        await upkeep.run(user)

    assert raised.value is failure
    assert collaborators.asked == [GRANTS, VOUCHERS]
    assert await factory.balance_micro(user) == 0
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    assert await periods_granted(container, user) == [SIGNUP_GRANT_PERIOD]
    await assert_books_balance(container.uow_factory)


async def test_two_runs_for_one_user_at_once_wait_for_each_other_on_the_account_lock(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    factory: Factory,
    owner_engine: AsyncEngine,
) -> None:
    """The second run gets nowhere until the first has committed, so the two never
    book the same thing side by side."""
    user = await factory.user()
    first_is_inside, first_may_finish = asyncio.Event(), asyncio.Event()

    async def stay_inside(uow: UnitOfWork, account: Account) -> None:
        first_is_inside.set()
        await first_may_finish.wait()

    collaborators.grants.work = stay_inside
    first = asyncio.create_task(upkeep.run(user))
    try:
        async with asyncio.timeout(LOCK_WAIT_SECONDS):
            await first_is_inside.wait()
        second = asyncio.create_task(upkeep.run(user))
        try:
            await until_a_transaction_waits_for_a_lock(owner_engine)
            asked_while_the_first_ran = collaborators.asked.copy()
        finally:
            first_may_finish.set()
            await second
    finally:
        first_may_finish.set()
        await first

    assert asked_while_the_first_ran == [GRANTS]
    assert collaborators.asked == [GRANTS, VOUCHERS, GRANTS, VOUCHERS]
    first_run, second_run = collaborators.calls[0].uow, collaborators.calls[2].uow
    assert first_run is not second_run
    assert [call.uow for call in collaborators.calls] == [
        first_run,
        first_run,
        second_run,
        second_run,
    ]


# --- When a run starts: the throttle -------------------------------------------------------


async def test_the_first_request_of_a_user_starts_a_run(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
) -> None:
    user = await factory.user()

    await upkeep.run_if_due(user)

    assert units.opened == 1
    assert collaborators.asked == [GRANTS, VOUCHERS]
    assert collaborators.calls[0].user is user


async def test_a_second_request_within_the_interval_does_nothing_and_asks_postgres_nothing(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    user = await factory.user()
    await upkeep.run_if_due(user)

    await upkeep.run_if_due(user)
    clock.advance(INTERVAL - TICK)
    await upkeep.run_if_due(user)

    assert units.opened == 1, "no unit of work, so no query"
    assert collaborators.asked == [GRANTS, VOUCHERS]


async def test_a_request_one_interval_after_the_last_run_starts_the_next_one(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    user = await factory.user()
    await upkeep.run_if_due(user)

    clock.advance(INTERVAL)
    await upkeep.run_if_due(user)

    assert units.opened == 2
    assert collaborators.asked == [GRANTS, VOUCHERS, GRANTS, VOUCHERS]


async def test_the_interval_counts_from_the_last_run_not_from_the_last_request(
    upkeep: UserUpkeepService, units: CountedUnits, factory: Factory, clock: FrozenClock
) -> None:
    """A user who asks more often than once per interval is still looked after: a
    request that started nothing does not push the next run back."""
    user = await factory.user()
    await upkeep.run_if_due(user)
    two_thirds = INTERVAL * 2 / 3

    clock.advance(two_thirds)
    await upkeep.run_if_due(user)
    assert units.opened == 1

    clock.advance(two_thirds)
    await upkeep.run_if_due(user)
    assert units.opened == 2


async def test_the_interval_is_kept_per_user(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    alice, bob = await factory.user(), await factory.user()

    await upkeep.run_if_due(alice)
    clock.advance(INTERVAL / 2)
    await upkeep.run_if_due(bob)
    await upkeep.run_if_due(alice)
    assert units.opened == 2
    assert [call.user for call in collaborators.calls if call.service == GRANTS] == [alice, bob]

    # Alice is due again, Bob has half an interval to go.
    clock.advance(INTERVAL / 2)
    await upkeep.run_if_due(alice)
    await upkeep.run_if_due(bob)
    assert units.opened == 3
    assert collaborators.calls[-1].account.owner_id == alice.id


async def test_every_process_keeps_its_own_interval_and_starts_with_everybody_due(
    container: Container, units: CountedUnits, collaborators: Collaborators, factory: Factory
) -> None:
    """Two API processes, or one that was restarted: each looks after a user once per
    interval, by its own count. More runs change nothing; `run` books only what is owed."""
    user = await factory.user()
    one_process = upkeep_on(units, container, collaborators)
    another = upkeep_on(units, container, collaborators)

    await one_process.run_if_due(user)
    await one_process.run_if_due(user)
    assert units.opened == 1

    await another.run_if_due(user)
    await another.run_if_due(user)
    assert units.opened == 2


async def test_the_interval_is_upkeep_interval_seconds_of_the_settings(
    container: Container,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    shorter = timedelta(seconds=5)
    assert shorter < INTERVAL
    identity = container.settings.identity.model_copy(
        update={"upkeep_interval_seconds": int(shorter.total_seconds())}
    )
    upkeep = UserUpkeepService(
        units,
        container.settings.model_copy(update={"identity": identity}),
        clock,
        collaborators.grants,
        collaborators.vouchers,
    )
    user = await factory.user()
    await upkeep.run_if_due(user)

    clock.advance(shorter - TICK)
    await upkeep.run_if_due(user)
    assert units.opened == 1

    clock.advance(TICK)
    await upkeep.run_if_due(user)
    assert units.opened == 2


async def test_requests_of_one_user_that_arrive_together_start_one_run(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
) -> None:
    """The instant is stored before anything is awaited, so the requests that come
    while the first one's run is under way find it."""
    user = await factory.user()

    await asyncio.gather(*(upkeep.run_if_due(user) for _ in range(5)))

    assert units.opened == 1
    assert collaborators.asked == [GRANTS, VOUCHERS]


class Fate(Enum):
    """How a run ends once it was started."""

    DONE = auto()
    LOCK_TIMEOUT = auto()
    LOST_CONNECTION = auto()
    BUG = auto()


WORK_OF: Final[dict[Fate, Work]] = {
    Fate.DONE: nothing_owed,
    Fate.LOCK_TIMEOUT: raising(lambda: reported("55P03")),
    Fate.LOST_CONNECTION: raising(lost_connection),
    Fate.BUG: raising(lambda: Bug("nobody foresaw this")),
}

gaps = st.one_of(
    st.sampled_from([NO_TIME, INTERVAL - TICK, INTERVAL, INTERVAL + TICK]),
    st.timedeltas(min_value=NO_TIME, max_value=3 * INTERVAL),
)
"""The time between two requests of one user: around the interval, and anything up to
three of them."""


@hypothesis_settings(
    max_examples=60,
    deadline=None,
    # The fixtures are shared by the examples. Each example brings what it must not
    # share: its own user, its own upkeep service and a clock set back to the start.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(requests=st.lists(st.tuples(gaps, st.sampled_from(Fate)), min_size=1, max_size=10))
@example(
    requests=[
        (NO_TIME, Fate.LOCK_TIMEOUT),
        (NO_TIME, Fate.DONE),
        (INTERVAL - TICK, Fate.DONE),
        (TICK, Fate.DONE),
    ]
)
@example(
    requests=[
        (NO_TIME, Fate.LOST_CONNECTION),
        (INTERVAL - TICK, Fate.LOST_CONNECTION),
        (TICK, Fate.LOST_CONNECTION),
    ]
)
@example(
    requests=[
        (NO_TIME, Fate.BUG),
        (INTERVAL - TICK, Fate.DONE),
        (TICK, Fate.LOCK_TIMEOUT),
        (NO_TIME, Fate.BUG),
        (NO_TIME, Fate.DONE),
    ]
)
async def test_a_request_starts_a_run_exactly_when_none_was_started_within_the_interval(
    requests: list[tuple[timedelta, Fate]],
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    """Whatever the requests of a user and however their runs end: a run starts when
    the last one that counts was started an interval ago or more, and a run that timed
    out on a lock does not count. Only a lost connection reaches the request."""
    clock.set(TEST_NOW)
    user = await factory.user()
    collaborators = Collaborators()
    units = CountedUnits(container.uow_factory)
    upkeep = upkeep_on(units, container, collaborators)
    counted_from: datetime | None = None

    for gap, fate in requests:
        now = clock.advance(gap)
        due = counted_from is None or now - counted_from >= INTERVAL
        collaborators.vouchers.work = WORK_OF[fate]
        started_before = units.opened
        raised = False

        try:
            await upkeep.run_if_due(user)
        except InterfaceError:
            raised = True

        assert units.opened - started_before == int(due)
        assert raised == (due and fate is Fate.LOST_CONNECTION)
        if due:
            counted_from = None if fate is Fate.LOCK_TIMEOUT else now


# --- A run that fails ----------------------------------------------------------------------


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("error", LOCK_TIMEOUTS)
async def test_a_lock_timeout_is_logged_and_swallowed_and_the_next_request_tries_again(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    step: str,
) -> None:
    """Postgres is up and nothing was written: the request goes on without its upkeep,
    and the instant is taken back, so no interval has to pass before the next attempt."""
    user = await factory.user()
    collaborators.give(step, failing_with(error))

    with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
        await upkeep.run_if_due(user)

    (record,) = upkeep_records(caplog)
    assert record.levelno == logging.WARNING
    assert str(user.id) in record.getMessage()
    assert units.opened == 1

    # What was owed is still owed: the very next request starts a run, and that one counts.
    collaborators.give(step, nothing_owed)
    await upkeep.run_if_due(user)
    await upkeep.run_if_due(user)
    assert units.opened == 2
    assert collaborators.asked[-2:] == [GRANTS, VOUCHERS]
    assert len(upkeep_records(caplog)) == 1


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("error", LOST_CONNECTIONS)
async def test_a_lost_connection_is_raised_as_it_is(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    step: str,
) -> None:
    """It becomes the API's 503, which logs it there. One that was lost while it waited
    for a lock is a lost connection, not a lock timeout."""
    user = await factory.user()
    collaborators.give(step, failing_with(error))

    with (
        caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER),
        pytest.raises(type(error)) as raised,
    ):
        await upkeep.run_if_due(user)

    assert raised.value is error
    assert is_unavailable(raised.value)
    assert upkeep_records(caplog) == []

    # The instant stays: the next attempt is one interval away.
    collaborators.give(step, nothing_owed)
    await upkeep.run_if_due(user)
    assert units.opened == 1
    clock.advance(INTERVAL)
    await upkeep.run_if_due(user)
    assert units.opened == 2


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("error", OTHER_FAILURES)
async def test_any_other_failure_is_logged_with_its_traceback_and_swallowed(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    step: str,
) -> None:
    """A request must not fail because of upkeep. The next attempt is one interval away."""
    user = await factory.user()
    collaborators.give(step, failing_with(error))

    with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
        await upkeep.run_if_due(user)

    (record,) = upkeep_records(caplog)
    assert record.levelno == logging.ERROR
    assert str(user.id) in record.getMessage()
    assert record.exc_info is not None
    assert record.exc_info[1] is error
    assert record.exc_info[2] is not None, "the traceback"

    collaborators.give(step, nothing_owed)
    clock.advance(INTERVAL - TICK)
    await upkeep.run_if_due(user)
    assert units.opened == 1
    clock.advance(TICK)
    await upkeep.run_if_due(user)
    assert units.opened == 2
    assert len(upkeep_records(caplog)) == 1


async def test_a_run_that_does_its_work_logs_nothing(
    upkeep: UserUpkeepService, factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    user = await factory.user()

    with caplog.at_level(logging.DEBUG, logger=UPKEEP_LOGGER):
        await upkeep.run_if_due(user)

    assert upkeep_records(caplog) == []


async def test_a_request_cancelled_during_its_run_leaves_nothing_booked_and_no_lock(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancellation is no failure of the run and is not caught: it ends the request.
    What the run had booked is rolled back, its lock is given back, and the instant
    stays, so the next attempt is one interval away."""
    user = await factory.user()
    account_id = await account_id_of(container, user)
    book = booking_what_is_owed(container)
    has_booked = asyncio.Event()

    async def book_and_stay_inside(uow: UnitOfWork, account: Account) -> None:
        await book(uow, account)
        has_booked.set()
        await asyncio.Event().wait()

    collaborators.grants.work = book_and_stay_inside
    request = asyncio.create_task(upkeep.run_if_due(user))
    try:
        async with asyncio.timeout(LOCK_WAIT_SECONDS):
            await has_booked.wait()
        locked_while_it_ran = await is_locked(owner_engine, account_id)
    finally:
        request.cancel()
    with (
        caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER),
        pytest.raises(asyncio.CancelledError),
    ):
        await request

    assert locked_while_it_ran
    assert not await is_locked(owner_engine, account_id)
    assert await factory.balance_micro(user) == 0
    assert await periods_granted(container, user) == [SIGNUP_GRANT_PERIOD]
    assert upkeep_records(caplog) == []

    collaborators.grants.work = book
    await upkeep.run_if_due(user)
    assert units.opened == 1
    clock.advance(INTERVAL)
    await upkeep.run_if_due(user)
    assert units.opened == 2
    assert await factory.balance_micro(user) == OWED_MICRO
    await assert_books_balance(container.uow_factory)


# --- The same failures, as Postgres really produces them -----------------------------------


async def test_while_somebody_holds_the_users_account_a_request_goes_on_without_its_upkeep(
    impatient_units: CountedUnits,
    container: Container,
    collaborators: Collaborators,
    factory: Factory,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lock a run takes first, held by another transaction for longer than the lock
    timeout: a trade of the same user that is slow to commit, say."""
    user = await factory.user()
    upkeep = upkeep_on(impatient_units, container, collaborators)
    collaborators.grants.work = booking_what_is_owed(container)

    async with holding(owner_engine, "owner_id = :user_id", user_id=user.id):
        with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
            await upkeep.run_if_due(user)

    assert collaborators.asked == [], "the run never got its first lock"
    (record,) = upkeep_records(caplog)
    assert record.levelno == logging.WARNING
    assert str(user.id) in record.getMessage()
    assert await factory.balance_micro(user) == 0

    # The holder is gone. The same instant, the next request: it is looked after now.
    await upkeep.run_if_due(user)

    assert impatient_units.opened == 2
    assert collaborators.asked == [GRANTS, VOUCHERS]
    assert await factory.balance_micro(user) == OWED_MICRO
    await assert_books_balance(container.uow_factory)


async def test_while_somebody_holds_an_account_of_vis_a_request_goes_on_without_its_upkeep(
    impatient_units: CountedUnits,
    container: Container,
    collaborators: Collaborators,
    factory: Factory,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The lock `LedgerService` takes last when it books a grant. By then the run has
    its own account and has written the grant row; none of it stays."""
    user = await factory.user()
    upkeep = upkeep_on(impatient_units, container, collaborators)
    collaborators.grants.work = booking_what_is_owed(container)

    async with holding(owner_engine, "kind = 'VIS_ISSUANCE'"):
        with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
            await upkeep.run_if_due(user)

    assert collaborators.asked == [GRANTS], "the transaction ended in the first step"
    (record,) = upkeep_records(caplog)
    assert record.levelno == logging.WARNING
    assert await factory.balance_micro(user) == 0
    assert await periods_granted(container, user) == [SIGNUP_GRANT_PERIOD]
    assert await count_rows(container.uow_factory, JournalEntry) == 0

    await upkeep.run_if_due(user)

    assert impatient_units.opened == 2
    assert await factory.balance_micro(user) == OWED_MICRO
    assert len(await periods_granted(container, user)) == 2
    await assert_books_balance(container.uow_factory)


async def test_a_connection_postgres_drops_during_a_run_is_raised(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What a restart of Postgres looks like to a run that was under way."""
    user = await factory.user()
    collaborators.grants.work = losing_the_connection(owner_engine)

    with (
        caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER),
        pytest.raises(DBAPIError) as lost,
    ):
        await upkeep.run_if_due(user)

    assert is_unavailable(lost.value)
    assert not is_lock_timeout(lost.value)
    assert upkeep_records(caplog) == []

    # The pool replaces the dead connection: the next run is served again.
    collaborators.grants.work = nothing_owed
    clock.advance(INTERVAL)
    await upkeep.run_if_due(user)
    assert units.opened == 2
    assert collaborators.asked == [GRANTS, GRANTS, VOUCHERS]


@pytest.mark.postgres_down
async def test_an_unreachable_postgres_is_raised(
    upkeep: UserUpkeepService,
    units: CountedUnits,
    collaborators: Collaborators,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user = unsaved_user()

    with (
        caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER),
        pytest.raises(ConnectionError) as refused,
    ):
        await upkeep.run_if_due(user)

    assert is_unavailable(refused.value)
    assert units.opened == 1
    assert collaborators.asked == []
    assert upkeep_records(caplog) == []


async def test_a_failure_only_the_commit_finds_is_swallowed_and_leaves_nothing_behind(
    upkeep: UserUpkeepService,
    collaborators: Collaborators,
    container: Container,
    factory: Factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ledger's own check runs when the transaction ends, after both steps have
    returned. That failure is the run's too."""
    user = await factory.user()
    collaborators.grants.work = booking_what_is_owed(container)
    collaborators.vouchers.work = leave_an_unbalanced_entry

    with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
        await upkeep.run_if_due(user)

    (record,) = upkeep_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], IntegrityError)
    assert collaborators.asked == [GRANTS, VOUCHERS]
    assert await factory.balance_micro(user) == 0
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    assert await periods_granted(container, user) == [SIGNUP_GRANT_PERIOD]
    await assert_books_balance(container.uow_factory)
