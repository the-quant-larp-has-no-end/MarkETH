"""The first instant of a calendar day (`app.core.clock.day_start`).

A streak counts calendar days of `APP_TIMEZONE`, and a day ends where the next one
starts. On the two days a year the clocks change, a day is no 24 hours long.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.clock import day_start, local_date, start_of_day

ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")
ONE_DAY: Final[timedelta] = timedelta(days=1)


@pytest.mark.parametrize(
    ("day", "instant"),
    [
        (date(2026, 1, 15), datetime(2026, 1, 14, 23, 0, tzinfo=UTC)),
        (date(2026, 7, 15), datetime(2026, 7, 14, 22, 0, tzinfo=UTC)),
        (date(2026, 3, 29), datetime(2026, 3, 28, 23, 0, tzinfo=UTC)),
        (date(2026, 3, 30), datetime(2026, 3, 29, 22, 0, tzinfo=UTC)),
        (date(2026, 10, 25), datetime(2026, 10, 24, 22, 0, tzinfo=UTC)),
        (date(2026, 10, 26), datetime(2026, 10, 25, 23, 0, tzinfo=UTC)),
    ],
    ids=[
        "winter",
        "summer",
        "the-day-the-clocks-go-forward",
        "the-day-after",
        "the-day-the-clocks-go-back",
        "the-day-after-that",
    ],
)
def test_a_day_of_zurich_starts_at_its_midnight_as_an_instant_in_utc(
    day: date, instant: datetime
) -> None:
    start = day_start(day, ZURICH)

    assert start == instant
    assert start.tzinfo is UTC


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 28), 24), (date(2026, 3, 29), 23), (date(2026, 10, 25), 25)],
    ids=["an-ordinary-day", "the-clocks-go-forward", "the-clocks-go-back"],
)
def test_a_day_ends_where_the_next_one_starts(day: date, hours: int) -> None:
    length = day_start(day + ONE_DAY, ZURICH) - day_start(day, ZURICH)

    assert length == timedelta(hours=hours)


def test_a_day_of_utc_starts_at_midnight_utc() -> None:
    assert day_start(date(2026, 10, 10), ZoneInfo("UTC")) == datetime(2026, 10, 10, tzinfo=UTC)


@given(st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 1, 1)))
def test_the_start_of_a_day_is_its_first_instant(day: date) -> None:
    start = day_start(day, ZURICH)

    assert local_date(start, ZURICH) == day
    assert local_date(start - timedelta(microseconds=1), ZURICH) == day - ONE_DAY
    assert start_of_day(start + timedelta(hours=12), ZURICH) == start
