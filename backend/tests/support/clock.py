"""A clock that moves only when a test moves it (backend/CLAUDE.md, "Tests")."""

from datetime import UTC, datetime, timedelta
from typing import Final

TEST_NOW: Final[datetime] = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
"""Where every test's clock starts: a Friday noon in UTC, 14:00 in Zurich, well inside
a month, so nothing crosses a day or month boundary by accident."""


class FrozenClock:
    """Implements `app.core.clock.Clock`. Time stands still between `advance` and `set`.

    Only the application's own notion of time is frozen. Postgres, Redis TTLs and
    `asyncio` timeouts keep running in real time.
    """

    def __init__(self, start: datetime = TEST_NOW) -> None:
        self._now = _aware(start)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward by `delta` and return the new instant."""
        if delta < timedelta(0):
            raise ValueError("the clock only moves forward; use set() to jump back")
        self._now += delta
        return self._now

    def set(self, instant: datetime) -> None:
        """Jump to `instant`, which must be timezone-aware."""
        self._now = _aware(instant)


def _aware(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("the clock needs a timezone-aware instant")
    return instant.astimezone(UTC)
