"""What follows a commit that announced something, and the pace at which the leaderboard
is said to have moved (`app/services/announcer.py`, SPEC §9).

The cached market lists are dropped before an event about a market's price or status
goes out. `leaderboard.changed` goes out at most once per `LEADERBOARD_REFRESH_SECONDS`:
at once for a move after a quiet interval, and once at the end of an interval for all
the moves inside it.

The clock of the pace is the test's: `Timers` stands where the event loop's
`call_later` stands and moves the `FrozenClock` with it.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, get_args
from uuid import UUID

import pytest

from app.core.enums import UserRole, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.integrations.redis.market_cache import MarketCache
from app.schemas.live import LIVE_EVENTS, LeaderboardChangedEvent
from app.services.announcer import (
    DROPS_THE_MARKET_LISTS,
    MOVES_THE_RANKING,
    Announcer,
    LeaderboardPace,
    call_later_on_the_loop,
)
from app.services.live_hub import LiveFrame, LiveHub
from tests.support.clock import FrozenClock
from tests.support.settings import load_base_settings

MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e")
MARKETS: Final[Topic] = Topic(TopicKind.MARKETS)
MARKET: Final[Topic] = Topic(TopicKind.MARKET, MARKET_ID)
LEADERBOARD: Final[Topic] = Topic(TopicKind.LEADERBOARD)

REFRESH_SECONDS: Final[int] = load_base_settings().live.leaderboard_refresh_seconds

ALICE: Final[AuthenticatedUser] = AuthenticatedUser(
    id=UUID(int=1),
    email="alice@ethz.ch",
    name="Alice",
    username="alice",
    degree_programme="BSc Computer Science",
    role=UserRole.USER,
    status=UserStatus.ACTIVE,
)

EVENT_NAMES: Final[frozenset[str]] = frozenset(
    get_args(model.model_fields["type"].annotation)[0] for model in LIVE_EVENTS
)
"""The name of every event of the stream: the one value of each model's `type`."""


@dataclass(frozen=True)
class Body:
    """Stands where an event model stands."""

    type: str

    def model_dump_json(self) -> str:
        return f'{{"type":"{self.type}"}}'


def event(name: str, *topics: Topic) -> LiveEvent:
    return LiveEvent(topics or (MARKETS, MARKET), Audience.everyone(), Body(name))


def own(name: str = "me.changed") -> LiveEvent:
    return LiveEvent((Topic(TopicKind.ME, ALICE.id),), Audience.users([ALICE.id]), Body(name))


class Log:
    """What happened, in order: a cache that was dropped and each event the hub
    published."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def saw(self, published: LiveEvent) -> None:
        self.lines.append(published.payload.type)


class WatchedCache(MarketCache):
    """Stands where the cache of the market lists stands, with no Redis behind it."""

    def __init__(self, log: Log) -> None:
        self._log = log

    async def invalidate_lists(self) -> None:
        # A real drop is a round trip to Redis: other tasks run meanwhile.
        await asyncio.sleep(0)
        self._log.lines.append("lists dropped")


class BrokenCache(MarketCache):
    def __init__(self) -> None:
        pass

    async def invalidate_lists(self) -> None:
        raise RuntimeError("the cache is broken")


@pytest.fixture
def hub(clock: FrozenClock) -> LiveHub:
    return LiveHub(load_base_settings().live, clock)


@pytest.fixture
def log(hub: LiveHub) -> Log:
    log = Log()
    hub.watch(log.saw)
    return log


@asynccontextmanager
async def reading(hub: LiveHub, *topics: Topic) -> AsyncIterator[list[str]]:
    """The names of what a stream of Alice's brings, read in a task of its own."""
    names: list[str] = []

    async def take(frames: AsyncIterator[LiveFrame]) -> None:
        async for frame in frames:
            names.append(frame.event)

    async with hub.open(ALICE, "tab-0001", topics) as frames:
        reader = asyncio.create_task(take(frames))
        try:
            await settled()
            yield names
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def settled() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# --- The hand-over ---------------------------------------------------------------------


