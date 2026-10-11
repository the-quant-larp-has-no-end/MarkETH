"""Streams of the application's hub, read by a test (SPEC §9).

A service announces inside its transaction and the unit of work hands the events to the
hub with the commit: when the call that wrote has returned, its events lie on the queue
of every stream they are for. `listening` opens such a stream as a tab does and reads it
in a task of its own, the way the route does.

    async with listening(container.live, factory.identity(alice), [of(market)]) as heard:
        await container.broadcasts.start_as_admin(admin, market.id)
        await settled()
        assert heard.names == ["broadcast.changed"]

What a stream brought is kept without its `ready`: every stream opens with one, and
`listening` asserts it. It is kept without the hub's `beat`s too: the hub of a started
container beats every `SSE_HEARTBEAT_SECONDS` of real time, whatever a test writes, so a
list of names is the same on a slow machine. Two streams of one user need two tab ids.

The factory books through the application's ledger: open a stream once the people and
positions of the test are in place.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from typing import Any, Final

from app.core.identity import AuthenticatedUser
from app.core.live import Topic, TopicKind
from app.models import Market
from app.services.live_hub import LiveFrame, LiveHub

MARKETS: Final[Topic] = Topic(TopicKind.MARKETS)
"""The topic of the market lists."""

BEAT: Final[str] = "beat"
"""The event by which the hub says that a stream is alive."""

TURNS: Final[int] = 5
"""The turns of the event loop `settled` gives the readers: a reader takes everything
its stream was offered in one."""


def of(market: Market) -> Topic:
    """The topic of one market."""
    return Topic(TopicKind.MARKET, market.id)


def broadcast_of(market: Market) -> Topic:
    """The topic of the live stream of one market."""
    return Topic(TopicKind.BROADCAST, market.id)


class Heard:
    """What one stream brought after its `ready`, in the order it arrived, without the
    beats."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.ended = False
        """Whether the hub ended the stream."""

    async def take(self, frames: AsyncIterator[LiveFrame]) -> None:
        async for frame in frames:
            if frame.event != BEAT:
                self.bodies.append(json.loads(frame.data))
        self.ended = True

    @property
    def names(self) -> list[str]:
        return [body["type"] for body in self.bodies]

    def named(self, name: str) -> list[dict[str, Any]]:
        """The bodies of the events called `name`."""
        return [body for body in self.bodies if body["type"] == name]


async def settled() -> None:
    """Let the readers take what their streams were offered."""
    for _ in range(TURNS):
        await asyncio.sleep(0)


@asynccontextmanager
async def listening(
    hub: LiveHub,
    viewer: AuthenticatedUser,
    topics: Collection[Topic] = (),
    *,
    tab: str = "tab-0001",
) -> AsyncIterator[Heard]:
    """A stream of `viewer` on `topics`, open for as long as the block lasts. It always
    carries the viewer's own events."""
    async with hub.open(viewer, tab, topics) as frames:
        heard = Heard()
        reader = asyncio.create_task(heard.take(frames))
        try:
            await settled()
            ready = heard.bodies.pop(0)
            assert ready["type"] == "ready", ready
            yield heard
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
