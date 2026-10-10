"""When a user's monthly grants fall due (SPEC §2, Appendix A.3).

`app.services.grants.monthly_grant` is calendar arithmetic and nothing else, so it is
checked here without a database: a grant a month, on the day and at the time of day of
the sign-up in `APP_TIMEZONE`, on the last day of a month that is too short, and through
the two days a year on which the clocks change. `tests/integration/test_grants.py`
proves that what falls due is booked.

The expected instants are written out as Zurich has them, or worked out here by counting
months, never by asking the functions under test.
"""

from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Final
from uuid import uuid7
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.clock import add_months, months_later
from app.core.config import Settings
from app.core.enums import UserRole, UserStatus
from app.models import User
from app.repositories.unit_of_work import UnitOfWork
from app.services.audit import AuditService
from app.services.grants import GrantService, MonthlyGrant, monthly_grant
from app.services.ledger import LedgerService
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import DEFAULT_DEGREE_LEVEL, DEFAULT_DEGREE_PROGRAMME
from tests.support.settings import load_base_settings

ZURICH: Final[ZoneInfo] = load_base_settings().app.timezone

HOUR: Final[timedelta] = timedelta(hours=1)

YEARS: Final[range] = range(2025, 2035)
"""The years the grants of these tests fall due in."""


def last_sunday(year: int, month: int) -> date:
    last_day = date(year, month, monthrange(year, month)[1])
    return last_day - timedelta(days=(last_day.weekday() + 1) % 7)


CLOCKS_FORWARD: Final[tuple[date, ...]] = tuple(last_sunday(year, 3) for year in YEARS)
"""The last Sundays of March: in Zurich the clocks go from 02:00 to 03:00, and the
times in between do not exist that day."""

CLOCKS_BACK: Final[tuple[date, ...]] = tuple(last_sunday(year, 10) for year in YEARS)
"""The last Sundays of October: the clocks go from 03:00 back to 02:00, and the times
in between come twice that day."""


def zurich(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """An instant by what the clocks in Zurich show."""
    return datetime(year, month, day, hour, minute, tzinfo=ZURICH)


def in_zurich(instant: datetime) -> datetime:
    return instant.astimezone(ZURICH)


def wall_clock(instant: datetime) -> datetime:
    """What the clocks in Zurich show at `instant`, as a date and a time without a zone."""
    return in_zurich(instant).replace(tzinfo=None)


def month_number(day: date | datetime) -> int:
    """The calendar months since year 0: one more is the next month."""
    return day.year * 12 + day.month - 1


def period_of(month: int) -> str:
    """`YYYY-MM` of a `month_number`."""
    return f"{month // 12:04d}-{month % 12 + 1:02d}"


def is_skipped(wall: datetime) -> bool:
    """Whether the clocks in Zurich never show `wall`: it lies in the hour a spring
    change leaves out."""
    return wall.date() in CLOCKS_FORWARD and time(2) <= wall.time() < time(3)


def schedule(signed_up: datetime, grants: int) -> list[MonthlyGrant]:
    return [monthly_grant(signed_up, number, ZURICH) for number in range(1, grants + 1)]


def test_the_tests_run_on_the_calendar_of_zurich_and_know_its_clock_changes() -> None:
    """What the tests below stand on: on each of these Sundays one minute leads from
    01:59 to 03:00, or 02:30 comes twice, an hour apart. No other day has either."""
    assert ZURICH.key == "Europe/Zurich"
    assert (CLOCKS_FORWARD[1], CLOCKS_BACK[1]) == (date(2026, 3, 29), date(2026, 10, 25))
    assert (CLOCKS_FORWARD[2], CLOCKS_BACK[2]) == (date(2027, 3, 28), date(2027, 10, 31))
    day = date(YEARS.start, 1, 1)
    while day.year in YEARS:
        before = datetime.combine(day, time(1, 59), tzinfo=ZURICH).astimezone(UTC)
        after = datetime.combine(day, time(3), tzinfo=ZURICH).astimezone(UTC)
        half_past_two = datetime.combine(day, time(2, 30), tzinfo=ZURICH)
        twice = half_past_two.replace(fold=1).astimezone(UTC) != half_past_two.astimezone(UTC)

        assert (after - before == timedelta(minutes=1)) == (day in CLOCKS_FORWARD), day
        assert (twice and after - before == 2 * HOUR + timedelta(minutes=1)) == (
            day in CLOCKS_BACK
        ), day
        day += timedelta(days=1)


# --- A month ahead on the calendar -------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "months", "moved"),
    [
        (date(2026, 10, 9), 1, date(2026, 11, 9)),
        (date(2026, 10, 9), 3, date(2027, 1, 9)),
        (date(2026, 12, 31), 1, date(2027, 1, 31)),
        (date(2027, 1, 31), 1, date(2027, 2, 28)),
        (date(2027, 1, 31), 2, date(2027, 3, 31)),
        (date(2027, 1, 31), 3, date(2027, 4, 30)),
        (date(2028, 1, 31), 1, date(2028, 2, 29)),
        (date(2028, 1, 30), 1, date(2028, 2, 29)),
        (date(2028, 1, 29), 1, date(2028, 2, 29)),
        (date(2027, 1, 29), 1, date(2027, 2, 28)),
        (date(2028, 2, 29), 12, date(2029, 2, 28)),
        (date(2028, 2, 29), 48, date(2032, 2, 29)),
        (date(2026, 10, 9), 0, date(2026, 10, 9)),
    ],
    ids=[
        "one-month",
        "over-the-end-of-a-year",
        "december-to-january",
        "the-31st-into-february",
        "the-31st-is-back-in-march",
        "the-31st-into-a-month-of-30-days",
        "the-31st-into-a-leap-february",
        "the-30th-into-a-leap-february",
        "the-29th-into-a-leap-february",
        "the-29th-into-february",
        "the-29th-of-february-a-year-on",
        "the-29th-of-february-four-years-on",
        "no-month-at-all",
    ],
)
def test_a_day_moves_to_the_same_day_of_a_later_month_or_to_its_last_day(
    day: date, months: int, moved: date
) -> None:
    assert add_months(day, months) == moved


