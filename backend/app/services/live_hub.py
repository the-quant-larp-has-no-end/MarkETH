"""The hub of the event stream: the open streams of this API process, and what reaches
each of them (SPEC §9).

`GET /stream` is one long answer per tab. The hub holds every such stream, hands each
event to the streams that follow one of its topics and whose viewer its audience admits,
and tells every stream every `SSE_HEARTBEAT_SECONDS` that it is alive.

    async with hub.open(viewer, tab, topics) as frames:    # refused here, or registered
        async for frame in frames:                         # `ready` first
            yield frame.wire

The hub keeps no event. A stream carries what is published from the moment it opens, and
the tab that hears `ready` reads once what its topics cover. It does no I/O and awaits
nothing while it publishes: an event is a reference put on the queue of each stream it
is for, and the streams write in their own tasks.

A stream ends, and its iterator with it, when

* its reader leaves the block;
* its user opens a stream under the same `tab`: the one that is replaced ends at once,
  so a stream a browser left behind gives its place back;
* its reader is `LIVE_QUEUE_EVENTS` events behind: at once;
* it has lasted 80% to 100% of `LIVE_STREAM_MAX_SECONDS`: the moment is drawn per stream,
  a round of the ticker ends it, and what it was offered still goes out first;
* an event says so (`LiveEvent.ends_streams_of`): after that event has gone out;
* the hub stops.

A stream that ended takes no further event and gives its place back at that moment. Its
tab opens one again through the portal and the guards, which refuse a caller who has
lost the right to one.

The hub lives in the one API process of the stack. No event passes Redis or ScyllaDB.
"""

import asyncio
import logging
import random
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Collection, Sequence
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import LiveSettings
from app.core.errors import ServiceUnavailableError, ValidationFailedError
from app.core.identity import AuthenticatedUser
from app.core.live import LiveEvent, LivePayload, Topic, TopicKind
from app.schemas.live import BeatEvent, ReadyEvent

logger = logging.getLogger(__name__)

STREAM_UNAVAILABLE_MESSAGE: Final[str] = "Live updates are unavailable."
"""Message of the `ServiceUnavailableError` a stream is refused with while the hub holds
`LIVE_MAX_STREAMS` streams. The tab polls."""

LIFE_SHARE_MIN: Final[float] = 0.8
"""A stream lasts between this share of `LIVE_STREAM_MAX_SECONDS` and all of it, so the
streams of a busy moment do not end in the same instant."""

RETRY_FACTOR_MAX: Final[int] = 2
"""`ready` tells a browser to wait between `LIVE_RETRY_MS` and this many times as long
before it opens a stream again by itself."""


def too_many_topics_message(limit: int) -> str:
    """Message of the `ValidationFailedError` that refuses a stream which names more
    topics than `LIVE_TOPICS_PER_STREAM`, which is `limit`."""
    return f"A stream can follow at most {limit} topics."


@dataclass(frozen=True, slots=True)
class LiveFrame:
    """One server-sent event, made once for everybody who receives it."""

    event: str
    """The event's name."""

    data: str
    """The JSON of its body."""

    retry: int | None
    """On `ready` only: the milliseconds a browser waits before it opens the stream
    again by itself."""

    wire: bytes
    """The event as it travels: `event: market.quote\\ndata: {...}\\n\\n`, after a
    `retry:` line on `ready`. Every stream is written the same bytes."""


def _frame(name: str, data: str, body: bytes, retry: int | None = None) -> LiveFrame:
    """The frame of an event called `name` whose JSON is `data`, which is `body` in
    bytes."""
    head = f"event: {name}\ndata: " if retry is None else f"retry: {retry}\nevent: {name}\ndata: "
    return LiveFrame(name, data, retry, b"".join((head.encode(), body, b"\n\n")))


def _own_frame(payload: LivePayload, retry: int | None = None) -> LiveFrame:
    """The frame of an event the hub sends by itself: `ready` and `beat`."""
    data = payload.model_dump_json()
    return _frame(payload.type, data, data.encode(), retry)


def _breaks_its_line(text: str) -> bool:
    """Whether `text` would end the line of an event it is written on."""
    return "\n" in text or "\r" in text


class _Stream:
    """One open stream: who reads it, what it follows and what waits to go out."""

    __slots__ = ("ends_at", "key", "queue", "ready", "topics", "viewer")

    def __init__(
        self,
        viewer: AuthenticatedUser,
        tab: str,
        topics: frozenset[Topic],
        *,
        queue_events: int,
        ends_at: datetime,
        ready: LiveFrame,
    ) -> None:
        self.viewer = viewer
        self.key = (viewer.id, tab)
        self.topics = topics
        self.queue: asyncio.Queue[LiveFrame] = asyncio.Queue(maxsize=queue_events)
        self.ends_at = ends_at
        self.ready = ready


