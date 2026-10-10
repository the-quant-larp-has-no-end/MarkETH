"""`GrantService`: the signup grant, the monthly grants and the admin's run
(SPEC §2, §12 "Grants", Appendix A.3, A.5, A.8).

A grant happens once because its `grants` row is unique per user and period. A monthly
grant falls due a whole number of calendar months after the sign-up, on its day and at
its time of day in `APP_TIMEZONE`. The clock of the tests starts on 9 October 2026 at
14:00 in Zurich, so a user the factory makes then is owed a grant on the 9th of every
later month at 14:00 there. When exactly a grant falls due, through short months and
clock changes, is `tests/unit/test_monthly_grants.py`; here what falls due is booked.
"""

import asyncio
import logging
from calendar import monthrange
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    AccountKind,
    JournalKind,
    UserRole,
    UserStatus,
)
from app.core.units import format_cr, to_cr
from app.db import database as database_errors
from app.models import Account, AuditLogEntry, Grant, JournalEntry, Posting, User
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.admin.grants import MonthlyGrantRunResponse
from app.services.audit import AuditAction
from app.services.grants import GrantService
from app.services.ledger import LedgerRef, monthly_grant_key, signup_grant_key
from tests.support.books import assert_books_balance, count_rows, system_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

BASE: Final[Settings] = load_base_settings()
ZURICH: Final[ZoneInfo] = BASE.app.timezone
SIGNUP: Final[int] = BASE.grants.signup_grant_micro
MONTHLY: Final[int] = BASE.grants.monthly_grant_micro
"""SIGNUP_GRANT_CR and MONTHLY_GRANT_CR of the test settings, in µCR."""

FIRST_DUE: Final[datetime] = datetime(2026, 11, 9, 14, 0, tzinfo=ZURICH)
SECOND_DUE: Final[datetime] = datetime(2026, 12, 9, 14, 0, tzinfo=ZURICH)
THIRD_DUE: Final[datetime] = datetime(2027, 1, 9, 14, 0, tzinfo=ZURICH)
"""When the first three monthly grants fall due for somebody who signed up at
`TEST_NOW`: the 9th of the three months after it, at 14:00 in Zurich."""

TICK: Final[timedelta] = timedelta(microseconds=1)
"""The smallest step a clock can take."""

IMPATIENT_MS: Final[int] = 200
"""A POSTGRES_LOCK_TIMEOUT_MS short enough to sit through in a test."""


def test_the_tests_sign_up_at_two_in_the_afternoon_of_the_ninth_in_zurich() -> None:
    """What the tests below stand on: the settings say Zurich, both grants move
    credits, and the time of day a grant falls due at is Zurich's. Summer time ends
    between the sign-up and the first grant, so in UTC that grant falls due an hour
    later in the day than the sign-up was."""
    assert BASE.app.timezone_name == "Europe/Zurich"
    assert SIGNUP > 0
    assert MONTHLY > 0
    assert TEST_NOW.astimezone(ZURICH) == datetime(2026, 10, 9, 14, 0, tzinfo=ZURICH)
    assert TEST_NOW.astimezone(UTC) == datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert FIRST_DUE.astimezone(UTC) == datetime(2026, 11, 9, 13, 0, tzinfo=UTC)
    assert SECOND_DUE.astimezone(UTC) == datetime(2026, 12, 9, 13, 0, tzinfo=UTC)
    assert THIRD_DUE.astimezone(UTC) == datetime(2027, 1, 9, 13, 0, tzinfo=UTC)


async def without_a_grant(container: Container, name: str = "newcomer") -> User:
    """A user and their account as the profile transaction has them the moment before
    it books the signup grant: no `grants` row yet."""
    level, programme = CATALOGUE[0].level, CATALOGUE[0].programmes[0]
    async with container.uow_factory() as uow:
        user = await uow.users.add(
            User(
                email=f"{name}@ethz.ch",
                name="",
                username=name,
                degree_level=level,
                degree_programme=programme,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
                policy_accepted_at=container.clock.now(),
                created_at=container.clock.now(),
            )
        )
        await uow.accounts.create_user_account(user.id)
    return user


async def grant_signup(grants: GrantService, container: Container, user: User) -> bool:
    """`grant_signup` the way its callers run it: in a transaction of its own that
    holds the user's account."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        return await grants.grant_signup(uow, user, account)


async def grant_owed_months(grants: GrantService, container: Container, user: User) -> int:
    """`grant_owed_months` the way upkeep runs it: account lock first."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        return await grants.grant_owed_months(uow, user, account)


