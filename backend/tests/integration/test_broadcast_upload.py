"""Sending the video of a live stream: `POST /broadcasts/{id}/chunks` (SPEC §6).

A studio posts the pieces of its recording one at a time, each with the part it belongs
to and its place in that part. The API judges the caller, hands the bytes to the media
box and keeps none. The box takes a piece exactly when it is the one its part waits
for: a piece that comes again is acknowledged, and a gap is refused, so that the studio
begins another part with a recorder of its own.

The application runs on Postgres and Redis, with `FakeMediaBox` where the media box
stands (tests/support/media.py).
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, Final
from uuid import uuid7

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.core.enums import BroadcastEndReason, BroadcastStatus, UserStatus
from app.core.errors import SUSPENDED_MESSAGE, ForbiddenError
from app.services.broadcasts import (
    ENDED_MESSAGE,
    MEDIA_UNAVAILABLE_MESSAGE,
    NO_DATA_MESSAGE,
    NOT_STARTED_MESSAGE,
    NOT_THE_BROADCASTER_MESSAGE,
    TOO_LARGE_MESSAGE,
)
from tests.support.broadcasts import PART, Calls, as_json, broadcast, refusal, stored
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.media import FakeMediaBox, behind, later, webm
from tests.support.statements import recorded_statements, writes
from tests.support.streams import MARKETS, broadcast_of, listening, settled

pytestmark = pytest.mark.integration

NOT_FOUND: Final[tuple[int, str, str, dict[str, Any]]] = (404, "NOT_FOUND", "Not found.", {})

MEDIA_UNAVAILABLE: Final[tuple[int, str, str, dict[str, Any]]] = (
    503,
    "SERVICE_UNAVAILABLE",
    MEDIA_UNAVAILABLE_MESSAGE,
    {},
)

OTHER_PART: Final[str] = "part-0002"

SECOND: Final[timedelta] = timedelta(seconds=1)


@pytest.fixture
async def box() -> AsyncIterator[FakeMediaBox]:
    async with FakeMediaBox() as listening_box:
        yield listening_box


@pytest.fixture
def limits() -> dict[str, object]:
    """Other values of the section "Live video" a test runs with."""
    return {}


@pytest.fixture
def container_settings(
    container_settings: Settings, box: FakeMediaBox, limits: dict[str, object]
) -> Settings:
    """The settings of the application, with the media box of the test behind them."""
    return behind(container_settings, box, **limits)


@pytest.fixture
def calls(client: httpx.AsyncClient, as_user: ActAs) -> Calls:
    return Calls(client, as_user)


def taken(response: httpx.Response) -> dict[str, Any]:
    """The answer to a piece that was answered at all."""
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- Who sends ------------------------------------------------------------------------


async def test_only_the_broadcaster_of_a_live_stream_sends_its_video(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    proposer, member = await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    asked = (await calls.request(proposer, market.id)).json()["broadcast"]
    await calls.approve(admin, asked["id"])
    await calls.start(proposer, asked["id"])

    as_member = await calls.chunk(member, asked["id"], 0, webm())
    as_admin = await calls.chunk(admin, asked["id"], 0, webm())
    as_other_admin = await calls.chunk(other_admin, asked["id"], 0, webm())
    as_broadcaster = await calls.chunk(proposer, asked["id"], 0, webm())

    assert refusal(as_member) == NOT_FOUND
    for refused in (as_admin, as_other_admin):
        assert refusal(refused) == (403, "FORBIDDEN", NOT_THE_BROADCASTER_MESSAGE, {})
    assert taken(as_broadcaster) == {"restart": False, "next_seq": 1}
    assert len(box.asked("chunk")) == 1


async def test_a_broadcast_nobody_has_takes_no_video(calls: Calls, factory: Factory) -> None:
    admin = await factory.admin()

    response = await calls.chunk(admin, uuid7(), 0, webm())

    assert refusal(response) == NOT_FOUND


async def test_somebody_who_started_as_an_admin_and_is_none_any_more_sends_nothing(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    """The row says who decided: its own broadcaster. Without the role that decision
    carries nothing, and the broadcast ends by itself."""
    former_admin = await factory.user()
    admin = await factory.admin()
    market = await factory.market()
    live = await broadcast(container, market, former_admin, BroadcastStatus.LIVE)
    assert live.decided_by == live.broadcaster_id == former_admin.id

    response = await calls.chunk(former_admin, live.id, 0, webm())
    with pytest.raises(ForbiddenError):
        await container.broadcasts.take_chunk(
            factory.identity(former_admin), live.id, PART, 0, webm()
        )

    assert refusal(response) == (403, "FORBIDDEN", "You are not allowed to do this.", {})
    assert box.requests == []
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    as_it_stands = (await calls.read(admin, market.id)).json()["broadcast"]
    assert (as_it_stands["status"], as_it_stands["end_reason"]) == ("ENDED", "STALLED")


async def test_a_suspended_account_sends_nothing_and_its_live_stream_ends_by_itself(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    suspended = await factory.user(status=UserStatus.SUSPENDED)
    market = await factory.market(created_by=suspended)
    live = await broadcast(container, market, suspended, BroadcastStatus.LIVE, decided_by=admin)

    response = await calls.chunk(suspended, live.id, 0, webm())

    assert refusal(response) == (403, "FORBIDDEN", SUSPENDED_MESSAGE, {})
    assert box.requests == []
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    as_it_stands = (await calls.read(admin, market.id)).json()["broadcast"]
    assert (as_it_stands["status"], as_it_stands["end_reason"]) == ("ENDED", "STALLED")


@pytest.mark.parametrize(
    ("status", "answered"),
    [
        (
            BroadcastStatus.REQUESTED,
            (409, "CONFLICT", NOT_STARTED_MESSAGE, {"status": "REQUESTED"}),
        ),
        (
            BroadcastStatus.APPROVED,
            (409, "CONFLICT", NOT_STARTED_MESSAGE, {"status": "APPROVED"}),
        ),
        (
            BroadcastStatus.REFUSED,
            (409, "CONFLICT", NOT_STARTED_MESSAGE, {"status": "REFUSED"}),
        ),
        (
            BroadcastStatus.ENDED,
            (409, "CONFLICT", ENDED_MESSAGE, {"status": "ENDED", "end_reason": "ADMIN"}),
        ),
    ],
    ids=["requested", "approved", "refused", "ended"],
)
async def test_video_is_sent_while_the_live_stream_is_on_and_at_no_other_time(
    calls: Calls,
    container: Container,
    factory: Factory,
    box: FakeMediaBox,
    status: BroadcastStatus,
    answered: tuple[int, str, str, dict[str, Any]],
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(
        container,
        market,
        proposer,
        status,
        decided_by=admin,
        end_reason=BroadcastEndReason.ADMIN,
    )

    assert refusal(await calls.chunk(proposer, row.id, 0, webm())) == answered
    assert box.requests == []


async def test_a_piece_for_a_live_stream_the_clock_ended_is_refused_and_the_end_is_stored(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    assert taken(await calls.chunk(admin, live["id"], 0, webm())) == {
        "restart": False,
        "next_seq": 1,
    }
    stall = timedelta(seconds=container.settings.broadcast.stall_seconds)
    clock.advance(stall)

    response = await calls.chunk(admin, live["id"], 1, later())

    assert refusal(response) == (
        409,
        "CONFLICT",
        ENDED_MESSAGE,
        {"status": "ENDED", "end_reason": "STALLED"},
    )
    row = await stored(container, live["id"])
    assert (row.status, row.ended_at) == (BroadcastStatus.ENDED, TEST_NOW + stall)
    assert len(box.pieces) == 1
    assert box.asked("stop") == [{"op": "stop", "id": live["id"]}]


# --- What the box is handed -----------------------------------------------------------


async def test_the_box_receives_the_bytes_that_were_sent_with_their_part_and_their_place(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    recording = [webm(4096, b"\x01"), later(9000, b"\x02"), later(1, b"\x03"), later(70_000)]

    answers = [
        taken(await calls.chunk(admin, live["id"], seq, data)) for seq, data in enumerate(recording)
    ]

    assert answers == [{"restart": False, "next_seq": seq + 1} for seq in range(len(recording))]
    assert [(str(piece.broadcast_id), piece.part, piece.seq) for piece in box.pieces] == [
        (live["id"], PART, seq) for seq in range(len(recording))
    ]
    assert [piece.data for piece in box.pieces] == recording
    assert box.asked("chunk")[2] == {
        "op": "chunk",
        "id": live["id"],
        "part": PART,
        "seq": 2,
        "size": 1,
    }


async def test_a_piece_that_comes_again_is_acknowledged_and_not_taken_twice(
    calls: Calls, factory: Factory, box: FakeMediaBox
) -> None:
    """The answer to a piece got lost: the studio sends the same bytes under the same
    `seq`, and is told to go on where the box stands."""
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await calls.chunk(admin, live["id"], 0, webm())
    first = await calls.chunk(admin, live["id"], 1, later())

    again = await calls.chunk(admin, live["id"], 1, later())
    much_earlier = await calls.chunk(admin, live["id"], 0, webm())

    assert taken(first) == taken(again) == taken(much_earlier) == {"restart": False, "next_seq": 2}
    assert [piece.seq for piece in box.pieces] == [0, 1]


async def test_a_gap_is_refused_so_that_the_recorder_starts_a_fresh_run(
    calls: Calls, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await calls.chunk(admin, live["id"], 0, webm())

    skipped = await calls.chunk(admin, live["id"], 2, later())
    middle_of_another = await calls.chunk(admin, live["id"], 5, later(), part=OTHER_PART)
    no_recording = await calls.chunk(admin, live["id"], 0, later(), part="part-0003")
    fresh = await calls.chunk(admin, live["id"], 0, webm(64, b"f"), part=OTHER_PART)
    goes_on = await calls.chunk(admin, live["id"], 1, later(), part=OTHER_PART)
    the_part_before = await calls.chunk(admin, live["id"], 1, later())

    for refused in (skipped, middle_of_another, no_recording, the_part_before):
        assert taken(refused) == {"restart": True, "next_seq": 0}
    assert taken(fresh) == {"restart": False, "next_seq": 1}
    assert taken(goes_on) == {"restart": False, "next_seq": 2}
    assert [(piece.part, piece.seq) for piece in box.pieces] == [
        (PART, 0),
        (OTHER_PART, 0),
        (OTHER_PART, 1),
    ]


async def test_an_empty_body_and_one_that_is_too_large_never_reach_the_box(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    most = container.settings.broadcast.chunk_max_bytes

    empty = await calls.chunk(admin, live["id"], 0, b"")
    too_large = await calls.chunk(admin, live["id"], 0, webm(most + 1))
    largest = await calls.chunk(admin, live["id"], 0, webm(most))

    assert refusal(empty) == (422, "VALIDATION_ERROR", NO_DATA_MESSAGE, {})
    assert refusal(too_large) == (422, "VALIDATION_ERROR", TOO_LARGE_MESSAGE, {})
    assert taken(largest) == {"restart": False, "next_seq": 1}
    assert [len(piece.data) for piece in box.pieces] == [most]
    assert len(box.asked("chunk")) == 1


async def test_a_piece_that_does_not_say_which_it_is_is_refused_before_anything_is_asked(
    calls: Calls, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]

    short_part = await calls.chunk(admin, live["id"], 0, webm(), part="seven77")
    before_the_first = await calls.chunk(admin, live["id"], -1, webm())

    for refused in (short_part, before_the_first):
        assert refused.status_code == 422
        assert refused.json()["code"] == "VALIDATION_ERROR"
    assert box.asked("chunk") == []


# --- A box that cannot take a piece ---------------------------------------------------


async def test_a_box_whose_queue_is_full_takes_nothing_and_the_studio_sends_again(
    calls: Calls, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await calls.chunk(admin, live["id"], 0, webm())
    box.chunk_error = "busy"

    busy = await calls.chunk(admin, live["id"], 1, later())
    box.chunk_error = None
    again = await calls.chunk(admin, live["id"], 1, later())

    assert refusal(busy) == MEDIA_UNAVAILABLE
    assert taken(again) == {"restart": False, "next_seq": 2}
    assert [piece.seq for piece in box.pieces] == [0, 1]


@pytest.mark.parametrize("limits", [{"media_timeout_ms": 300}])
@pytest.mark.parametrize("trouble", ["gone", "silent"])
async def test_a_box_that_does_not_answer_is_live_video_that_is_unavailable(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, trouble: str
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    if trouble == "gone":
        await box.gone()
    else:
        box.hang()

    response = await calls.chunk(admin, live["id"], 0, webm())

    assert refusal(response) == MEDIA_UNAVAILABLE
    row = await stored(container, live["id"])
    assert (row.status, row.last_chunk_at) == (BroadcastStatus.LIVE, row.started_at)


async def test_a_box_that_started_again_is_given_the_broadcast_again_and_a_fresh_part_goes_on(
    calls: Calls, factory: Factory, box: FakeMediaBox
) -> None:
    """The box holds nothing of what it held before. The piece that finds out is not one
    a part begins with: the studio begins another part."""
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await calls.chunk(admin, live["id"], 0, webm())
    box.restart()
    assert box.held == []

    finds_out = await calls.chunk(admin, live["id"], 1, later())
    fresh = await calls.chunk(admin, live["id"], 0, webm(), part=OTHER_PART)

    assert taken(finds_out) == {"restart": True, "next_seq": 0}
    assert taken(fresh) == {"restart": False, "next_seq": 1}
    assert [str(held) for held in box.held] == [live["id"]]
    assert [request["op"] for request in box.requests] == [
        "start",
        "chunk",
        "chunk",
        "start",
        "chunk",
    ]
    assert box.requests[3] == {"op": "start", "id": live["id"]}, "nothing is handed over"


async def test_a_piece_that_begins_a_part_is_handed_over_again_once_the_box_holds_the_broadcast(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """The first piece of a part meets a box that started again: the studio has just
    begun this part and does not begin another one for it."""
    admin = await factory.admin()
    market = await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE)
    assert box.held == []

    response = await calls.chunk(admin, live.id, 0, webm())

    assert taken(response) == {"restart": False, "next_seq": 1}
    assert [request["op"] for request in box.requests] == ["chunk", "start", "chunk"]
    assert [(piece.part, piece.seq) for piece in box.pieces] == [(PART, 0)]


async def test_a_box_with_no_place_left_takes_no_broadcast_back(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE)
    box.start_full = True

    response = await calls.chunk(admin, live.id, 0, webm())

    assert refusal(response) == MEDIA_UNAVAILABLE
    assert box.pieces == []


async def test_a_broadcast_that_ended_while_its_piece_was_on_its_way_takes_no_place_again(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """An admin ends the broadcast while a piece is at the box. The box, told to stop,
    does not know the broadcast any more. It is not given a place for a broadcast that
    is over: that place would keep the next start out."""
    admin, other_admin = await factory.admin(), await factory.admin(1)
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await calls.chunk(admin, live["id"], 0, webm())
    ending = factory.identity(other_admin)

    async def end_it(request: dict[str, Any]) -> None:
        if request["op"] == "chunk":
            box.before_answer = None
            await container.broadcasts.end(ending, row.id)

    row = await stored(container, live["id"])
    box.before_answer = end_it

    response = await calls.chunk(admin, live["id"], 1, later())

    assert refusal(response) == (
        409,
        "CONFLICT",
        ENDED_MESSAGE,
        {"status": "ENDED", "end_reason": "ADMIN"},
    )
    assert [request["op"] for request in box.requests] == ["start", "chunk", "chunk", "stop"]
    assert box.held == []


# --- On air, and the sign of life -----------------------------------------------------


async def test_a_live_stream_is_on_air_from_the_first_answer_of_the_box_that_says_playable(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market, other_market = await factory.market(), await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    assert box.playable_after == 3
    hub = container.live

    async with (
        listening(hub, factory.identity(member), [broadcast_of(market)], tab="tab-0001") as tab,
        listening(hub, factory.identity(member), [MARKETS], tab="tab-0002") as lists,
        listening(
            hub, factory.identity(member), [broadcast_of(other_market)], tab="tab-0003"
        ) as elsewhere,
    ):
        for seq, data in enumerate([webm(), later()]):
            clock.advance(SECOND)
            await calls.chunk(admin, live["id"], seq, data)
        await settled()
        before = (await calls.read(member, market.id)).json()
        assert (before["broadcast"]["on_air"], before["broadcast"]["playlist_url"]) == (False, None)
        assert before["viewer"]["can_watch"] is False
        assert (await stored(container, live["id"])).on_air_at is None
        assert (tab.bodies, lists.bodies) == ([], [])

        on_air_at = clock.advance(SECOND)
        await calls.chunk(admin, live["id"], 2, later())
        await settled()
        changed = {
            "type": "broadcast.changed",
            "market_id": str(market.id),
            "ts": as_json(on_air_at),
        }
        assert (tab.bodies, lists.bodies, elsewhere.bodies) == ([changed], [changed], [])

        for seq in range(3, 12):
            clock.advance(SECOND)
            await calls.chunk(admin, live["id"], seq, later())
        await settled()
        assert (tab.bodies, lists.bodies) == ([changed], [changed]), "announced once"

    row = await stored(container, live["id"])
    assert row.on_air_at == on_air_at, "stored once"
    after = (await calls.read(member, market.id)).json()
    assert after["broadcast"]["on_air"] is True
    assert after["broadcast"]["playlist_url"] == f"/api/broadcasts/{live['id']}/hls/index.m3u8"
    assert after["viewer"]["can_watch"] is True


async def test_the_sign_of_life_is_written_with_the_first_piece_and_then_once_per_interval(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    """A hundred pieces write once. A broadcast that sends is never stalled, and its row
    is not written with every second of video."""
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    box.playable_after = 10**9
    interval = container.settings.broadcast.heartbeat_interval
    assert interval == timedelta(seconds=container.settings.broadcast.stall_seconds) / 4
    step = interval / 200

    first_at = clock.advance(2 * SECOND)
    with recorded_statements(container) as statements:
        await calls.chunk(admin, live["id"], 0, webm())
        for seq in range(1, 100):
            clock.advance(step)
            await calls.chunk(admin, live["id"], seq, later())

    assert [statement.split()[:2] for statement in statements if writes(statement)] == [
        ["UPDATE", "broadcasts"]
    ]
    assert (await stored(container, live["id"])).last_chunk_at == first_at
    assert len(box.pieces) == 100

    clock.set(first_at + interval - SECOND)
    await calls.chunk(admin, live["id"], 100, later())
    assert (await stored(container, live["id"])).last_chunk_at == first_at

    due_at = clock.advance(SECOND)
    await calls.chunk(admin, live["id"], 101, later())
    assert (await stored(container, live["id"])).last_chunk_at == due_at


async def test_a_live_stream_that_keeps_sending_is_not_stalled(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    stall = timedelta(seconds=container.settings.broadcast.stall_seconds)
    answers = [taken(await calls.chunk(admin, live["id"], 0, webm()))]

    for seq in range(1, 40):
        clock.advance(stall / 8)
        answers.append(taken(await calls.chunk(admin, live["id"], seq, later())))

    assert clock.now() - TEST_NOW > 4 * stall
    assert [answer["restart"] for answer in answers] == [False] * 40
    assert (await calls.read(admin, market.id)).json()["broadcast"]["status"] == "LIVE"


async def test_a_piece_that_arrives_beside_the_end_of_its_broadcast_revives_nothing(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    ended = await broadcast(container, market, admin, BroadcastStatus.ENDED)
    later_on = clock.advance(timedelta(minutes=1))

    async with container.uow_factory() as uow:
        went_on_air = await uow.broadcasts.heartbeat(ended.id, later_on, on_air=True)

    row = await stored(container, ended.id)
    assert went_on_air is False
    assert (row.status, row.last_chunk_at, row.on_air_at) == (
        BroadcastStatus.ENDED,
        TEST_NOW,
        None,
    )