class LiveHub:
    def __init__(self, settings: LiveSettings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock
        self._streams: set[_Stream] = set()
        self._followers: dict[Topic, set[_Stream]] = {}
        self._by_tab: dict[tuple[UUID, str], _Stream] = {}
        self._watchers: list[Callable[[LiveEvent], None]] = []
        self._ticker: asyncio.Task[None] | None = None
        self._stopped = False
        self._warned_full_at: datetime | None = None

    # --- Its life -----------------------------------------------------------------

    async def start(self) -> None:
        """Start the ticker: one task that calls `tick` every `SSE_HEARTBEAT_SECONDS`.

        A hub that was not started publishes all the same; its streams get no beat and
        last until something else ends them.
        """
        if self._ticker is None and not self._stopped:
            self._ticker = asyncio.create_task(self._tick_for_ever(), name="live-hub-ticker")

    async def stop(self) -> None:
        """End every stream at once, then the ticker. Safe without `start`, and more
        than once. A hub that was stopped opens no stream: one that asks is refused
        like one beyond `LIVE_MAX_STREAMS`."""
        self._stopped = True
        for stream in tuple(self._streams):
            self._end(stream, at_once=True)
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)

    def tick(self) -> None:
        """One round of the ticker: a stream whose life is over is ended, after what it
        was offered has gone out; every other stream is offered a `beat`."""
        now = self._clock.now()
        beat = _own_frame(BeatEvent(type="beat", ts=now))
        for stream in tuple(self._streams):
            if now >= stream.ends_at:
                self._end(stream, at_once=False)
            else:
                self._offer(stream, beat)

    async def _tick_for_ever(self) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_seconds)
            try:
                self.tick()
            except Exception:
                logger.exception("A round of the event stream's ticker failed")

    # --- Publishing ---------------------------------------------------------------

    def publish(self, events: Sequence[LiveEvent]) -> None:
        """Hand each event, in order, to the streams it is for.

        A stream gets an event exactly if it follows one of the event's topics and the
        event's audience admits its viewer, and once however many of its topics the
        event names. The body is turned into JSON once and into bytes once: every
        stream is handed the same frame. After an event with `ends_streams_of` has been
        handed out, the streams of that user end.

        It awaits nothing and raises nothing, whatever an event or a watcher does: the
        transaction behind an event has committed. An event whose JSON is larger than
        `LIVE_EVENT_MAX_BYTES`, or whose name or JSON holds a line break, is a bug of
        its publisher: it reaches no stream and no watcher and is logged as an error.

        A unit of work publishes through the `Announcer`. This is called directly for
        an event no transaction stands behind, on the event loop like everything here.
        """
        for event in events:
            try:
                self._publish(event)
            except Exception:
                logger.exception("A live event was not published")
            if event.ends_streams_of is not None:
                self.end_streams_of(event.ends_streams_of)

    def watch(self, watcher: Callable[[LiveEvent], None]) -> None:
        """Call `watcher` with every event that is published, after the streams were
        handed it. A watcher may publish. One that raises is logged and costs nothing
        else."""
        self._watchers.append(watcher)

    def _publish(self, event: LiveEvent) -> None:
        name = event.payload.type
        data = event.payload.model_dump_json()
        body = data.encode()
        if len(body) > self._settings.event_max_bytes:
            logger.error(
                "Dropped a %r event of %d bytes: an event holds at most %d (LIVE_EVENT_MAX_BYTES)",
                name,
                len(body),
                self._settings.event_max_bytes,
            )
            return
        if _breaks_its_line(name) or _breaks_its_line(data):
            logger.error("Dropped a %r event: its name or its JSON holds a line break", name)
            return
        frame = _frame(name, data, body)
        for stream in self._following(event.topics):
            if event.audience.admits(stream.viewer):
                self._offer(stream, frame)
        for watcher in self._watchers:
            try:
                watcher(event)
            except Exception:
                logger.exception("A watcher of the event stream failed on a %r event", name)

    def _following(self, topics: Sequence[Topic]) -> set[_Stream]:
        """The streams that follow one of `topics`, each once."""
        found: set[_Stream] = set()
        for topic in topics:
            found.update(self._followers.get(topic, ()))
        return found

    def _offer(self, stream: _Stream, frame: LiveFrame) -> None:
        """Put `frame` on the queue of `stream`, or end a stream whose queue is full."""
        try:
            stream.queue.put_nowait(frame)
        except asyncio.QueueFull:
            logger.info(
                "Ended a stream whose reader is %d events behind (LIVE_QUEUE_EVENTS)",
                self._settings.queue_events,
            )
            self._end(stream, at_once=True)
        except asyncio.QueueShutDown:
            self._forget(stream)

    # --- Streams ------------------------------------------------------------------

    @asynccontextmanager
    async def open(
        self, viewer: AuthenticatedUser, tab: str, topics: Collection[Topic]
    ) -> AsyncIterator[AsyncIterator[LiveFrame]]:
        """A stream of `viewer` that follows `topics` and the viewer's own events.

        Use as `async with hub.open(viewer, tab, topics) as frames: async for frame in
        frames`. On entry, in one step that awaits nothing:

        1. More topics than `LIVE_TOPICS_PER_STREAM`: `ValidationFailedError` (422)
           with `details.field` = `topic`. A hub that was stopped:
           `ServiceUnavailableError` (503). Nothing changes in either case.
        2. The stream this user holds under the same `tab` is ended at once.
        3. `LIVE_MAX_STREAMS` open streams: `ServiceUnavailableError` (503), with a
           warning in the log at most once per `SSE_HEARTBEAT_SECONDS`.
        4. The stream is registered: from here on it is handed what is published.

        Both errors are raised before the iterator exists, so a route can answer them
        as JSON. Naming a topic grants nothing: whether an event reaches the stream is
        the audience's word on `viewer`, who is the caller the guards let in. The
        viewer's own topic is followed without being named, and nobody else's is: a
        `me` topic among `topics` is left out.

        The iterator yields `ready` first, then whatever is published, and ends when
        the stream ends. Leaving the block ends the stream and gives its place back.
        """
        stream = self._admit(viewer, tab, topics)
        try:
            async with aclosing(self._frames(stream)) as frames:
                yield frames
        finally:
            self._end(stream, at_once=True)

    def end_streams_of(self, user_id: UUID) -> None:
        """End every stream of this user, after what each was offered has gone out."""
        for stream in tuple(self._followers.get(Topic(TopicKind.ME, user_id), ())):
            self._end(stream, at_once=False)

    @property
    def open_streams(self) -> int:
        """How many streams the hub holds: the count `LIVE_MAX_STREAMS` bounds."""
        return len(self._streams)

    def _admit(self, viewer: AuthenticatedUser, tab: str, topics: Collection[Topic]) -> _Stream:
        named = {topic for topic in topics if topic.kind is not TopicKind.ME}
        limit = self._settings.topics_per_stream
        if len(named) > limit:
            raise ValidationFailedError(too_many_topics_message(limit), details={"field": "topic"})
        if self._stopped:
            raise ServiceUnavailableError(STREAM_UNAVAILABLE_MESSAGE)
        replaced = self._by_tab.get((viewer.id, tab))
        if replaced is not None:
            self._end(replaced, at_once=True)
        now = self._clock.now()
        if len(self._streams) >= self._settings.max_streams:
            self._warn_that_it_is_full(now)
            raise ServiceUnavailableError(STREAM_UNAVAILABLE_MESSAGE)

        longest = self._settings.stream_max_seconds
        shortest = LIFE_SHARE_MIN * longest
        # The ticker ends a stream at its first round after the moment drawn here, which
        # is up to a heartbeat later: the draw leaves that round its time.
        life_seconds = random.uniform(
            shortest, max(shortest, longest - self._settings.heartbeat_seconds)
        )
        retry_ms = random.randint(
            self._settings.retry_ms, RETRY_FACTOR_MAX * self._settings.retry_ms
        )
        stream = _Stream(
            viewer,
            tab,
            frozenset(named | {Topic(TopicKind.ME, viewer.id)}),
            queue_events=self._settings.queue_events,
            ends_at=now + timedelta(seconds=life_seconds),
            ready=_own_frame(ReadyEvent(type="ready", ts=now), retry_ms),
        )
        self._streams.add(stream)
        self._by_tab[stream.key] = stream
        for topic in stream.topics:
            self._followers.setdefault(topic, set()).add(stream)
        return stream

    def _warn_that_it_is_full(self, now: datetime) -> None:
        heartbeat = timedelta(seconds=self._settings.heartbeat_seconds)
        last = self._warned_full_at
        if last is not None and timedelta(0) <= now - last < heartbeat:
            return
        self._warned_full_at = now
        logger.warning(
            "The hub holds %d streams (LIVE_MAX_STREAMS): a stream is refused",
            len(self._streams),
        )

    @staticmethod
    async def _frames(stream: _Stream) -> AsyncGenerator[LiveFrame]:
        yield stream.ready
        while True:
            try:
                frame = await stream.queue.get()
            except asyncio.QueueShutDown:
                return
            yield frame

    def _end(self, stream: _Stream, *, at_once: bool) -> None:
        """End `stream`: it leaves every index and takes no further event. `at_once`
        drops what waits on its queue; otherwise that still goes out first."""
        self._forget(stream)
        stream.queue.shutdown(immediate=at_once)

    def _forget(self, stream: _Stream) -> None:
        if stream not in self._streams:
            return
        self._streams.discard(stream)
        if self._by_tab.get(stream.key) is stream:
            del self._by_tab[stream.key]
        for topic in stream.topics:
            followers = self._followers[topic]
            followers.discard(stream)
            if not followers:
                del self._followers[topic]
