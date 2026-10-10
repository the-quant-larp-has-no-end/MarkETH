"""The hub of the event stream (`app/services/live_hub.py`, SPEC §9).

An event reaches a stream exactly if the stream follows one of its topics and its
audience admits the stream's viewer. The hub keeps no event: a stream carries what is
published from the moment it opens, after `ready`. Every stream has a queue of
`LIVE_QUEUE_EVENTS` and is ended when it overflows, when its life is over, when its
user opens another under the same tab, and when an event says so.

Time is the `FrozenClock` of the test: a round of the ticker is `hub.tick()`.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

import pytest

from app.core.config import LiveSettings
from app.core.enums import UserRole, UserStatus
from app.core.errors import ServiceUnavailableError, ValidationFailedError
from app.core.identity import AuthenticatedUser
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.services.live_hub import (
    LIFE_SHARE_MIN,
    RETRY_FACTOR_MAX,
    STREAM_UNAVAILABLE_MESSAGE,
    LiveFrame,
    LiveHub,
    too_many_topics_message,
)
from tests.support.clock import FrozenClock
from tests.support.settings import load_base_settings

MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e")
OTHER_MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5f")
PROGRAMME: Final[str] = "BSc Computer Science"
OTHER_PROGRAMME: Final[str] = "MSc Data Science"

MARKETS: Final[Topic] = Topic(TopicKind.MARKETS)
LEADERBOARD: Final[Topic] = Topic(TopicKind.LEADERBOARD)
MARKET: Final[Topic] = Topic(TopicKind.MARKET, MARKET_ID)
OTHER_MARKET: Final[Topic] = Topic(TopicKind.MARKET, OTHER_MARKET_ID)
BROADCAST: Final[Topic] = Topic(TopicKind.BROADCAST, MARKET_ID)


def viewer(
    number: int, programme: str = PROGRAMME, role: UserRole = UserRole.USER
) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=UUID(int=number),
        email=f"viewer{number}@ethz.ch",
        name=f"Viewer {number}",
        username=f"viewer{number}",
        degree_programme=programme,
        role=role,
        status=UserStatus.ACTIVE,
    )


ALICE: Final[AuthenticatedUser] = viewer(1)
BOB: Final[AuthenticatedUser] = viewer(2, OTHER_PROGRAMME)
ADMIN: Final[AuthenticatedUser] = viewer(3, OTHER_PROGRAMME, UserRole.ADMIN)


def own(who: AuthenticatedUser) -> Topic:
    return Topic(TopicKind.ME, who.id)


def live_settings(**changed: Any) -> LiveSettings:
    """The live settings of the tests, with the named fields set for one test."""
    return load_base_settings().live.model_copy(update=changed)


@dataclass(frozen=True)
class Body:
    """Stands where an event model stands."""

    type: str
    text: str = ""

    def model_dump_json(self) -> str:
        return json.dumps(
            {"type": self.type, "text": self.text}, separators=(",", ":"), ensure_ascii=False
        )


def event(
    *topics: Topic,
    to: Audience | None = None,
    name: str = "market.status",
    text: str = "",
    ends_streams_of: UUID | None = None,
) -> LiveEvent:
    return LiveEvent(
        topics, to or Audience.everyone(), Body(name, text), ends_streams_of=ends_streams_of
    )


class Reading:
    """What one stream brought, read in a task of its own as a route reads it."""

    def __init__(self) -> None:
        self.frames: list[LiveFrame] = []
        self.ended = False

    async def take(self, frames: AsyncIterator[LiveFrame]) -> None:
        async for frame in frames:
            self.frames.append(frame)
        self.ended = True

    @property
    def names(self) -> list[str]:
        return [frame.event for frame in self.frames]

    @property
    def texts(self) -> list[str]:
        return [json.loads(frame.data).get("text", "") for frame in self.frames[1:]]


async def settled() -> None:
    """Let every reader take what its stream was offered."""
    for _ in range(5):
        await asyncio.sleep(0)


@asynccontextmanager
async def reading(
    hub: LiveHub,
    who: AuthenticatedUser,
    topics: Collection[Topic] = (),
    *,
    tab: str = "tab-0001",
) -> AsyncIterator[Reading]:
    async with hub.open(who, tab, topics) as frames:
        taken = Reading()
        reader = asyncio.create_task(taken.take(frames))
        try:
            await settled()
            yield taken
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def eventually(holds: Callable[[], bool]) -> None:
    """Wait in real time until `holds`."""
    for _ in range(400):
        if holds():
            return
        await asyncio.sleep(0.005)
    pytest.fail("the condition did not become true within two seconds")


async def everything(frames: AsyncIterator[LiveFrame]) -> list[LiveFrame]:
    """What a stream that has ended still hands out."""
    async with asyncio.timeout(1):
        return [frame async for frame in frames]


@pytest.fixture
def hub(clock: FrozenClock) -> LiveHub:
    return LiveHub(live_settings(), clock)


# --- Who receives what ----------------------------------------------------------------


async def test_an_event_reaches_a_stream_that_follows_one_of_its_topics_and_reaches_it_once(
    hub: LiveHub,
) -> None:
    async with (
        reading(hub, ALICE, [MARKETS, MARKET]) as both,
        reading(hub, BOB, [MARKET]) as one,
        reading(hub, ADMIN, [OTHER_MARKET, LEADERBOARD, BROADCAST]) as neither,
    ):
        hub.publish([event(MARKETS, MARKET, text="a")])
        await settled()

        assert both.names == ["ready", "market.status"]
        assert one.names == ["ready", "market.status"]
        assert neither.names == ["ready"]
        # One frame for everybody who receives the event.
        assert both.frames[1] is one.frames[1]


async def test_events_reach_a_stream_in_the_order_they_were_published(hub: LiveHub) -> None:
    async with reading(hub, ALICE, [MARKETS, LEADERBOARD]) as alice:
        hub.publish([event(MARKETS, text="1"), event(LEADERBOARD, text="2")])
        hub.publish([event(MARKETS, text="3")])
        await settled()

        assert alice.texts == ["1", "2", "3"]


@pytest.mark.parametrize(
    ("audience", "admitted"),
    [
        (Audience.everyone(), ["viewer1", "viewer2", "viewer3"]),
        (Audience.market(MARKET_ID, None, UUID(int=9)), ["viewer1", "viewer2", "viewer3"]),
        (Audience.market(MARKET_ID, PROGRAMME, UUID(int=9)), ["viewer1", "viewer3"]),
        (Audience.market(MARKET_ID, PROGRAMME, BOB.id), ["viewer1", "viewer2", "viewer3"]),
        (Audience.users([BOB.id]), ["viewer2"]),
        (Audience.users([]), []),
        (Audience.admins(), ["viewer3"]),
    ],
)
async def test_an_event_reaches_no_viewer_its_audience_does_not_admit_whatever_the_stream_follows(
    hub: LiveHub, audience: Audience, admitted: list[str]
) -> None:
    """Naming a topic grants nothing: all three streams follow every topic of the
    event."""
    async with AsyncExitStack() as stack:
        readings = {
            who.username: await stack.enter_async_context(
                reading(hub, who, [MARKETS, MARKET, BROADCAST])
            )
            for who in (ALICE, BOB, ADMIN)
        }
        hub.publish([event(MARKETS, MARKET, BROADCAST, to=audience)])
        await settled()

        assert [name for name, taken in readings.items() if len(taken.frames) == 2] == admitted
        assert all(taken.names[0] == "ready" for taken in readings.values())


async def test_every_stream_follows_its_viewers_own_topic_without_naming_it_and_nobody_elses(
    hub: LiveHub,
) -> None:
    async with (
        reading(hub, ALICE) as alice,
        # A topic of somebody else that reaches the hub is left out of what is followed.
        reading(hub, BOB, [own(ALICE), MARKETS]) as bob,
    ):
        hub.publish([event(own(ALICE), to=Audience.users([ALICE.id]), name="me.changed")])
        # An event on Alice's topic that is for everyone: only whoever follows it hears.
        hub.publish([event(own(ALICE), name="me.changed")])
        await settled()

        assert alice.names == ["ready", "me.changed", "me.changed"]
        assert bob.names == ["ready"]

        hub.end_streams_of(ALICE.id)
        await settled()

        assert (alice.ended, bob.ended) == (True, False)


# --- What a stream is handed -----------------------------------------------------------


async def test_a_stream_opens_with_ready_and_carries_nothing_from_before_it_opened(
    hub: LiveHub, clock: FrozenClock
) -> None:
    settings = live_settings()
    hub.publish([event(MARKETS, text="before")])

    async with hub.open(ALICE, "tab-0001", [MARKETS]) as frames:
        # Published after the stream opened and before its reader read anything.
        hub.publish([event(MARKETS, text="after")])
        ready = await anext(frames)
        after = await anext(frames)

    assert ready.event == "ready"
    assert json.loads(ready.data) == {"type": "ready", "ts": _as_json(clock.now())}
    assert ready.retry is not None
    assert settings.retry_ms <= ready.retry <= RETRY_FACTOR_MAX * settings.retry_ms
    assert ready.wire == f"retry: {ready.retry}\nevent: ready\ndata: {ready.data}\n\n".encode()
    assert json.loads(after.data) == {"type": "market.status", "text": "after"}


def _as_json(instant: datetime) -> str:
    """An instant as an event model writes it."""
    return instant.isoformat().replace("+00:00", "Z")


async def test_a_frame_is_the_name_and_the_json_of_its_event_and_carries_no_id(
    hub: LiveHub,
) -> None:
    async with reading(hub, ALICE, [MARKETS]) as alice:
        hub.publish([event(MARKETS, name="market.quote", text="é\n")])
        await settled()

    ready, quote = alice.frames
    assert (quote.event, quote.retry) == ("market.quote", None)
    assert json.loads(quote.data) == {"type": "market.quote", "text": "é\n"}
    assert quote.wire == f"event: market.quote\ndata: {quote.data}\n\n".encode()
    for frame in (ready, quote):
        lines = frame.wire.decode().split("\n")
        # One `data:` line, no `id:` line, and the empty line that ends the event.
        assert [line.partition(":")[0] for line in lines[:-2]] == (
            ["retry", "event", "data"] if frame is ready else ["event", "data"]
        )
        assert lines[-2:] == ["", ""]


class Counted:
    """A body that counts how often it is turned into JSON."""

    type = "market.quote"

    def __init__(self) -> None:
        self.written = 0

    def model_dump_json(self) -> str:
        self.written += 1
        return '{"type":"market.quote"}'


async def test_a_body_is_written_once_and_its_frame_encoded_once_for_a_thousand_receivers(
    clock: FrozenClock,
) -> None:
    hub = LiveHub(live_settings(max_streams=1000), clock)
    body = Counted()

    async with AsyncExitStack() as stack:
        streams = [
            await stack.enter_async_context(hub.open(viewer(number), "tab-0001", [MARKETS]))
            for number in range(1, 1001)
        ]
        hub.publish([LiveEvent((MARKETS,), Audience.everyone(), body)])
        received = []
        for frames in streams:
            assert (await anext(frames)).event == "ready"
            received.append(await anext(frames))

    assert body.written == 1
    assert len(received) == 1000
    assert all(frame.wire is received[0].wire for frame in received)


async def test_an_event_above_the_largest_size_reaches_nobody_and_is_logged(
    clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    hub = LiveHub(live_settings(event_max_bytes=512), clock)
    seen: list[str] = []
    hub.watch(lambda published: seen.append(published.payload.type))
    fits = "x" * (512 - len(Body("chat.message").model_dump_json()))
    accented = "é" * 300

    async with reading(hub, ALICE, [MARKET]) as alice:
        with caplog.at_level(logging.ERROR, logger="app.services.live_hub"):
            hub.publish(
                [
                    event(MARKET, name="chat.message", text=fits + "x"),
                    event(MARKET, name="chat.message", text=fits),
                    # 300 characters of two bytes each: the limit is one of bytes.
                    event(MARKET, name="chat.message", text=accented),
                ]
            )
        await settled()

        assert [len(frame.data.encode()) for frame in alice.frames[1:]] == [512]
    assert seen == ["chat.message"]
    assert [record.getMessage() for record in caplog.records] == [
        "Dropped a 'chat.message' event of 513 bytes: an event holds at most 512 "
        "(LIVE_EVENT_MAX_BYTES)",
        f"Dropped a 'chat.message' event of {len(accented) * 2 + 512 - len(fits)} bytes: "
        "an event holds at most 512 (LIVE_EVENT_MAX_BYTES)",
    ]


class Raw:
    """A body that writes itself as it likes."""

    def __init__(self, name: str, written: str) -> None:
        self.type = name
        self._written = written

    def model_dump_json(self) -> str:
        return self._written


@pytest.mark.parametrize(
    ("name", "written"),
    [
        ("market.status", '{"type":"market.status"}\n\nevent: me.changed\ndata: {}'),
        ("market.status", '{"type":\r"market.status"}'),
        ("market.status\nevent: me.changed", "{}"),
    ],
)
async def test_an_event_that_would_break_its_line_reaches_nobody_and_is_logged(
    hub: LiveHub, caplog: pytest.LogCaptureFixture, name: str, written: str
) -> None:
    """A frame is one `event:` line and one `data:` line. A body whose JSON or name
    holds a line break would write a second event into the stream."""
    async with reading(hub, ALICE, [MARKETS]) as alice:
        with caplog.at_level(logging.ERROR, logger="app.services.live_hub"):
            hub.publish([LiveEvent((MARKETS,), Audience.everyone(), Raw(name, written))])
            hub.publish([event(MARKETS, text="line\nbreak in a string")])
        await settled()

        assert alice.texts == ["line\nbreak in a string"]
    (record,) = caplog.records
    assert "holds a line break" in record.getMessage()


# --- Streams that end ------------------------------------------------------------------


async def test_a_stream_that_falls_too_far_behind_is_ended_and_the_others_go_on(
    clock: FrozenClock,
) -> None:
    hub = LiveHub(live_settings(queue_events=3), clock)

    async with (
        hub.open(ALICE, "tab-0001", [MARKETS]) as nobody_reads,
        reading(hub, BOB, [MARKETS]) as bob,
    ):
        for number in range(3):
            hub.publish([event(MARKETS, text=str(number))])
            await settled()
        assert hub.open_streams == 2

        hub.publish([event(MARKETS, text="3")])
        await settled()

        # Ended at once: what waited on its queue is not handed out.
        assert hub.open_streams == 1
        assert [frame.event for frame in await everything(nobody_reads)] == ["ready"]

        hub.publish([event(MARKETS, text="4")])
        await settled()

        assert bob.texts == ["0", "1", "2", "3", "4"]
        assert not bob.ended


async def test_a_reader_that_keeps_up_is_never_behind(clock: FrozenClock) -> None:
    hub = LiveHub(live_settings(queue_events=2), clock)

    async with reading(hub, ALICE, [MARKETS]) as alice:
        for number in range(20):
            hub.publish([event(MARKETS, text=str(number))])
            await settled()

        assert alice.texts == [str(number) for number in range(20)]
        assert hub.open_streams == 1


async def test_the_stream_beyond_the_most_the_hub_holds_is_refused_and_the_next_one_let_in(
    clock: FrozenClock, caplog: pytest.LogCaptureFixture
) -> None:
    settings = live_settings(max_streams=2)
    hub = LiveHub(settings, clock)

    async def a_third() -> None:
        async with hub.open(ADMIN, "tab-0001", []):
            raise AssertionError("a third stream was opened")

    with caplog.at_level(logging.WARNING, logger="app.services.live_hub"):
        async with reading(hub, ALICE), reading(hub, BOB) as bob:
            for _ in range(3):
                with pytest.raises(ServiceUnavailableError) as refused:
                    await a_third()
                assert refused.value.message == STREAM_UNAVAILABLE_MESSAGE
                assert refused.value.status_code == 503
            assert hub.open_streams == 2
            assert not bob.ended

            # The log says so at most once per heartbeat.
            assert len(caplog.records) == 1
            clock.advance(timedelta(seconds=settings.heartbeat_seconds))
            with pytest.raises(ServiceUnavailableError):
                await a_third()
            assert [record.getMessage() for record in caplog.records] == [
                "The hub holds 2 streams (LIVE_MAX_STREAMS): a stream is refused"
            ] * 2

        # Both have left: their places are free again.
        assert hub.open_streams == 0
        async with reading(hub, ADMIN) as admin, reading(hub, ALICE):
            assert admin.names == ["ready"]
            assert hub.open_streams == 2


def test_the_two_sentences_a_stream_is_refused_with() -> None:
    assert STREAM_UNAVAILABLE_MESSAGE == "Live updates are unavailable."
    assert too_many_topics_message(8) == "A stream can follow at most 8 topics."


async def test_more_topics_than_a_stream_may_follow_are_refused_on_the_field_topic(
    clock: FrozenClock,
) -> None:
    hub = LiveHub(live_settings(topics_per_stream=2), clock)

    async def with_topics(topics: Sequence[Topic]) -> list[str]:
        async with reading(hub, ALICE, topics) as alice:
            return alice.names

    async with reading(hub, ALICE) as held:
        with pytest.raises(ValidationFailedError) as refused:
            await with_topics([MARKETS, LEADERBOARD, MARKET])

        assert refused.value.message == "A stream can follow at most 2 topics."
        assert refused.value.details == {"field": "topic"}
        assert refused.value.status_code == 422
        # A stream that is refused replaces none: the one under its tab goes on.
        await settled()
        assert (held.ended, hub.open_streams) == (False, 1)

    # A topic named twice counts once, and the viewer's own topic does not count.
    assert await with_topics([MARKETS, MARKET, MARKETS, MARKET]) == ["ready"]
    assert await with_topics([MARKETS, own(ALICE), MARKET]) == ["ready"]


async def test_a_stream_that_opens_for_a_user_and_tab_ends_the_one_it_replaces(
    hub: LiveHub,
) -> None:
    async with (
        reading(hub, ALICE, [MARKETS], tab="tab-aaaa") as left_behind,
        reading(hub, ALICE, [MARKETS], tab="tab-bbbb") as other_tab,
        reading(hub, BOB, [MARKETS], tab="tab-aaaa") as other_user,
        # Opened last, under the tab of the first.
        reading(hub, ALICE, [LEADERBOARD], tab="tab-aaaa") as replacing,
    ):
        await settled()

        assert left_behind.ended
        assert (other_tab.ended, other_user.ended, replacing.ended) == (False, False, False)
        assert hub.open_streams == 3

        hub.publish([event(MARKETS, text="a"), event(LEADERBOARD, text="b")])
        await settled()

        assert left_behind.texts == []
        assert (other_tab.texts, other_user.texts, replacing.texts) == (["a"], ["a"], ["b"])


async def test_a_stream_left_behind_gives_its_place_back_to_the_one_that_replaces_it(
    clock: FrozenClock,
) -> None:
    hub = LiveHub(live_settings(max_streams=1), clock)

    async with (
        hub.open(ALICE, "tab-aaaa", [MARKETS]) as left_behind,
        reading(hub, ALICE, [MARKETS], tab="tab-aaaa") as replacing,
    ):
        assert hub.open_streams == 1
        assert [frame.event for frame in await everything(left_behind)] == ["ready"]
        assert replacing.names == ["ready"]

        with pytest.raises(ServiceUnavailableError):
            async with hub.open(BOB, "tab-aaaa", []):
                raise AssertionError("the hub holds one stream")


async def test_a_replaced_stream_whose_reader_leaves_later_takes_nothing_from_its_successor(
    hub: LiveHub,
) -> None:
    async with AsyncExitStack() as later:
        async with reading(hub, ALICE, [MARKETS], tab="tab-aaaa") as first:
            second = await later.enter_async_context(reading(hub, ALICE, [MARKETS], tab="tab-aaaa"))
            await settled()
            assert first.ended
        # The reader of the first has left its block now.
        hub.publish([event(MARKETS, text="a")])
        await settled()
        assert (second.texts, hub.open_streams) == (["a"], 1)

        # The second is still the stream of its tab: a third ends it.
        async with reading(hub, ALICE, [MARKETS], tab="tab-aaaa") as third:
            await settled()
            assert (second.ended, third.ended, hub.open_streams) == (True, False, 1)


async def test_an_event_that_ends_the_streams_of_a_user_reaches_them_first_and_ends_nobody_elses(
    hub: LiveHub,
) -> None:
    async with (
        reading(hub, ALICE, [MARKETS], tab="tab-aaaa") as one,
        reading(hub, ALICE, tab="tab-bbbb") as two,
        reading(hub, BOB, [MARKETS]) as bob,
    ):
        hub.publish(
            [
                event(MARKETS, text="before"),
                event(
                    own(ALICE),
                    to=Audience.users([ALICE.id]),
                    name="me.changed",
                    ends_streams_of=ALICE.id,
                ),
                event(MARKETS, text="after"),
            ]
        )
        await settled()

        assert one.names == ["ready", "market.status", "me.changed"]
        assert two.names == ["ready", "me.changed"]
        assert (one.ended, two.ended) == (True, True)
        assert bob.texts == ["before", "after"]
        assert (bob.ended, hub.open_streams) == (False, 1)


async def test_the_streams_of_a_user_end_also_when_the_event_that_says_so_is_dropped(
    hub: LiveHub, caplog: pytest.LogCaptureFixture
) -> None:
    async with reading(hub, ALICE) as alice, reading(hub, BOB) as bob:
        with caplog.at_level(logging.ERROR, logger="app.services.live_hub"):
            hub.publish(
                [
                    LiveEvent(
                        (own(ALICE),),
                        Audience.users([ALICE.id]),
                        Raw("me.changed", "{\n}"),
                        ends_streams_of=ALICE.id,
                    )
                ]
            )
        await settled()

        assert (alice.names, alice.ended) == (["ready"], True)
        assert (bob.ended, hub.open_streams) == (False, 1)


async def test_a_stream_that_leaves_gives_its_place_back_and_leaves_no_trace(
    hub: LiveHub,
) -> None:
    async with reading(hub, ALICE, [MARKETS, MARKET]), reading(hub, BOB, [MARKET]):
        assert hub.open_streams == 2
        async with reading(hub, ADMIN, [BROADCAST]):
            assert hub.open_streams == 3
        assert hub.open_streams == 2

    assert hub.open_streams == 0
    # The indexes hold nothing of a stream that left: a process that runs for weeks
    # keeps no entry per market or per user that was ever followed.
    assert (hub._streams, hub._followers, hub._by_tab) == (set(), {}, {})

    hub.publish([event(MARKETS, MARKET, BROADCAST)])
    hub.end_streams_of(ALICE.id)


async def test_a_reader_that_fails_gives_its_place_back(hub: LiveHub) -> None:
    async def read_and_fail() -> None:
        async with hub.open(ALICE, "tab-0001", [MARKETS]) as frames:
            await anext(frames)
            raise RuntimeError("the connection is gone")

    with pytest.raises(RuntimeError, match="the connection is gone"):
        await read_and_fail()

    assert hub.open_streams == 0


# --- The ticker ------------------------------------------------------------------------


async def test_a_round_of_the_ticker_offers_every_stream_a_beat(
    hub: LiveHub, clock: FrozenClock
) -> None:
    async with reading(hub, ALICE, [MARKETS]) as alice, reading(hub, BOB) as bob:
        clock.advance(timedelta(seconds=15))
        hub.tick()
        await settled()

        assert alice.names == bob.names == ["ready", "beat"]
        beat = alice.frames[1]
        assert beat is bob.frames[1]
        assert json.loads(beat.data) == {"type": "beat", "ts": _as_json(clock.now())}
        assert beat.wire == f"event: beat\ndata: {beat.data}\n\n".encode()
        assert beat.retry is None


async def test_a_stream_past_its_life_is_ended_after_what_it_was_offered_has_gone_out(
    clock: FrozenClock,
) -> None:
    settings = live_settings()
    hub = LiveHub(settings, clock)

    async with hub.open(ALICE, "tab-0001", [MARKETS]) as frames:
        # Until 80% of the longest life no stream is ended.
        clock.advance(timedelta(seconds=LIFE_SHARE_MIN * settings.stream_max_seconds - 1))
        hub.tick()
        assert hub.open_streams == 1

        hub.publish([event(MARKETS, text="offered")])
        clock.advance(timedelta(seconds=(1 - LIFE_SHARE_MIN) * settings.stream_max_seconds + 1))
        hub.tick()

        assert hub.open_streams == 0
        hub.publish([event(MARKETS, text="too late")])
        assert [frame.event for frame in await everything(frames)] == [
            "ready",
            "beat",
            "market.status",
        ]


async def test_the_life_of_a_stream_and_the_wait_before_the_next_are_drawn_within_their_ranges(
    clock: FrozenClock,
) -> None:
    settings = live_settings(max_streams=200)
    hub = LiveHub(settings, clock)
    longest = settings.stream_max_seconds

    async with AsyncExitStack() as stack:
        streams = [
            await stack.enter_async_context(hub.open(viewer(number), "tab-0001", []))
            for number in range(1, 201)
        ]
        waits = [(await anext(frames)).retry for frames in streams]

        clock.advance(timedelta(seconds=LIFE_SHARE_MIN * longest - 1))
        hub.tick()
        assert hub.open_streams == 200

        clock.advance(timedelta(seconds=(1 - LIFE_SHARE_MIN) * longest / 2))
        hub.tick()
        assert 0 < hub.open_streams < 200

        # A round of the ticker ends a stream, up to a heartbeat after the moment drawn
        # for it: every moment is drawn early enough for the longest life to hold.
        last_round = (1 - LIFE_SHARE_MIN) * longest / 2 + 1 - settings.heartbeat_seconds
        clock.advance(timedelta(seconds=last_round))
        hub.tick()
        assert hub.open_streams == 0

    assert all(
        wait is not None and settings.retry_ms <= wait <= RETRY_FACTOR_MAX * settings.retry_ms
        for wait in waits
    )
    assert len(set(waits)) > 1
    assert (LIFE_SHARE_MIN, RETRY_FACTOR_MAX) == (0.8, 2)


async def test_the_ticker_beats_by_itself_once_the_hub_is_started_and_ends_with_it(
    clock: FrozenClock,
) -> None:
    # A heartbeat of 10 ms, which no setting can be: the test waits in real time.
    hub = LiveHub(live_settings(heartbeat_seconds=0.01), clock)

    async with reading(hub, ALICE) as alice, reading(hub, BOB) as bob:
        await hub.start()
        await hub.start()
        await eventually(lambda: alice.names.count("beat") >= 2)

        await hub.stop()
        await settled()

        assert (alice.ended, bob.ended, hub.open_streams) == (True, True, 0)
        beats = alice.names.count("beat")
        await asyncio.sleep(0.05)
        assert alice.names.count("beat") == beats
        await hub.stop()


async def test_stopping_a_hub_that_was_never_started_ends_every_stream(hub: LiveHub) -> None:
    async with hub.open(ALICE, "tab-0001", [MARKETS]) as frames, reading(hub, BOB) as bob:
        hub.publish([event(MARKETS, text="waits on the queue")])

        await hub.stop()
        await settled()

        assert (bob.ended, hub.open_streams) == (True, 0)
        # At once: what waited is not handed out.
        assert [frame.event for frame in await everything(frames)] == ["ready"]


async def test_a_hub_that_was_stopped_opens_no_stream_and_starts_no_ticker(
    hub: LiveHub, caplog: pytest.LogCaptureFixture
) -> None:
    await hub.stop()
    await hub.start()

    with (
        caplog.at_level(logging.WARNING, logger="app.services.live_hub"),
        pytest.raises(ServiceUnavailableError) as refused,
    ):
        async with hub.open(ALICE, "tab-0001", [MARKETS]):
            raise AssertionError("a stopped hub opened a stream")

    assert refused.value.message == STREAM_UNAVAILABLE_MESSAGE
    assert (hub.open_streams, hub._ticker, caplog.records) == (0, None, [])
    # What is published now reaches nobody and fails nobody.
    hub.publish([event(MARKETS)])


# --- Publishing cannot fail ------------------------------------------------------------


class Broken:
    """A body that cannot be written."""

    type = "market.quote"

    def model_dump_json(self) -> str:
        raise RuntimeError("no JSON today")


async def test_publishing_raises_nothing_whatever_an_event_or_a_watcher_does(
    hub: LiveHub, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[str] = []

    def fails(published: LiveEvent) -> None:
        raise RuntimeError("the watcher failed")

    hub.watch(fails)
    hub.watch(lambda published: seen.append(published.payload.type))

    async with reading(hub, ALICE, [MARKETS]) as alice:
        with caplog.at_level(logging.ERROR, logger="app.services.live_hub"):
            # A plain call: there is nothing to await.
            returned = hub.publish(  # type: ignore[func-returns-value]
                [
                    LiveEvent((MARKETS,), Audience.everyone(), Broken()),
                    event(MARKETS, text="after the broken one"),
                ]
            )
        await settled()

        assert returned is None
        assert alice.texts == ["after the broken one"]
    assert seen == ["market.status"]
    assert [record.getMessage() for record in caplog.records] == [
        "A live event was not published",
        "A watcher of the event stream failed on a 'market.status' event",
    ]


async def test_a_watcher_is_told_every_event_after_the_streams_and_may_publish(
    hub: LiveHub,
) -> None:
    def answers(published: LiveEvent) -> None:
        if published.payload.type == "market.quote":
            hub.publish([event(LEADERBOARD, name="leaderboard.changed")])

    hub.watch(answers)

    async with reading(hub, ALICE, [MARKETS, LEADERBOARD]) as alice:
        hub.publish([event(MARKETS, name="market.quote"), event(MARKETS, name="market.status")])
        await settled()

        assert alice.names == ["ready", "market.quote", "leaderboard.changed", "market.status"]


async def test_a_watcher_hears_an_event_nobody_follows(hub: LiveHub) -> None:
    seen: list[LiveEvent] = []
    hub.watch(seen.append)
    unheard = event(OTHER_MARKET, to=Audience.users([]))

    hub.publish([unheard])

    assert seen == [unheard]