def test_an_instant_moves_by_the_calendar_of_the_timezone_and_comes_back_in_utc() -> None:
    """9 October at 14:00 in Zurich is 12:00 UTC in summer time; a month later the same
    time of day in Zurich is 13:00 UTC."""
    moved = months_later(TEST_NOW, 1, ZURICH)

    assert moved == datetime(2026, 11, 9, 13, 0, tzinfo=UTC)
    assert moved.utcoffset() == timedelta(0)
    assert wall_clock(moved) == datetime.combine(date(2026, 11, 9), time(14))
    assert months_later(TEST_NOW, 0, ZURICH) == TEST_NOW


# --- The grants of one user, written out --------------------------------------------------


def test_the_grants_follow_the_sign_up_a_month_apart_on_its_day_and_at_its_time() -> None:
    """Signed up on 9 October 2026 at 14:00 in Zurich."""
    first, second, third = schedule(TEST_NOW, 3)

    assert first == MonthlyGrant(number=1, period="2026-11", due_at=zurich(2026, 11, 9, 14))
    assert second == MonthlyGrant(number=2, period="2026-12", due_at=zurich(2026, 12, 9, 14))
    assert third == MonthlyGrant(number=3, period="2027-01", due_at=zurich(2027, 1, 9, 14))
    assert first.due_at.utcoffset() == timedelta(0), "an instant, in UTC"


def test_no_grant_falls_due_on_the_first_of_a_month_unless_that_is_the_day_of_sign_up() -> None:
    assert [wall_clock(grant.due_at).day for grant in schedule(TEST_NOW, 24)] == [9] * 24
    on_a_first = schedule(zurich(2026, 10, 1, 8, 15), 24)
    assert [wall_clock(grant.due_at).day for grant in on_a_first] == [1] * 24