def test_what_drops_the_lists_and_what_moves_the_ranking_are_events_of_the_stream() -> None:
    assert frozenset({"market.quote", "market.status"}) == DROPS_THE_MARKET_LISTS
    assert frozenset({"market.quote", "market.status", "me.changed"}) == MOVES_THE_RANKING
    assert DROPS_THE_MARKET_LISTS | MOVES_THE_RANKING <= EVENT_NAMES
    assert "leaderboard.changed" in EVENT_NAMES - MOVES_THE_RANKING


@pytest.mark.parametrize("name", sorted(DROPS_THE_MARKET_LISTS))
async def test_the_cached_market_lists_are_dropped_before_a_market_event_goes_out(
    hub: LiveHub, log: Log, name: str
) -> None:
    announcer = Announcer(hub, WatchedCache(log))

    async with reading(hub, MARKETS) as heard:
        await announcer([own(), event(name), own()])
        await settled()

        # Nothing of the hand-over went out before the lists were dropped, the events
        # before the market's own included.
        assert log.lines == ["lists dropped", "me.changed", name, "me.changed"]
        assert heard == ["ready", "me.changed", name, "me.changed"]


async def test_one_hand_over_drops_the_lists_once_however_many_market_events_it_holds(
    hub: LiveHub, log: Log
) -> None:
    announcer = Announcer(hub, WatchedCache(log))

    await announcer([event("market.status"), event("market.quote"), event("market.quote")])

    assert log.lines == ["lists dropped", "market.status", "market.quote", "market.quote"]


@pytest.mark.parametrize(
    "names",
    [
        ["me.changed"],
        ["leaderboard.changed"],
        ["discussion.changed", "chat.message", "chat.removed", "broadcast.changed"],
    ],
)
async def test_a_hand_over_without_a_price_or_a_status_leaves_the_cached_lists(
    hub: LiveHub, log: Log, names: list[str]
) -> None:
    """A market list holds prices and statuses. What else is announced about a market,
    also on the topic `markets`, changes no list."""
    announcer = Announcer(hub, WatchedCache(log))

    await announcer([event(name) for name in names])

    assert log.lines == names


async def test_a_cache_that_fails_holds_the_events_back_and_tells_the_unit_of_work(
    hub: LiveHub, log: Log
) -> None:
    """No market event goes out over lists that were not dropped. The unit of work logs
    what is raised here and goes no further (tests/integration/test_live_stream.py)."""
    announcer = Announcer(hub, BrokenCache())

    with pytest.raises(RuntimeError, match="the cache is broken"):
        await announcer([event("market.quote")])
    assert log.lines == []

    await announcer([own()])
    assert log.lines == ["me.changed"]


# --- The pace of the leaderboard ---------------------------------------------------------


class Waiting:
    """One call that `Timers` still owes."""

    def __init__(self, due: datetime, callback: Callable[[], None], timers: Timers) -> None:
        self.due = due
        self.callback = callback
        self._timers = timers

    def cancel(self) -> None:
        if self in self._timers.waiting:
            self._timers.waiting.remove(self)


class Timers:
    """The clock of these tests. `call_later` stands where the event loop's stands;
    `advance` moves the `FrozenClock` and makes every call that falls due on the way,
    at its own instant."""

    def __init__(self, clock: FrozenClock) -> None:
        self._clock = clock
        self.waiting: list[Waiting] = []

    def call_later(self, seconds: float, callback: Callable[[], None]) -> Waiting:
        timer = Waiting(self._clock.now() + timedelta(seconds=seconds), callback, self)
        self.waiting.append(timer)
        return timer

    def advance(self, seconds: float) -> None:
        end = self._clock.now() + timedelta(seconds=seconds)
        while due := sorted(
            (timer for timer in self.waiting if timer.due <= end), key=lambda timer: timer.due
        ):
            self.waiting.remove(due[0])
            self._clock.set(due[0].due)
            due[0].callback()
        self._clock.set(end)