async def periods_of(container: Container, user: User) -> list[str]:
    """The periods a user was granted, without the signup grant, oldest first."""
    async with container.uow_factory() as uow:
        periods = await uow.grants.list_periods(user.id)
    return [period for period in periods if period != SIGNUP_GRANT_PERIOD]


async def grants_of(container: Container, user: User) -> list[Grant]:
    async with container.uow_factory() as uow:
        statement = select(Grant).where(Grant.user_id == user.id).order_by(Grant.period)
        return list((await uow.session.scalars(statement)).all())


async def entries(container: Container, kind: JournalKind) -> list[JournalEntry]:
    """The grant entries of one kind, in the order they were booked: that of the
    postings that credit their users."""
    async with container.uow_factory() as uow:
        statement = (
            select(JournalEntry)
            .join(Posting, Posting.entry_id == JournalEntry.id)
            .where(JournalEntry.kind == kind, Posting.amount_micro > 0)
            .order_by(Posting.id)
        )
        return list((await uow.session.scalars(statement)).all())


async def audit_rows(container: Container) -> list[AuditLogEntry]:
    async with container.uow_factory() as uow:
        statement = select(AuditLogEntry).order_by(AuditLogEntry.id)
        return list((await uow.session.scalars(statement)).all())


@contextmanager
def recorded_statements(container: Container) -> Iterator[list[str]]:
    """The SQL the application sends to Postgres inside the block, in the order sent."""
    statements: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    engine = container.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def grants_with(container: Container, settings: Settings, **amounts: int) -> GrantService:
    """The application's `GrantService` as it would be with other grant amounts."""
    grants = settings.grants.model_copy(update=amounts)
    return GrantService(
        container.uow_factory,
        settings.model_copy(update={"grants": grants}),
        container.clock,
        container.ledger,
        container.audit,
    )


# --- The signup grant ------------------------------------------------------------------


async def test_the_signup_grant_books_once(container: Container, clock: FrozenClock) -> None:
    user = await without_a_grant(container)

    assert await grant_signup(container.grants, container, user) is True
    assert await grant_signup(container.grants, container, user) is False

    (grant,) = await grants_of(container, user)
    (entry,) = await entries(container, JournalKind.SIGNUP_GRANT)
    assert (grant.period, grant.entry_id, grant.created_at) == (
        SIGNUP_GRANT_PERIOD,
        entry.id,
        clock.now(),
    )
    assert (entry.ref_type, entry.ref_id) == (LedgerRef.GRANT, grant.id)
    assert (entry.idempotency_key, entry.created_at) == (signup_grant_key(user.id), clock.now())
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id)
    assert account is not None
    assert account.balance_micro == SIGNUP
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) == -SIGNUP
    assert await count_rows(container.uow_factory, JournalEntry) == 1
    await assert_books_balance(container.uow_factory)


async def test_two_signup_grants_at_once_book_one(container: Container) -> None:
    """Each caller holds the account first, so the second finds the row of the first."""
    user = await without_a_grant(container)

    booked = await asyncio.gather(
        *(grant_signup(container.grants, container, user) for _ in range(5))
    )

    assert sorted(booked) == [False, False, False, False, True]
    assert len(await grants_of(container, user)) == 1
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) == -SIGNUP
    await assert_books_balance(container.uow_factory)


async def test_a_signup_grant_of_nothing_is_recorded_and_posts_nothing(
    container: Container, settings: Settings
) -> None:
    """SIGNUP_GRANT_CR=0: the row says the grant was made, and no entry is written."""
    user = await without_a_grant(container)
    grants = grants_with(container, settings, signup_grant_micro=0)

    assert await grant_signup(grants, container, user) is True
    assert await grant_signup(grants, container, user) is False

    (grant,) = await grants_of(container, user)
    assert (grant.period, grant.entry_id) == (SIGNUP_GRANT_PERIOD, None)
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    await assert_books_balance(container.uow_factory)


# --- The monthly grants ----------------------------------------------------------------