@pytest.mark.parametrize(
    ("signed_up", "due"),
    [
        (
            zurich(2027, 1, 31, 10),
            [zurich(2027, 2, 28, 10), zurich(2027, 3, 31, 10), zurich(2027, 4, 30, 10)],
        ),
        (
            zurich(2027, 1, 30, 10),
            [zurich(2027, 2, 28, 10), zurich(2027, 3, 30, 10), zurich(2027, 4, 30, 10)],
        ),
        (
            zurich(2027, 1, 29, 10),
            [zurich(2027, 2, 28, 10), zurich(2027, 3, 29, 10), zurich(2027, 4, 29, 10)],
        ),
        (
            zurich(2028, 1, 31, 10),
            [zurich(2028, 2, 29, 10), zurich(2028, 3, 31, 10), zurich(2028, 4, 30, 10)],
        ),
        (
            zurich(2028, 2, 29, 10),
            [zurich(2028, 3, 29, 10), zurich(2028, 4, 29, 10), zurich(2028, 5, 29, 10)],
        ),
        (
            zurich(2026, 12, 31, 23, 59),
            [zurich(2027, 1, 31, 23, 59), zurich(2027, 2, 28, 23, 59), zurich(2027, 3, 31, 23, 59)],
        ),
    ],
    ids=[
        "the-31st",
        "the-30th",
        "the-29th",
        "the-31st-before-a-leap-february",
        "the-29th-of-february",
        "the-last-minute-of-a-year",
    ],
)
def test_a_month_without_the_day_of_sign_up_takes_its_last_day_and_the_next_is_back_on_it(
    signed_up: datetime, due: list[datetime]
) -> None:
    """The day is always counted from the sign-up, never from the grant before: a short
    February does not move every later grant to the 28th."""
    assert [grant.due_at for grant in schedule(signed_up, len(due))] == due


@pytest.mark.parametrize(
    ("signed_up", "number", "due_in_utc"),
    [
        # 02:30 does not exist on 28 March 2027: the clocks show 03:30 at that instant.
        (zurich(2027, 2, 28, 2, 30), 1, datetime(2027, 3, 28, 1, 30, tzinfo=UTC)),
        (zurich(2027, 2, 28, 2), 1, datetime(2027, 3, 28, 1, 0, tzinfo=UTC)),
        # 03:00 does: it is the first instant of summer time.
        (zurich(2027, 2, 28, 3), 1, datetime(2027, 3, 28, 1, 0, tzinfo=UTC)),
        (zurich(2027, 2, 28, 1, 59), 1, datetime(2027, 3, 28, 0, 59, tzinfo=UTC)),
        # A month later the time of day is back.
        (zurich(2027, 2, 28, 2, 30), 2, datetime(2027, 4, 28, 0, 30, tzinfo=UTC)),
        # 02:30 comes twice on 25 October 2026: the first of the two, in summer time.
        (zurich(2026, 9, 25, 2, 30), 1, datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),
        (zurich(2026, 9, 25, 2, 30), 2, datetime(2026, 11, 25, 1, 30, tzinfo=UTC)),
        # Signed up in the second of the two hours: a month later 02:30 comes once.
        (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), 1, datetime(2026, 11, 25, 1, 30, tzinfo=UTC)),
        (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), 1, datetime(2026, 11, 25, 1, 30, tzinfo=UTC)),
        # Across both changes the time of day in Zurich stays, and UTC moves by an hour.
        (zurich(2026, 10, 9, 14), 1, datetime(2026, 11, 9, 13, 0, tzinfo=UTC)),
        (zurich(2027, 3, 9, 14), 1, datetime(2027, 4, 9, 12, 0, tzinfo=UTC)),
    ],
    ids=[
        "a-time-the-clocks-skip",
        "the-first-skipped-minute",
        "the-first-minute-after-the-skip",
        "the-last-minute-before-the-skip",
        "the-month-after-a-skipped-time",
        "a-time-the-clocks-show-twice",
        "the-month-after-a-doubled-time",
        "signed-up-in-the-second-of-two-hours",
        "signed-up-in-the-first-of-two-hours",
        "over-the-end-of-summer-time",
        "over-its-start",
    ],
)
def test_the_time_of_day_is_zurichs_through_both_clock_changes(
    signed_up: datetime, number: int, due_in_utc: datetime
) -> None:
    grant = monthly_grant(signed_up, number, ZURICH)

    assert grant.due_at == due_in_utc
    assert grant.period == f"{in_zurich(due_in_utc):%Y-%m}"


def test_the_period_is_the_month_in_zurich_not_the_one_in_utc() -> None:
    """Signed up on 31 October at 23:30 UTC, which is 1 November in Zurich. The first
    grant falls due on 1 December there, while UTC is still in November."""
    signed_up = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)

    first = monthly_grant(signed_up, 1, ZURICH)

    assert wall_clock(signed_up) == datetime.combine(date(2026, 11, 1), time(0, 30))
    assert first.due_at == datetime(2026, 11, 30, 23, 30, tzinfo=UTC)
    assert first.period == "2026-12"


