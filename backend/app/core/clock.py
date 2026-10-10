"""Time as an injectable dependency.

Services never call `datetime.now()` themselves; they ask a `Clock`. Tests swap in a
frozen clock to exercise expiry, monthly grants and market deadlines deterministically.
"""

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


def first_of_next_month(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def start_of_month(instant: datetime, timezone: ZoneInfo) -> datetime:
    """First instant of the calendar month containing `instant`, in the business timezone."""
    local = instant.astimezone(timezone)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def start_of_day(instant: datetime, timezone: ZoneInfo) -> datetime:
    local = instant.astimezone(timezone)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)