async def test_no_monthly_grant_is_owed_before_a_month_has_passed_since_the_sign_up(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Not on the first of the next month either: the grant follows the sign-up, not
    the calendar."""
    user = await factory.user()

    for now in (
        TEST_NOW,
        datetime(2026, 11, 1, tzinfo=ZURICH),
        datetime(2026, 11, 9, 0, 0, tzinfo=ZURICH),
        FIRST_DUE - TICK,
    ):
        clock.set(now)
        assert await grant_owed_months(container.grants, container, user) == 0

    assert await periods_of(container, user) == []
    assert await factory.balance_micro(user) == 0
    await assert_books_balance(container.uow_factory)


async def test_the_first_monthly_grant_is_booked_a_month_after_the_sign_up_to_the_instant(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    user = await factory.user()
    clock.set(FIRST_DUE)

    assert await grant_owed_months(container.grants, container, user) == 1

    (grant,) = [grant for grant in await grants_of(container, user) if grant.period == "2026-11"]
    (entry,) = await entries(container, JournalKind.MONTHLY_GRANT)
    assert (grant.entry_id, grant.created_at) == (entry.id, clock.now())
    assert (entry.ref_type, entry.ref_id) == (LedgerRef.GRANT, grant.id)
    assert entry.idempotency_key == monthly_grant_key(user.id, "2026-11")
    assert entry.created_at == clock.now()
    assert await factory.balance_micro(user) == MONTHLY
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) == -MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_a_grant_is_dated_when_it_is_booked_and_filed_under_the_month_it_fell_due_in(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Somebody who comes by three weeks late: the row and the entry carry that
    instant, the period is the month of the due day."""
    user = await factory.user()
    late = FIRST_DUE + timedelta(days=21)
    clock.set(late)

    assert await grant_owed_months(container.grants, container, user) == 1

    (grant,) = [grant for grant in await grants_of(container, user) if grant.entry_id]
    (entry,) = await entries(container, JournalKind.MONTHLY_GRANT)
    assert (grant.period, grant.created_at, entry.created_at) == ("2026-11", late, late)
    await assert_books_balance(container.uow_factory)


async def test_a_repeated_monthly_grant_inserts_nothing(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    user = await factory.user()
    clock.set(SECOND_DUE)
    assert await grant_owed_months(container.grants, container, user) == 2
    rows = await count_rows(container.uow_factory, Grant)
    booked = await count_rows(container.uow_factory, JournalEntry)

    for _ in range(3):
        assert await grant_owed_months(container.grants, container, user) == 0

    assert await count_rows(container.uow_factory, Grant) == rows
    assert await count_rows(container.uow_factory, JournalEntry) == booked
    assert await factory.balance_micro(user) == 2 * MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_every_missed_month_is_booked_oldest_first(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Signed up on 9 October 2026 and back in the middle of February 2027."""
    user = await factory.user()
    clock.set(datetime(2027, 2, 14, 9, 30, tzinfo=ZURICH))
    owed = ["2026-11", "2026-12", "2027-01", "2027-02"]

    assert await grant_owed_months(container.grants, container, user) == len(owed)

    assert await periods_of(container, user) == owed
    booked = await entries(container, JournalKind.MONTHLY_GRANT)
    assert [entry.idempotency_key for entry in booked] == [
        monthly_grant_key(user.id, period) for period in owed
    ]
    rows = {grant.period: grant for grant in await grants_of(container, user)}
    assert [(entry.ref_id, entry.id) for entry in booked] == [
        (rows[period].id, rows[period].entry_id) for period in owed
    ]
    assert await factory.balance_micro(user) == len(owed) * MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_a_month_that_has_begun_brings_no_grant_until_its_day_has_come(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Back on 5 February: January's grant fell due on the 9th of January, February's
    falls due on the 9th of February."""
    user = await factory.user()
    clock.set(datetime(2027, 2, 5, 18, 0, tzinfo=ZURICH))

    assert await grant_owed_months(container.grants, container, user) == 3

    assert await periods_of(container, user) == ["2026-11", "2026-12", "2027-01"]
    await assert_books_balance(container.uow_factory)


async def test_only_the_months_still_owed_are_booked(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A user who came by in November and again in January."""
    user = await factory.user()
    clock.set(FIRST_DUE + timedelta(days=10))
    assert await grant_owed_months(container.grants, container, user) == 1

    clock.set(datetime(2027, 1, 20, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, user) == 2

    assert await periods_of(container, user) == ["2026-11", "2026-12", "2027-01"]
    assert await factory.balance_micro(user) == 3 * MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_every_user_has_a_grant_day_of_their_own(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Two people who signed up eleven days apart are owed their grants eleven days
    apart, month after month."""
    early = await factory.user()
    clock.set(datetime(2026, 10, 20, 10, 0, tzinfo=ZURICH))
    late = await factory.user()

    clock.set(datetime(2026, 11, 15, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, early) == 1
    assert await grant_owed_months(container.grants, container, late) == 0

    clock.set(datetime(2026, 11, 20, 10, 0, tzinfo=ZURICH) - TICK)
    assert await grant_owed_months(container.grants, container, late) == 0
    clock.set(datetime(2026, 11, 20, 10, 0, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, late) == 1

    assert await periods_of(container, early) == ["2026-11"]
    assert await periods_of(container, late) == ["2026-11"]
    await assert_books_balance(container.uow_factory)


async def test_the_day_of_sign_up_is_the_one_of_the_app_timezone(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Signed up on 31 October at 23:30 UTC, which is 1 November at 00:30 in Zurich.
    The first grant falls due on 1 December at 00:30 there and is December's, though
    UTC is still in November then."""
    clock.set(datetime(2026, 10, 31, 23, 30, tzinfo=UTC))
    user = await factory.user()
    due = datetime(2026, 12, 1, 0, 30, tzinfo=ZURICH)
    assert due.astimezone(UTC) == datetime(2026, 11, 30, 23, 30, tzinfo=UTC)

    clock.set(due - timedelta(seconds=1))
    assert await grant_owed_months(container.grants, container, user) == 0
    clock.set(due)
    assert await grant_owed_months(container.grants, container, user) == 1

    assert await periods_of(container, user) == ["2026-12"]
    await assert_books_balance(container.uow_factory)


async def test_a_sign_up_on_the_31st_is_owed_on_the_last_day_of_a_shorter_month(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """31 December, then 31 January, 28 February and 31 March: February takes its last
    day, and March is back on the 31st."""
    clock.set(datetime(2026, 12, 31, 12, 0, tzinfo=ZURICH))
    user = await factory.user()

    clock.set(datetime(2027, 1, 31, 12, 0, tzinfo=ZURICH) - TICK)
    assert await grant_owed_months(container.grants, container, user) == 0
    clock.set(datetime(2027, 2, 28, 12, 0, tzinfo=ZURICH) - TICK)
    assert await grant_owed_months(container.grants, container, user) == 1
    clock.set(datetime(2027, 2, 28, 12, 0, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, user) == 1
    clock.set(datetime(2027, 3, 30, 23, 59, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, user) == 0
    clock.set(datetime(2027, 3, 31, 12, 0, tzinfo=ZURICH))
    assert await grant_owed_months(container.grants, container, user) == 1

    assert await periods_of(container, user) == ["2027-01", "2027-02", "2027-03"]
    assert await factory.balance_micro(user) == 3 * MONTHLY
    await assert_books_balance(container.uow_factory)


def months_owed(signed_up: datetime, now: datetime) -> list[str]:
    """The months whose grant has fallen due by `now` for somebody who signed up at
    `signed_up`, each as `YYYY-MM`, as Zurich counts them.

    Counted, not walked: the whole calendar months from the one of the sign-up to the
    one of `now`, less the last when its day and time of day have not come yet. This
    month's grant falls due on the day of the sign-up, or on the last day of a shorter
    month, at the time of day of the sign-up.
    """
    start, end = signed_up.astimezone(ZURICH), now.astimezone(ZURICH)
    # Months are numbered from year 0, so that the next number is the next month.
    first_month = start.year * 12 + start.month
    months = end.year * 12 + end.month - first_month
    if months > 0:
        day = min(start.day, monthrange(end.year, end.month)[1])
        due = datetime.combine(end.date().replace(day=day), start.time(), tzinfo=ZURICH)
        if now < due.replace(fold=0):
            months -= 1
    return [
        f"{index // 12:04d}-{index % 12 + 1:02d}"
        for index in range(first_month, first_month + max(months, 0))
    ]


def test_the_oracle_of_the_property_below_counts_the_grants_that_have_fallen_due() -> None:
    assert months_owed(TEST_NOW, TEST_NOW) == []
    assert months_owed(TEST_NOW, datetime(2026, 11, 1, tzinfo=ZURICH)) == []
    assert months_owed(TEST_NOW, FIRST_DUE - TICK) == []
    assert months_owed(TEST_NOW, FIRST_DUE) == ["2026-11"]
    assert months_owed(TEST_NOW, SECOND_DUE - TICK) == ["2026-11"]
    assert months_owed(TEST_NOW, datetime(2027, 1, 9, 13, 0, tzinfo=UTC)) == [
        "2026-11",
        "2026-12",
        "2027-01",
    ]
    on_the_31st = datetime(2027, 1, 31, 8, 0, tzinfo=ZURICH)
    assert months_owed(on_the_31st, datetime(2027, 2, 28, 7, 59, tzinfo=ZURICH)) == []
    assert months_owed(on_the_31st, datetime(2027, 2, 28, 8, 0, tzinfo=ZURICH)) == ["2027-02"]
    assert months_owed(on_the_31st, datetime(2027, 3, 30, 23, 0, tzinfo=ZURICH)) == ["2027-02"]


ENDS_OF_MONTHS = st.builds(
    lambda year, month, seconds: (
        datetime(year, month, 1, tzinfo=ZURICH) + timedelta(seconds=seconds)
    ),
    st.integers(min_value=2026, max_value=2027),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=-4 * 86_400, max_value=3 * 3600),
)
"""Instants from four days before the start of a Zurich month to three hours after it:
the 28th to the 31st, and both sides of the start of the month in Zurich and in UTC."""

INSTANTS = st.one_of(
    ENDS_OF_MONTHS,
    st.integers(min_value=0, max_value=2 * 365 * 86_400).map(
        lambda seconds: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    ),
)
"""The end of a month, or any second of the two years from 2026 on."""


@hypothesis_settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(signed_up=INSTANTS, away=INSTANTS)
async def test_a_user_is_owed_one_grant_for_every_month_that_has_passed_since_the_sign_up(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    signed_up: datetime,
    away: datetime,
) -> None:
    """Whenever somebody signs up and whenever they come back. Every example is a user
    of its own in the one database of the test."""
    now = max(signed_up, away)
    clock.set(signed_up)
    user = await factory.user()
    clock.set(now)
    owed = months_owed(signed_up, now)

    assert await grant_owed_months(container.grants, container, user) == len(owed)
    assert await grant_owed_months(container.grants, container, user) == 0

    assert await periods_of(container, user) == owed
    assert await factory.balance_micro(user) == len(owed) * MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_two_upkeeps_at_once_book_every_month_once(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    user = await factory.user()
    clock.set(datetime(2027, 1, 20, tzinfo=ZURICH))

    booked = await asyncio.gather(
        *(grant_owed_months(container.grants, container, user) for _ in range(4))
    )

    assert sorted(booked) == [0, 0, 0, 3]
    assert await periods_of(container, user) == ["2026-11", "2026-12", "2027-01"]
    assert await factory.balance_micro(user) == 3 * MONTHLY
    await assert_books_balance(container.uow_factory)


async def test_a_monthly_grant_of_nothing_is_recorded_and_posts_nothing(
    container: Container, settings: Settings, factory: Factory, clock: FrozenClock
) -> None:
    """MONTHLY_GRANT_CR=0: the rows are there, so the months are not owed again."""
    user = await factory.user()
    clock.set(SECOND_DUE)
    grants = grants_with(container, settings, monthly_grant_micro=0)

    assert await grant_owed_months(grants, container, user) == 2
    assert await grant_owed_months(grants, container, user) == 0

    monthly = [g for g in await grants_of(container, user) if g.period != SIGNUP_GRANT_PERIOD]
    assert [(grant.period, grant.entry_id) for grant in monthly] == [
        ("2026-11", None),
        ("2026-12", None),
    ]
    assert await count_rows(container.uow_factory, JournalEntry) == 0
    assert await grant_owed_months(container.grants, container, user) == 0, "not owed twice"
    await assert_books_balance(container.uow_factory)


# --- The next grant --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "next_grant_at"),
    [
        (TEST_NOW, FIRST_DUE),
        (datetime(2026, 11, 1, tzinfo=ZURICH), FIRST_DUE),
        (FIRST_DUE - TICK, FIRST_DUE),
        (FIRST_DUE, SECOND_DUE),
        (datetime(2026, 12, 15, 12, 0, tzinfo=UTC), THIRD_DUE),
        (datetime(2027, 3, 31, 22, 0, tzinfo=UTC), datetime(2027, 4, 9, 14, 0, tzinfo=ZURICH)),
    ],
    ids=[
        "at-the-sign-up",
        "on-the-first-of-the-next-month",
        "the-instant-before-the-first-grant",
        "as-the-first-grant-falls-due",
        "between-the-second-and-the-third",
        "in-summer-time",
    ],
)
async def test_the_next_grant_of_a_user_falls_due_on_the_next_monthly_return_of_their_sign_up(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    now: datetime,
    next_grant_at: datetime,
) -> None:
    """A grant that falls due this very instant is owed, so the next is the one after."""
    user = await factory.user()
    clock.set(now)

    assert container.grants.next_grant_at(user) == next_grant_at


async def test_the_next_grant_is_asked_per_user(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    early = await factory.user()
    clock.set(datetime(2026, 10, 31, 9, 0, tzinfo=ZURICH))
    on_the_31st = await factory.user()

    assert container.grants.next_grant_at(early) == FIRST_DUE
    assert container.grants.next_grant_at(on_the_31st) == datetime(
        2026, 11, 30, 9, 0, tzinfo=ZURICH
    )


# --- The admin's run -------------------------------------------------------------------


def run_report(run: MonthlyGrantRunResponse) -> tuple[int, int, int]:
    return run.users_checked, run.users_failed, run.grants_booked


async def test_the_admin_run_books_what_every_active_user_is_owed_and_reports_it(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    since_october = await factory.user()
    suspended = await factory.user(status=UserStatus.SUSPENDED)
    clock.set(FIRST_DUE + timedelta(days=3))
    since_november = await factory.user()
    clock.set(SECOND_DUE + timedelta(days=4))

    run = await container.grants.run_monthly(factory.identity(admin))

    # The admin is a user like any other: two months for them and for the user of
    # October, one for the user of 12 November, none for the suspended one.
    assert run_report(run) == (3, 0, 5)
    assert run.total_cr == to_cr(5 * MONTHLY)
    assert await periods_of(container, admin) == ["2026-11", "2026-12"]
    assert await periods_of(container, since_october) == ["2026-11", "2026-12"]
    assert await periods_of(container, since_november) == ["2026-12"]
    assert await periods_of(container, suspended) == []
    assert await factory.balance_micro(since_october) == 2 * MONTHLY
    assert await factory.balance_micro(since_november) == MONTHLY
    assert len(await entries(container, JournalKind.MONTHLY_GRANT)) == 5
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) == -5 * MONTHLY

    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action) == (admin.id, AuditAction.MONTHLY_GRANT_RUN)
    assert (audited.target, audited.before) == ("grants:monthly", None)
    assert audited.after == {
        "users_checked": 3,
        "users_failed": 0,
        "grants_booked": 5,
        "total_micro": 5 * MONTHLY,
    }
    assert audited.created_at == clock.now()
    await assert_books_balance(container.uow_factory)


async def test_suspended_users_get_no_monthly_grant_from_the_run(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    suspended = await factory.user(status=UserStatus.SUSPENDED, balance_micro=SIGNUP)
    clock.set(SECOND_DUE)

    run = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(run) == (1, 0, 2), "only the admin was looked at"
    assert await periods_of(container, suspended) == []
    assert await factory.balance_micro(suspended) == SIGNUP
    await assert_books_balance(container.uow_factory)


async def test_a_second_run_books_nothing_and_is_audited_all_the_same(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    await factory.user()
    clock.set(FIRST_DUE)
    first = await container.grants.run_monthly(factory.identity(admin))
    booked = await count_rows(container.uow_factory, JournalEntry)

    second = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(first) == (2, 0, 2)
    assert run_report(second) == (2, 0, 0)
    assert second.total_cr == to_cr(0)
    assert await count_rows(container.uow_factory, JournalEntry) == booked
    assert [row.after for row in await audit_rows(container)] == [
        {"users_checked": 2, "users_failed": 0, "grants_booked": 2, "total_micro": 2 * MONTHLY},
        {"users_checked": 2, "users_failed": 0, "grants_booked": 0, "total_micro": 0},
    ]
    await assert_books_balance(container.uow_factory)


async def test_two_runs_at_once_book_every_grant_once(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, second_admin = await factory.admin(), await factory.admin(1)
    for _ in range(4):
        await factory.user()
    clock.set(SECOND_DUE)

    runs = await asyncio.gather(
        container.grants.run_monthly(factory.identity(admin)),
        container.grants.run_monthly(factory.identity(second_admin)),
    )

    assert [run.users_checked for run in runs] == [6, 6]
    assert [run.users_failed for run in runs] == [0, 0]
    assert sum(run.grants_booked for run in runs) == 6 * 2
    assert len(await entries(container, JournalKind.MONTHLY_GRANT)) == 6 * 2
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) == -12 * MONTHLY
    assert len(await audit_rows(container)) == 2
    await assert_books_balance(container.uow_factory)


async def test_the_run_locks_a_users_account_before_it_touches_their_grants(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Lock order: the user's account first. A run that wrote a grant and only then
    waited for the account would hold `vis:issuance` while it waits, and a code that
    the same user redeems holds the account while it waits for `vis:issuance`."""
    admin = await factory.admin()
    clock.set(FIRST_DUE)

    with recorded_statements(container) as statements:
        run = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(run) == (1, 0, 1)
    account_locks = [
        index
        for index, statement in enumerate(statements)
        if "FROM accounts" in statement and "FOR NO KEY UPDATE" in statement
    ]
    about_grants = [index for index, statement in enumerate(statements) if "grants" in statement]
    assert account_locks, statements
    assert about_grants, statements
    assert "accounts.owner_id" in statements[account_locks[0]], "the user's own account"
    assert account_locks[0] < about_grants[0]
    await assert_books_balance(container.uow_factory)


async def test_a_user_whose_grants_fail_costs_only_that_user_and_a_repeat_retries_them(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The second of three users fails after their first month was booked. That month
    is rolled back with the rest of their transaction, and the third is still served."""
    admin = await factory.admin()
    unlucky = await factory.user()
    after_them = await factory.user()
    clock.set(SECOND_DUE)
    post = container.ledger.post
    posted_for_the_unlucky: list[str] = []

    async def post_or_fail(uow: UnitOfWork, **entry: Any) -> JournalEntry:
        key: str = entry["idempotency_key"]
        if str(unlucky.id) in key:
            posted_for_the_unlucky.append(key)
            if len(posted_for_the_unlucky) == 2:
                raise RuntimeError("the second month does not go through")
        return await post(uow, **entry)

    with monkeypatch.context() as patched, caplog.at_level(logging.ERROR, "app.services.grants"):
        patched.setattr(container.ledger, "post", post_or_fail)
        run = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(run) == (3, 1, 4)
    assert run.total_cr == to_cr(4 * MONTHLY)
    assert len(posted_for_the_unlucky) == 2, "the first month was booked before the failure"
    assert await periods_of(container, unlucky) == []
    assert await factory.balance_micro(unlucky) == 0
    assert await periods_of(container, after_them) == ["2026-11", "2026-12"]
    (logged,) = [record for record in caplog.records if record.name == "app.services.grants"]
    assert str(unlucky.id) in logged.getMessage()
    assert logged.exc_info is not None, "with its traceback"
    assert (await audit_rows(container))[-1].after == {
        "users_checked": 3,
        "users_failed": 1,
        "grants_booked": 4,
        "total_micro": 4 * MONTHLY,
    }

    again = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(again) == (3, 0, 2)
    assert await periods_of(container, unlucky) == ["2026-11", "2026-12"]
    assert await factory.balance_micro(unlucky) == 2 * MONTHLY
    await assert_books_balance(container.uow_factory)


@pytest.fixture
async def impatient(settings: Settings, clock: FrozenClock) -> AsyncIterator[Container]:
    """The application on a connection pool of its own, built with a lock timeout of
    `IMPATIENT_MS`. Its database is the one every other fixture uses."""
    postgres = settings.postgres.model_copy(update={"lock_timeout_ms": IMPATIENT_MS})
    container = Container.build(settings.model_copy(update={"postgres": postgres}), clock=clock)
    yield container
    await container.stop()


async def test_a_user_whose_lock_times_out_counts_as_failed_and_the_run_goes_on(
    impatient: Container,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Somebody else holds the account of the second of three users for longer than
    POSTGRES_LOCK_TIMEOUT_MS: a settlement, say. Postgres is up, so the run does not
    end there; that user waits for the next run."""
    admin = await factory.admin()
    busy = await factory.user()
    after_them = await factory.user()
    clock.set(FIRST_DUE)

    async with owner_engine.connect() as holder:
        await holder.execute(
            text("SELECT id FROM accounts WHERE owner_id = :user_id FOR UPDATE"),
            {"user_id": busy.id},
        )
        with caplog.at_level(logging.WARNING, "app.services.grants"):
            run = await impatient.grants.run_monthly(factory.identity(admin))

    assert run_report(run) == (3, 1, 2)
    assert await periods_of(container, busy) == []
    assert await periods_of(container, admin) == ["2026-11"]
    assert await periods_of(container, after_them) == ["2026-11"]
    (logged,) = [record for record in caplog.records if record.name == "app.services.grants"]
    assert logged.levelno == logging.WARNING
    assert str(busy.id) in logged.getMessage()
    (audited,) = await audit_rows(container)
    assert audited.after == {
        "users_checked": 3,
        "users_failed": 1,
        "grants_booked": 2,
        "total_micro": 2 * MONTHLY,
    }

    # The holder is gone and its lock with it.
    again = await impatient.grants.run_monthly(factory.identity(admin))

    assert run_report(again) == (3, 0, 1)
    assert await periods_of(container, busy) == ["2026-11"]
    await assert_books_balance(container.uow_factory)


class DriverError(Exception):
    """Stands in for the driver's error, which carries Postgres' SQLSTATE."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


def ended_by_postgres(sqlstate: str) -> DBAPIError:
    """A transaction Postgres ended for another, on a connection that is whole."""
    return OperationalError("UPDATE accounts", {}, DriverError(sqlstate))


@pytest.mark.parametrize(
    "error",
    [
        ended_by_postgres("40P01"),
        ended_by_postgres("40001"),
        PoolTimeoutError("QueuePool limit reached, connection timed out"),
    ],
    ids=["deadlock-victim", "serialization-failure", "pool-exhausted"],
)
async def test_a_user_whose_transaction_did_not_get_through_counts_as_failed_and_the_run_goes_on(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Postgres ended the transaction of the second of three users for another, or the
    pool had no connection free for it. Postgres is up, so the run does not end there,
    as it does not for a lock that was not granted in time: that user waits for the
    next run, and a warning says so without a traceback."""
    admin = await factory.admin()
    unlucky = await factory.user()
    after_them = await factory.user()
    clock.set(FIRST_DUE)
    grant_owed = container.grants.grant_owed_months
    assert database_errors.is_transient(error)

    async def fail_for_the_unlucky(uow: UnitOfWork, user: User, account: Account) -> int:
        if user.id == unlucky.id:
            raise error
        return await grant_owed(uow, user, account)

    with monkeypatch.context() as patched, caplog.at_level(logging.WARNING, "app.services.grants"):
        patched.setattr(container.grants, "grant_owed_months", fail_for_the_unlucky)
        run = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(run) == (3, 1, 2)
    assert await periods_of(container, unlucky) == []
    assert await periods_of(container, admin) == ["2026-11"]
    assert await periods_of(container, after_them) == ["2026-11"]
    (logged,) = [record for record in caplog.records if record.name == "app.services.grants"]
    assert logged.levelno == logging.WARNING
    assert str(unlucky.id) in logged.getMessage()
    assert logged.exc_info is None

    again = await container.grants.run_monthly(factory.identity(admin))

    assert run_report(again) == (3, 0, 1)
    assert await periods_of(container, unlucky) == ["2026-11"]
    await assert_books_balance(container.uow_factory)


async def test_a_lost_connection_ends_the_run_instead_of_failing_every_user(
    impatient: Container,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres drops the connection while the second of three users is served. That is
    nobody's failure to count: the error leaves the run, to become the API's 503. What
    was booked before it stays booked, and no audit row claims a run that did not end."""
    admin = await factory.admin()
    cut_off = await factory.user()
    after_them = await factory.user()
    clock.set(FIRST_DUE)
    grant_owed = impatient.grants.grant_owed_months

    async def lose_the_connection(uow: UnitOfWork, user: User, account: Account) -> int:
        if user.id == cut_off.id:
            backend = await uow.session.scalar(text("SELECT pg_backend_pid()"))
            async with owner_engine.connect() as superuser:
                await superuser.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})
        return await grant_owed(uow, user, account)

    with monkeypatch.context() as patched:
        patched.setattr(impatient.grants, "grant_owed_months", lose_the_connection)
        with pytest.raises(DBAPIError) as lost:
            await impatient.grants.run_monthly(factory.identity(admin))

    assert database_errors.is_unavailable(lost.value)
    assert not database_errors.is_transient(lost.value)
    assert await periods_of(container, admin) == ["2026-11"]
    assert await periods_of(container, cut_off) == []
    assert await periods_of(container, after_them) == []
    assert await audit_rows(container) == []

    # The same run, sent again, books what is left and nothing twice.
    again = await impatient.grants.run_monthly(factory.identity(admin))

    assert run_report(again) == (3, 0, 2)
    await assert_books_balance(container.uow_factory)


# --- Through the route -------------------------------------------------------------------


async def test_the_route_runs_the_grants_for_an_admin_and_answers_with_the_report(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    clock: FrozenClock,
) -> None:
    admin = await factory.admin()
    user = await factory.user()
    clock.set(SECOND_DUE)
    as_user(admin)

    response = await client.post("/api/admin/grants/monthly")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "users_checked": 2,
        "users_failed": 0,
        "grants_booked": 4,
        "total_cr": format_cr(4 * MONTHLY),
    }
    assert await factory.balance_micro(user) == 2 * MONTHLY
    again = await client.post("/api/admin/grants/monthly")
    assert (again.json()["grants_booked"], again.json()["total_cr"]) == (0, "0")
    await assert_books_balance(container.uow_factory)


async def test_the_route_is_for_admins_and_books_nothing_for_anybody_else(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    clock: FrozenClock,
) -> None:
    user, barista = await factory.user(), await factory.barista()
    clock.set(SECOND_DUE)

    for somebody in (user, barista):
        as_user(somebody)
        response = await client.post("/api/admin/grants/monthly")
        assert (response.status_code, response.json()["code"]) == (403, "FORBIDDEN")

    assert await periods_of(container, user) == []
    assert await audit_rows(container) == []
