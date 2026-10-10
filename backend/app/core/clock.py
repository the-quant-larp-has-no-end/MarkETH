"""Time as an injectable dependency.

Services never call `datetime.now()` themselves; they ask a `Clock`. Tests swap in a
frozen clock to exercise expiry, monthly grants and market deadlines deterministically.
"""

from calendar import monthrange
from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, timezone-aware, in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def local_date(instant: datetime, timezone: ZoneInfo) -> date:
    """Calendar date of an instant in the business timezone (Europe/Zurich by default)."""
    return instant.astimezone(timezone).date()


def month_period(day: date) -> str:
    """`YYYY-MM`, the `grants.period` of a monthly grant (SPEC §2)."""
    return f"{day.year:04d}-{day.month:02d}"


def add_months(day: date, months: int) -> date:
    """`day` moved `months` calendar months ahead: the same day of the month, or the
    last day of that month where it has no such day. From 31 January that is 28 or 29
    February after one month and 31 March after two."""
    months_since_year_zero = day.year * 12 + day.month - 1 + months
    year, month = months_since_year_zero // 12, months_since_year_zero % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def months_later(instant: datetime, months: int, timezone: ZoneInfo) -> datetime:
    """The instant `months` calendar months after `instant` on the calendar of
    `timezone`: the day `add_months` gives, at the same time of day there.

    On the two days a year the clocks change, a time of day can be no single instant.
    One the clocks skip (02:30 on the last Sunday of March in Zurich) is read with the
    offset before the change, which is the instant the clocks show an hour later
    (03:30). One they show twice (02:30 on the last Sunday of October) is the first
    of the two.

    The result is in UTC.
    """
    local = instant.astimezone(timezone)
    moved = datetime.combine(add_months(local.date(), months), local.time(), tzinfo=timezone)
    return moved.replace(fold=0).astimezone(UTC)


def start_of_month(instant: datetime, timezone: ZoneInfo) -> datetime:
    """First instant of the calendar month containing `instant`, in the business timezone."""
    local = instant.astimezone(timezone)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def start_of_day(instant: datetime, timezone: ZoneInfo) -> datetime:
    local = instant.astimezone(timezone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)
