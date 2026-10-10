"""What follows a commit that announced something (SPEC §9).

A service announces inside its transaction (`uow.announce`). Once the commit has
returned and the connection is back in the pool, the unit of work hands the events of
that transaction to the `Announcer`, in the order they were announced. So an event
exists exactly if its transaction committed. What then happens, in this order:

1. If one of the events is a `market.quote` or a `market.status`, the cached market
   lists are dropped. A tab that reads a list because of an event never reads the copy
   from before the event. No service drops the cached lists itself.
2. The events go to the hub, which hands them to the open streams.

Nothing here can fail the request whose transaction stands: the cache answers a Redis
that is down by doing nothing, the hub raises nothing, and the unit of work logs and
swallows whatever is raised all the same.

`LeaderboardPace` stands beside it: it watches what the hub publishes and says that the
ranking moved, at most once per `LEADERBOARD_REFRESH_SECONDS`.
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import Final, Protocol

from app.core.clock import Clock
from app.core.config import LiveSettings
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.integrations.redis.market_cache import MarketCache
from app.schemas.live import LeaderboardChangedEvent
from app.services.live_hub import LiveHub

DROPS_THE_MARKET_LISTS: Final[frozenset[str]] = frozenset({"market.quote", "market.status"})
"""The events after which a market list reads differently: a price and a status are
part of every list their market is on."""

MOVES_THE_RANKING: Final[frozenset[str]] = frozenset(
    {"market.quote", "market.status", "me.changed"}
)
"""The events that can move a figure of the leaderboard: a price values open positions,
a settlement changes the rates, and a balance, a username or a suspension changes a
row."""


class Announcer:
    """The callable a unit of work is given (`UnitOfWork(session_factory, announcer)`)."""

    def __init__(self, hub: LiveHub, market_cache: MarketCache) -> None:
        self._hub = hub
        self._market_cache = market_cache

    async def __call__(self, events: Sequence[LiveEvent]) -> None:
        """Take the events of one committed transaction: drop the cached market lists
        if one of them is about a market's price or status, then publish them all."""
        if any(event.payload.type in DROPS_THE_MARKET_LISTS for event in events):
            await self._market_cache.invalidate_lists()
        self._hub.publish(events)


class Timer(Protocol):
    """A call that is still to come and can be called off."""

    def cancel(self) -> None: ...


type CallLater = Callable[[float, Callable[[], None]], Timer]
"""Calls a function once, a number of seconds from now."""


def call_later_on_the_loop(seconds: float, callback: Callable[[], None]) -> Timer:
    """`CallLater` of the running event loop."""
    return asyncio.get_running_loop().call_later(seconds, callback)


class LeaderboardPace:
    """Says that the ranking moved (`leaderboard.changed`), at most once per
    `LEADERBOARD_REFRESH_SECONDS`.

    It is a watcher of the hub (`hub.watch(pace.saw)`): no service calls anything for
    it. An event of `MOVES_THE_RANKING` is a move. A move that comes after a quiet
    interval is announced at once, and an interval begins with every announcement. The
    moves inside an interval are announced together, once, when it is over. So no
    number of trades makes the announcement come faster, and nothing is announced while
    nothing moves.

    The event goes out on `leaderboard` to everyone, carries no figure, and is
    published through the hub directly: no transaction stands behind it. A tab that
    hears it reads the ranking on its screen again.

    `call_later` is how the end of an interval is waited for: the event loop's own
    timer, unless a test brings a clock of its own.
    """

    def __init__(
        self,
        settings: LiveSettings,
        clock: Clock,
        publish: Callable[[Sequence[LiveEvent]], None],
        call_later: CallLater = call_later_on_the_loop,
    ) -> None:
        self._interval_seconds = float(settings.leaderboard_refresh_seconds)
        self._clock = clock
        self._publish = publish
        self._call_later = call_later
        self._interval: Timer | None = None
        self._moved_in_interval = False
        self._stopped = False

    def saw(self, event: LiveEvent) -> None:
        """The hub's watcher: an event that moves the ranking is a move."""
        if event.payload.type in MOVES_THE_RANKING:
            self.moved()

    def moved(self) -> None:
        """The ranking moved: announce it now, or with the other moves of the running
        interval when that is over."""
        if self._stopped:
            return
        if self._interval is None:
            self._announce()
        else:
            self._moved_in_interval = True

    def stop(self) -> None:
        """Announce nothing from here on, and wait for nothing."""
        self._stopped = True
        interval, self._interval = self._interval, None
        if interval is not None:
            interval.cancel()

    def _announce(self) -> None:
        self._moved_in_interval = False
        self._interval = self._call_later(self._interval_seconds, self._interval_is_over)
        self._publish(
            [
                LiveEvent(
                    (Topic(TopicKind.LEADERBOARD),),
                    Audience.everyone(),
                    LeaderboardChangedEvent(type="leaderboard.changed", ts=self._clock.now()),
                )
            ]
        )

    def _interval_is_over(self) -> None:
        self._interval = None
        if self._moved_in_interval and not self._stopped:
            self._announce()
