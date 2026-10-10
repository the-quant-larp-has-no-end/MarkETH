"""How `QuoteHistoryStore.read` asks ScyllaDB for a chart (SPEC §9), without a node.

A read is bounded: it names how many points it wants and gets the newest ones. The
store asks one partition a time, the newest day first, and stops once it has enough.
What it sends, in which order and with which limit, is proven here against a client
kept in memory, which answers the store's SELECT the way a partition would.

That the statement itself means the same to a real node is proven in
`tests/integration/integrations/test_quote_history.py` (run with `-m scylla`).
"""

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.config import ScyllaSettings, Settings
from app.integrations.scylla.client import Row, ScyllaClient, ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from tests.support.settings import load_base_settings

MIDNIGHT: Final[datetime] = datetime(2026, 10, 10, tzinfo=UTC)
DAY: Final[timedelta] = timedelta(days=1)
HOUR: Final[timedelta] = timedelta(hours=1)
MILLISECOND: Final[timedelta] = timedelta(milliseconds=1)

MARKET: Final[UUID] = uuid7()
ELSEWHERE: Final[UUID] = uuid7()


class Partitions(ScyllaClient):
    """Stands where `ScyllaClient` stands for a read: the rows of
    `quotes_by_market_day`, kept in memory.

    `execute` answers the one SELECT the store sends, as a node would: the rows of
    one market and one day whose `ts` lies in the range, newest first, cut at the
    LIMIT. `asked` holds the day and the LIMIT of every statement, in order.
    `seconds_per_statement` makes each of them take that long, and `fails` makes them
    raise what the client raises when the node does not answer.
    """

    def __init__(self, points: Sequence[QuotePoint] = ()) -> None:
        # The parent's constructor opens a thread pool for a driver that is not used
        # here, so it is left out on purpose.
        self.points = list(points)
        self.asked: list[tuple[date, int]] = []
        self.seconds_per_statement = 0.0
        self.fails = False

    async def execute(
        self,
        cql: str,
        parameters: Sequence[Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> list[Row]:
        assert parameters is not None
        assert "ORDER BY ts DESC, version DESC LIMIT %s" in cql, "newest first, and bounded"
        assert cql.count("%s") == len(parameters) == 5
        market_id, day, since_ms, until_ms, limit = parameters
        self.asked.append((day, limit))
        if self.seconds_per_statement:
            await asyncio.sleep(self.seconds_per_statement)
        if self.fails:
            raise ScyllaUnavailableError("the stand-in for ScyllaDB did not answer")
        assert limit >= 1, "a node refuses a LIMIT that is not positive"
        matching = [
            point
            for point in self.points
            if point.market_id == market_id
            and point.ts.date() == day
            and since_ms <= _epoch_ms(point.ts) <= until_ms
        ]
        matching.sort(key=lambda point: (point.ts, point.version), reverse=True)
        return [_row(point) for point in matching[:limit]]


def _epoch_ms(instant: datetime) -> int:
    return (instant - datetime(1970, 1, 1, tzinfo=UTC)) // MILLISECOND


def _row(point: QuotePoint) -> Row:
    """A point as the driver hands a row over: the instant without a timezone, in UTC."""
    return {
        "ts": point.ts.astimezone(UTC).replace(tzinfo=None),
        "version": point.version,
        "p_yes_micro": point.p_yes_micro,
        "q_yes_micro": point.q_yes_micro,
        "q_no_micro": point.q_no_micro,
        "trade_id": point.trade_id,
    }


def point(ts: datetime, version: int, market_id: UUID = MARKET) -> QuotePoint:
    return QuotePoint(
        market_id=market_id,
        ts=ts,
        version=version,
        p_yes_micro=500_000 + version,
        q_yes_micro=version * 1_000_000,
        q_no_micro=0,
        trade_id=None,
    )


def spread(per_day: Sequence[int], start: datetime = MIDNIGHT) -> list[QuotePoint]:
    """Points on consecutive days from `start`: `per_day[n]` of them on day n, a
    minute apart from its first minute, with versions that go on rising."""
    points: list[QuotePoint] = []
    for day, count in enumerate(per_day):
        for minute in range(count):
            ts = start + day * DAY + timedelta(minutes=minute + 1)
            points.append(point(ts, version=len(points)))
    return points


def store_on(node: Partitions, settings: ScyllaSettings | None = None) -> QuoteHistoryStore:
    return QuoteHistoryStore(node, settings or load_base_settings().scylla)


def days(first: datetime, count: int) -> list[date]:
    """`count` days ending with the day of `first`, the newest first."""
    return [(first - back * DAY).date() for back in range(count)]


async def test_a_read_returns_the_newest_points_of_its_range_oldest_first() -> None:
    points = spread([3, 2, 4])
    node = Partitions([*points, point(MIDNIGHT + HOUR, 77, ELSEWHERE)])
    store = store_on(node)
    whole = (MIDNIGHT, MIDNIGHT + 3 * DAY)

    assert await store.read(MARKET, *whole, limit=100) == points
    assert await store.read(MARKET, *whole, limit=9) == points
    assert await store.read(MARKET, *whole, limit=5) == points[-5:]
    assert await store.read(MARKET, *whole, limit=1) == points[-1:]


async def test_the_newest_day_is_asked_first_and_no_day_once_the_limit_is_reached() -> None:
    """A market that trades a lot costs one statement: its newest day fills the limit."""
    points = spread([40, 40, 40])
    node = Partitions(points)

    read = await store_on(node).read(MARKET, MIDNIGHT, MIDNIGHT + 2 * DAY + 12 * HOUR, limit=25)

    assert read == points[-25:]
    assert node.asked == [((MIDNIGHT + 2 * DAY).date(), 25)]


async def test_each_older_day_is_asked_for_what_is_still_missing_and_no_more() -> None:
    """Whatever the days hold, ScyllaDB is asked for at most `limit` rows in all: each
    statement asks for what the ones before it left open."""
    points = spread([40, 3, 0, 5, 2])
    node = Partitions(points)
    newest = MIDNIGHT + 4 * DAY

    read = await store_on(node).read(MARKET, MIDNIGHT, newest + 12 * HOUR, limit=20)

    assert read == points[-20:]
    assert node.asked == list(zip(days(newest, 5), [20, 18, 13, 13, 10], strict=True))
    assert len(read) == 20, "ten of the forty points of the oldest day"


async def test_a_quiet_market_is_asked_day_by_day_back_to_the_start_of_its_range() -> None:
    points = spread([1, 0, 0, 2, 0, 0, 1])
    node = Partitions(points)
    newest = MIDNIGHT + 6 * DAY

    read = await store_on(node).read(MARKET, MIDNIGHT, newest + 12 * HOUR, limit=5_000)

    assert read == points
    assert [day for day, _ in node.asked] == days(newest, 7)
    assert [limit for _, limit in node.asked] == [5_000, 4_999, 4_999, 4_999, 4_997, 4_997, 4_997]


async def test_a_read_stays_inside_its_range_to_the_millisecond() -> None:
    before = point(MIDNIGHT + HOUR - MILLISECOND, 1)
    first = point(MIDNIGHT + HOUR, 2)
    inside = point(MIDNIGHT + DAY + HOUR, 3)
    last = point(MIDNIGHT + 2 * DAY + HOUR, 4)
    after = point(MIDNIGHT + 2 * DAY + HOUR + MILLISECOND, 5)
    node = Partitions([before, first, inside, last, after])

    read = await store_on(node).read(MARKET, first.ts, last.ts, limit=10)

    assert read == [first, inside, last]
    assert [day for day, _ in node.asked] == days(last.ts, 3)


async def test_two_points_of_one_instant_come_back_in_the_order_of_their_versions() -> None:
    same = MIDNIGHT + HOUR
    points = [point(same, 2), point(same, 3), point(same + MILLISECOND, 4)]
    node = Partitions(points[::-1])
    store = store_on(node)

    assert await store.read(MARKET, MIDNIGHT, MIDNIGHT + DAY, limit=10) == points
    assert await store.read(MARKET, MIDNIGHT, MIDNIGHT + DAY, limit=2) == points[1:]


async def test_a_range_that_ends_before_it_starts_asks_nothing_and_holds_nothing() -> None:
    node = Partitions(spread([3]))

    assert await store_on(node).read(MARKET, MIDNIGHT + 2 * DAY, MIDNIGHT, limit=10) == []
    assert node.asked == []


@pytest.mark.parametrize("limit", [0, -1])
async def test_a_read_that_asks_for_no_point_is_refused_before_anything_is_sent(
    limit: int,
) -> None:
    node = Partitions(spread([3]))

    with pytest.raises(ValueError, match="at least one point"):
        await store_on(node).read(MARKET, MIDNIGHT, MIDNIGHT + DAY, limit=limit)

    assert node.asked == []


async def test_an_instant_without_a_timezone_is_refused_before_anything_is_sent() -> None:
    node = Partitions(spread([3]))
    naive = datetime(2026, 10, 10, 9, 0)  # noqa: DTZ001

    with pytest.raises(ValueError, match="timezone-aware"):
        await store_on(node).read(MARKET, naive, MIDNIGHT + DAY, limit=10)

    assert node.asked == []


async def test_a_statement_that_gets_no_answer_ends_the_read_with_that_error() -> None:
    """The caller reads Postgres instead; the days after the failed one are not asked."""
    node = Partitions(spread([1, 1, 1]))
    node.fails = True

    with pytest.raises(ScyllaUnavailableError, match="did not answer"):
        await store_on(node).read(MARKET, MIDNIGHT, MIDNIGHT + 3 * DAY, limit=10)

    assert len(node.asked) == 1


async def test_a_read_of_many_days_ends_within_one_request_timeout_as_a_whole(
    base_settings: Settings,
) -> None:
    """Ninety days of a node that takes its time over each: no statement is too slow
    by itself, the read as a whole is. It ends after one request timeout, as
    `ScyllaUnavailableError`, and nothing is asked afterwards."""
    settings = base_settings.scylla.model_copy(update={"request_timeout_ms": 100})
    node = Partitions(spread([1] * 90))
    node.seconds_per_statement = 0.02

    started = time.perf_counter()
    with pytest.raises(ScyllaUnavailableError, match=r"within 0\.1 s"):
        await store_on(node, settings).read(MARKET, MIDNIGHT, MIDNIGHT + 90 * DAY, limit=5_000)
    waited = time.perf_counter() - started

    assert 0.1 <= waited < 1.0, "far from the 1.8 s the ninety statements would take"
    asked = len(node.asked)
    assert 1 <= asked < 90
    await asyncio.sleep(0.05)
    assert len(node.asked) == asked, "the read went on after it had given up"


@given(
    per_day=st.lists(st.integers(min_value=0, max_value=30), min_size=1, max_size=8),
    limit=st.integers(min_value=1, max_value=120),
)
async def test_whatever_the_days_hold_a_read_returns_the_newest_points_and_asks_for_no_more(
    per_day: list[int], limit: int
) -> None:
    """For any spread of points over the days of a range and any limit: the result is
    the newest `limit` points, oldest first; the days are asked newest first, each
    once at most; and the limits of all statements that returned rows add up to no
    more than `limit` beyond what they returned."""
    points = spread(per_day)
    node = Partitions(points)
    newest = MIDNIGHT + (len(per_day) - 1) * DAY

    read = await store_on(node).read(MARKET, MIDNIGHT, newest + 23 * HOUR, limit=limit)

    assert read == points[-limit:]
    asked_days = [day for day, _ in node.asked]
    assert asked_days == days(newest, len(per_day))[: len(asked_days)]
    returned = 0
    for day, asked_for in node.asked:
        assert asked_for == limit - returned, "what is still missing, and no more"
        returned += min(asked_for, sum(1 for held in points if held.ts.date() == day))
    assert returned == len(read) <= limit
    if len(asked_days) < len(per_day):
        assert returned == limit, "it stopped early only because it had enough"