class Announced:
    """Stands where `LiveHub.publish` stands: keeps what the pace published, and when."""

    def __init__(self, clock: FrozenClock) -> None:
        self._clock = clock
        self._start = clock.now()
        self.events: list[LiveEvent] = []
        self.at: list[float] = []

    def __call__(self, events: Sequence[LiveEvent]) -> None:
        self.events.extend(events)
        self.at.extend((self._clock.now() - self._start).total_seconds() for _ in events)


@pytest.fixture
def timers(clock: FrozenClock) -> Timers:
    return Timers(clock)


@pytest.fixture
def announced(clock: FrozenClock) -> Announced:
    return Announced(clock)


@pytest.fixture
def pace(clock: FrozenClock, timers: Timers, announced: Announced) -> LeaderboardPace:
    return LeaderboardPace(load_base_settings().live, clock, announced, timers.call_later)


def test_the_first_move_after_a_quiet_interval_is_announced_at_once(
    pace: LeaderboardPace, announced: Announced, clock: FrozenClock
) -> None:
    pace.moved()

    (told,) = announced.events
    assert told == LiveEvent(
        (LEADERBOARD,),
        Audience.everyone(),
        LeaderboardChangedEvent(type="leaderboard.changed", ts=clock.now()),
    )
    assert told.ends_streams_of is None
    assert announced.at == [0.0]


def test_the_moves_inside_an_interval_are_announced_once_at_its_end(
    pace: LeaderboardPace, announced: Announced, timers: Timers, clock: FrozenClock
) -> None:
    pace.moved()
    for _ in range(10):
        timers.advance(0.4)
        pace.moved()
    assert announced.at == [0.0]

    timers.advance(REFRESH_SECONDS - 4.0 - 0.001)
    assert announced.at == [0.0]

    timers.advance(0.001)
    assert announced.at == [0.0, float(REFRESH_SECONDS)]
    # The event says when it is announced, not when the ranking moved.
    assert announced.events[1].payload == LeaderboardChangedEvent(
        type="leaderboard.changed", ts=clock.now()
    )


def test_nothing_is_announced_while_nothing_moves(
    pace: LeaderboardPace, announced: Announced, timers: Timers
) -> None:
    timers.advance(60)
    assert (announced.at, timers.waiting) == ([], [])

    pace.moved()
    timers.advance(600)

    # One move, one announcement: the interval after it ends without another.
    assert announced.at == [60.0]
    assert timers.waiting == []


def test_no_number_of_moves_makes_the_announcement_come_faster(
    pace: LeaderboardPace, announced: Announced, timers: Timers
) -> None:
    for _ in range(600):
        pace.moved()
        timers.advance(0.1)

    # Sixty seconds of moves: one announcement at once, and one at the end of every
    # interval since.
    assert announced.at == [float(second) for second in range(0, 60 + 1, REFRESH_SECONDS)]
    gaps = [later - earlier for earlier, later in zip(announced.at, announced.at[1:], strict=False)]
    assert min(gaps) >= REFRESH_SECONDS


def test_a_move_after_an_interval_that_ended_quietly_is_announced_at_once_again(
    pace: LeaderboardPace, announced: Announced, timers: Timers
) -> None:
    pace.moved()
    timers.advance(REFRESH_SECONDS)
    pace.moved()
    timers.advance(REFRESH_SECONDS - 1)
    pace.moved()
    timers.advance(1)

    assert announced.at == [0.0, float(REFRESH_SECONDS), 2.0 * REFRESH_SECONDS]


def test_an_interval_begins_with_the_announcement_at_the_end_of_the_one_before(
    pace: LeaderboardPace, announced: Announced, timers: Timers
) -> None:
    pace.moved()
    timers.advance(1)
    pace.moved()
    timers.advance(REFRESH_SECONDS)
    # One second into the interval that began with the second announcement.
    pace.moved()
    timers.advance(REFRESH_SECONDS - 2)
    assert announced.at == [0.0, float(REFRESH_SECONDS)]

    timers.advance(1)
    assert announced.at == [0.0, float(REFRESH_SECONDS), 2.0 * REFRESH_SECONDS]