# --- The same, for any sign-up -------------------------------------------------------------

ANY_SECOND = st.integers(min_value=0, max_value=6 * 365 * 86_400).map(
    lambda seconds: datetime(2025, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
)
"""Any second of the six years from 2025 on."""

ENDS_OF_MONTHS = st.builds(
    lambda year, month, day, seconds: (
        datetime(year, month, min(day, monthrange(year, month)[1]), tzinfo=ZURICH)
        + timedelta(seconds=seconds)
    ),
    st.integers(min_value=2025, max_value=2030),
    st.integers(min_value=1, max_value=12),
    st.integers(min_value=28, max_value=31),
    st.integers(min_value=0, max_value=86_399),
)
"""An instant on the 28th to the 31st of a month in Zurich: the days a later month may
not have."""

AROUND_A_CLOCK_CHANGE = st.builds(
    lambda sunday, seconds: (
        datetime.combine(sunday, time(0), tzinfo=ZURICH).astimezone(UTC)
        + timedelta(seconds=seconds)
    ),
    st.sampled_from([*CLOCKS_FORWARD, *CLOCKS_BACK]),
    st.integers(min_value=0, max_value=5 * 3600),
)
"""An instant in the first five hours of a day on which the clocks change: before the
change, in the hour that is skipped or doubled, and after it."""

ON_THE_DAY_OF_A_LATER_CLOCK_CHANGE = st.builds(
    lambda sunday, months_before, seconds: (
        datetime.combine(add_months(sunday, -months_before), time(1), tzinfo=ZURICH).astimezone(UTC)
        + timedelta(seconds=seconds)
    ),
    st.sampled_from([*CLOCKS_FORWARD[1:], *CLOCKS_BACK[1:]]),
    st.integers(min_value=1, max_value=6),
    st.integers(min_value=0, max_value=3 * 3600),
)
"""An instant between 01:00 and 04:00 on the day of the month on which, some months
later, the clocks change: one of its grants falls due in the hour that is skipped or
doubled."""

SIGN_UPS = st.one_of(
    ANY_SECOND, ENDS_OF_MONTHS, AROUND_A_CLOCK_CHANGE, ON_THE_DAY_OF_A_LATER_CLOCK_CHANGE
)

GRANTS: Final[int] = 30
"""How many grants of a sign-up a property looks at: two and a half years of them."""


@given(signed_up=SIGN_UPS)
def test_every_calendar_month_after_the_sign_up_has_exactly_one_grant(signed_up: datetime) -> None:
    """Never two in one calendar month and never a month without one: grant k has the
    period of the k-th month after the sign-up, and its instant lies in that month."""
    month_of_sign_up = month_number(in_zurich(signed_up))
    grants = schedule(signed_up, GRANTS)

    assert [grant.number for grant in grants] == list(range(1, GRANTS + 1))
    assert [grant.period for grant in grants] == [
        period_of(month_of_sign_up + number) for number in range(1, GRANTS + 1)
    ]
    assert len({grant.period for grant in grants}) == GRANTS
    for grant in grants:
        assert f"{in_zurich(grant.due_at):%Y-%m}" == grant.period


@given(signed_up=SIGN_UPS)
def test_every_grant_falls_due_on_the_day_of_sign_up_or_on_the_last_day_of_a_shorter_month(
    signed_up: datetime,
) -> None:
    day_of_sign_up = in_zurich(signed_up).day

    for grant in schedule(signed_up, GRANTS):
        due = in_zurich(grant.due_at)
        last_day = monthrange(due.year, due.month)[1]
        assert due.day == min(day_of_sign_up, last_day)


@given(signed_up=SIGN_UPS)
def test_every_grant_falls_due_at_the_time_of_day_of_the_sign_up(signed_up: datetime) -> None:
    """What the clocks in Zurich show when a grant falls due is what they showed at the
    sign-up. The one exception is a time the clocks skip that day: the grant falls due
    at the instant they show an hour later. A time they show twice is the first of the
    two."""
    time_of_sign_up = wall_clock(signed_up).time()

    for grant in schedule(signed_up, GRANTS):
        due = in_zurich(grant.due_at)
        meant = datetime.combine(due.date(), time_of_sign_up)
        if is_skipped(meant):
            assert wall_clock(grant.due_at) == meant + HOUR
        else:
            assert wall_clock(grant.due_at) == meant
            assert due.fold == 0, "the first of two"
        assert grant.due_at.utcoffset() == timedelta(0)


@given(signed_up=SIGN_UPS)
def test_the_grants_come_one_after_another_about_a_month_apart(signed_up: datetime) -> None:
    """Between two grants lie 28 to 31 days, give or take the hour of a clock change;
    the first is as far from the sign-up."""
    instants = [signed_up, *(grant.due_at for grant in schedule(signed_up, GRANTS))]

    for earlier, later in pairwise(instants):
        assert timedelta(days=28) - HOUR <= later - earlier <= timedelta(days=31) + HOUR


# --- The next grant of a user ---------------------------------------------------------------


def no_unit_of_work() -> UnitOfWork:
    raise AssertionError("when a grant falls due is arithmetic: Postgres is not asked")


def grants_at(base_settings: Settings, now: datetime) -> GrantService:
    """The grant service at `now`, with nothing behind it."""
    clock = FrozenClock(now)
    return GrantService(no_unit_of_work, base_settings, clock, LedgerService(), AuditService(clock))


def signed_up_at(instant: datetime) -> User:
    """A user row that is in no database."""
    return User(
        id=uuid7(),
        email="somebody@ethz.ch",
        name="Some Body",
        username="somebody",
        degree_level=DEFAULT_DEGREE_LEVEL,
        degree_programme=DEFAULT_DEGREE_PROGRAMME,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        policy_accepted_at=instant,
        created_at=instant,
    )


@pytest.mark.parametrize(
    ("signed_up", "now", "next_at"),
    [
        (TEST_NOW, TEST_NOW, zurich(2026, 11, 9, 14)),
        (TEST_NOW, zurich(2026, 11, 1), zurich(2026, 11, 9, 14)),
        (TEST_NOW, zurich(2026, 11, 9, 13, 59), zurich(2026, 11, 9, 14)),
        (TEST_NOW, zurich(2026, 11, 9, 14), zurich(2026, 12, 9, 14)),
        (TEST_NOW, zurich(2026, 12, 15, 12), zurich(2027, 1, 9, 14)),
        (TEST_NOW, zurich(2029, 6, 9, 14, 1), zurich(2029, 7, 9, 14)),
        (zurich(2027, 1, 31, 10), zurich(2027, 2, 1), zurich(2027, 2, 28, 10)),
        (zurich(2027, 1, 31, 10), zurich(2027, 2, 28, 10), zurich(2027, 3, 31, 10)),
        (zurich(2027, 2, 28, 2, 30), zurich(2027, 3, 1), datetime(2027, 3, 28, 1, 30, tzinfo=UTC)),
    ],
    ids=[
        "at-the-sign-up",
        "on-the-first-of-the-next-month",
        "a-minute-before-the-first-grant",
        "as-the-first-grant-falls-due",
        "between-two-grants",
        "years-later",
        "the-31st-before-february",
        "the-31st-after-february",
        "a-time-the-clocks-skip",
    ],
)
def test_the_next_grant_is_the_first_that_falls_due_after_now(
    base_settings: Settings, signed_up: datetime, now: datetime, next_at: datetime
) -> None:
    """A grant that falls due this very instant is owed, so the next is the one
    after it."""
    assert grants_at(base_settings, now).next_grant_at(signed_up_at(signed_up)) == next_at


@given(signed_up=SIGN_UPS, later=st.integers(min_value=0, max_value=3 * 365 * 86_400))
def test_the_next_grant_is_always_ahead_and_never_more_than_a_month_and_an_hour(
    signed_up: datetime, later: int
) -> None:
    now = signed_up + timedelta(seconds=later)

    next_at = grants_at(load_base_settings(), now).next_grant_at(signed_up_at(signed_up))

    assert now < next_at <= now + timedelta(days=31) + HOUR
    due = [grant.due_at for grant in schedule(signed_up, 40)]
    assert next_at in due
    assert next(instant for instant in due if now < instant) == next_at