@pytest.mark.parametrize(
    "name", sorted(EVENT_NAMES | {"discussion.changed", "chat.message", "broadcast.changed"})
)
def test_only_the_three_events_that_move_the_ranking_count_as_a_move(
    pace: LeaderboardPace, announced: Announced, name: str
) -> None:
    pace.saw(event(name))

    assert len(announced.events) == (1 if name in MOVES_THE_RANKING else 0)
    assert (name in MOVES_THE_RANKING) == (name in {"market.quote", "market.status", "me.changed"})


def test_a_pace_that_was_stopped_announces_nothing_and_waits_for_nothing(
    pace: LeaderboardPace, announced: Announced, timers: Timers
) -> None:
    pace.moved()
    pace.moved()
    assert len(timers.waiting) == 1

    pace.stop()
    assert timers.waiting == []
    pace.moved()
    timers.advance(10 * REFRESH_SECONDS)
    pace.stop()

    assert announced.at == [0.0]


# --- The pace as a watcher of the hub ----------------------------------------------------


async def test_what_a_commit_announces_moves_the_leaderboard_at_its_pace(
    hub: LiveHub, clock: FrozenClock, timers: Timers
) -> None:
    pace = LeaderboardPace(load_base_settings().live, clock, hub.publish, timers.call_later)
    hub.watch(pace.saw)
    log = Log()
    announcer = Announcer(hub, WatchedCache(log))

    async with reading(hub, MARKETS, LEADERBOARD) as heard:
        # A trade: the trader's balance through the ledger, then the price.
        await announcer([own(), event("market.quote")])
        await settled()
        assert heard == ["ready", "me.changed", "leaderboard.changed", "market.quote"]

        for _ in range(5):
            timers.advance(0.5)
            await announcer([own(), event("market.quote")])
        await settled()
        assert heard.count("leaderboard.changed") == 1

        timers.advance(REFRESH_SECONDS - 2.5)
        await settled()
        assert heard.count("leaderboard.changed") == 2
        assert heard[-1] == "leaderboard.changed"

        # The announcement is no move: nothing follows it.
        timers.advance(10 * REFRESH_SECONDS)
        await settled()
        assert heard.count("leaderboard.changed") == 2
        assert timers.waiting == []


async def test_an_event_published_without_a_transaction_moves_the_leaderboard_too(
    hub: LiveHub, clock: FrozenClock, timers: Timers
) -> None:
    pace = LeaderboardPace(load_base_settings().live, clock, hub.publish, timers.call_later)
    hub.watch(pace.saw)

    async with reading(hub, LEADERBOARD) as heard:
        hub.publish([event("broadcast.changed"), event("market.status")])
        await settled()

        assert heard == ["ready", "leaderboard.changed"]


# --- The pace on the event loop ----------------------------------------------------------


async def test_the_event_loop_calls_later_and_a_call_that_was_called_off_is_not_made() -> None:
    made: list[str] = []

    call_later_on_the_loop(0.01, lambda: made.append("kept"))
    call_later_on_the_loop(0.01, lambda: made.append("called off")).cancel()
    for _ in range(200):
        if made:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.02)

    assert made == ["kept"]


async def test_without_a_clock_of_its_own_the_pace_waits_on_the_event_loop(
    clock: FrozenClock, announced: Announced
) -> None:
    pace = LeaderboardPace(load_base_settings().live, clock, announced)
    loop = asyncio.get_running_loop()
    before = loop.time()

    pace.moved()
    pace.moved()

    assert len(announced.events) == 1
    interval = pace._interval
    assert isinstance(interval, asyncio.TimerHandle)
    assert REFRESH_SECONDS - 1 < interval.when() - before <= REFRESH_SECONDS + 1
    pace.stop()
    assert interval.cancelled()
