"""The API's client of the media box, against a socket of the test (SPEC §9).

The client speaks four request lines and reads one answer line each. These tests hold
the lines byte for byte, what each answer becomes, and the one thing every failure of
the box is to the API: `MediaUnavailableError` within `BROADCAST_MEDIA_TIMEOUT_MS`, and
False from `ping`, which raises nothing.

Nothing here needs a data store. The box is `FakeMediaBox` (tests/support/media.py);
`test_media_client_and_box.py` holds the same client against the box as it is built.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Final
from uuid import UUID, uuid7

import pytest

from app.core.config import Settings
from app.integrations.media import (
    BoxStatus,
    ChunkAnswer,
    ChunkRefusal,
    HeldBroadcast,
    MediaClient,
    MediaUnavailableError,
)
from app.integrations.media.client import ANSWER_LINE_BYTES, STOP_IDS
from tests.support.media import FakeMediaBox, behind, later, webm

PART: Final[str] = "part-0001"

TIMEOUT_MS: Final[int] = 300
"""`BROADCAST_MEDIA_TIMEOUT_MS` of these tests: short, so that a box that says nothing
costs a test a third of a second."""

SLACK_SECONDS: Final[float] = 2.0
"""What a loaded machine may add to one timeout before a test calls it late."""

ONE: Final[UUID] = UUID("0199c1d4-0000-7000-8000-000000000001")
TWO: Final[UUID] = UUID("0199c1d4-0000-7000-8000-000000000002")


@pytest.fixture
async def box() -> AsyncIterator[FakeMediaBox]:
    async with FakeMediaBox() as listening:
        yield listening


@pytest.fixture
def client(base_settings: Settings, box: FakeMediaBox) -> MediaClient:
    return MediaClient(behind(base_settings, box, media_timeout_ms=TIMEOUT_MS).broadcast)


# --- The lines ----------------------------------------------------------------------


async def test_each_request_is_the_line_the_box_takes(
    client: MediaClient, box: FakeMediaBox
) -> None:
    await client.status()
    await client.start(ONE)
    await client.chunk(ONE, PART, 0, webm(64))
    await client.start(TWO, stop=[ONE])
    await client.stop(TWO)

    assert box.lines == [
        b'{"op":"status"}',
        b'{"op":"start","id":"0199c1d4-0000-7000-8000-000000000001"}',
        b'{"op":"chunk","id":"0199c1d4-0000-7000-8000-000000000001",'
        b'"part":"part-0001","seq":0,"size":64}',
        b'{"op":"start","id":"0199c1d4-0000-7000-8000-000000000002",'
        b'"stop":["0199c1d4-0000-7000-8000-000000000001"]}',
        b'{"op":"stop","id":"0199c1d4-0000-7000-8000-000000000002"}',
    ]


async def test_the_bytes_of_a_piece_follow_its_line_and_arrive_whole(
    client: MediaClient, box: FakeMediaBox, base_settings: Settings
) -> None:
    largest = webm(base_settings.broadcast.chunk_max_bytes)
    await client.start(ONE)

    await client.chunk(ONE, PART, 0, largest)
    await client.chunk(ONE, PART, 1, later(1))

    assert [(piece.part, piece.seq, len(piece.data)) for piece in box.pieces] == [
        (PART, 0, len(largest)),
        (PART, 1, 1),
    ]
    assert box.bytes_of(ONE) == largest + later(1)


async def test_a_start_that_hands_over_the_most_broadcasts_fits_the_line_of_the_box(
    client: MediaClient, box: FakeMediaBox
) -> None:
    handed_over = [uuid7() for _ in range(STOP_IDS)]

    assert await client.start(ONE, stop=handed_over) is True

    (line,) = box.lines
    assert len(line) <= 512
    assert box.requests[0]["stop"] == [str(other) for other in handed_over]


async def test_a_start_that_hands_over_more_than_the_box_takes_sends_nothing(
    client: MediaClient, box: FakeMediaBox
) -> None:
    with pytest.raises(ValueError, match="at most 8"):
        await client.start(ONE, stop=[uuid7() for _ in range(STOP_IDS + 1)])

    assert box.lines == []


# --- The answers --------------------------------------------------------------------


async def test_a_start_says_whether_the_broadcast_has_a_place(
    client: MediaClient, box: FakeMediaBox
) -> None:
    assert await client.start(ONE) is True
    assert await client.start(ONE) is True, "a broadcast the box holds keeps its place"
    assert await client.start(TWO) is False, "the box of the tests holds one"
    assert box.held == [ONE]

    assert await client.start(TWO, stop=[ONE]) is True
    assert box.held == [TWO]


async def test_a_piece_is_taken_acknowledged_or_refused(
    client: MediaClient, box: FakeMediaBox
) -> None:
    await client.start(ONE)

    first = await client.chunk(ONE, PART, 0, webm())
    second = await client.chunk(ONE, PART, 1, later())
    again = await client.chunk(ONE, PART, 1, later())
    third = await client.chunk(ONE, PART, 2, later())
    skipped = await client.chunk(ONE, PART, 9, later())
    of_nobody = await client.chunk(TWO, PART, 0, webm())
    box.chunk_error = "busy"
    busy = await client.chunk(ONE, PART, 3, later())

    assert first == ChunkAnswer(refusal=None, next_seq=1, playable=False)
    assert second == again == ChunkAnswer(refusal=None, next_seq=2, playable=False)
    assert third == ChunkAnswer(refusal=None, next_seq=3, playable=True)
    assert (first.taken, skipped.taken) == (True, False)
    assert skipped == ChunkAnswer(refusal=ChunkRefusal.RESTART, next_seq=0, playable=False)
    assert of_nobody == ChunkAnswer(refusal=ChunkRefusal.UNKNOWN, next_seq=0, playable=False)
    assert busy == ChunkAnswer(refusal=ChunkRefusal.BUSY, next_seq=0, playable=False)
    assert [piece.seq for piece in box.pieces] == [0, 1, 2], "a repeat is not taken twice"


async def test_the_status_is_what_the_box_holds(client: MediaClient, box: FakeMediaBox) -> None:
    assert await client.status() == BoxStatus(capacity=1, live=())

    await client.start(ONE)
    await client.chunk(ONE, PART, 0, webm())

    assert await client.status() == BoxStatus(
        capacity=1, live=(HeldBroadcast(id=ONE, next_seq=1, playable=False),)
    )


async def test_a_stop_is_fine_for_a_broadcast_the_box_does_not_hold(
    client: MediaClient, box: FakeMediaBox
) -> None:
    await client.start(ONE)

    await client.stop(ONE)
    await client.stop(TWO)

    assert box.held == []
    assert await client.ping() is True


# --- A box that is not there, or says nothing one can use ---------------------------


async def requests_of(client: MediaClient) -> list[tuple[str, object]]:
    """Each request of the client, sent once, with what became of it: its value, or the
    error it raised."""

    async def outcome(request: Awaitable[object]) -> object:
        try:
            return await request
        except MediaUnavailableError as error:
            return error

    return [
        ("status", await outcome(client.status())),
        ("start", await outcome(client.start(ONE))),
        ("chunk", await outcome(client.chunk(ONE, PART, 0, webm()))),
        ("stop", await outcome(client.stop(ONE))),
    ]


async def assert_unavailable(client: MediaClient) -> None:
    """Every request raises `MediaUnavailableError` within one timeout, and `ping` says
    False without raising."""
    began = time.monotonic()
    outcomes = await requests_of(client)
    pinged = await client.ping()
    took = time.monotonic() - began

    assert [(name, type(outcome)) for name, outcome in outcomes] == [
        (name, MediaUnavailableError) for name in ("status", "start", "chunk", "stop")
    ]
    assert pinged is False
    assert took < 5 * (TIMEOUT_MS / 1000 + SLACK_SECONDS), "one timeout per request, no more"


async def test_no_socket_file_is_a_box_that_is_unavailable(
    client: MediaClient, box: FakeMediaBox
) -> None:
    await box.gone()
    assert not box.has_socket_file

    await assert_unavailable(client)


async def test_a_socket_file_nobody_listens_behind_is_a_box_that_is_unavailable(
    client: MediaClient, box: FakeMediaBox
) -> None:
    """The container is between two processes: the file of the one before is there."""
    await box.gone()
    box.leave_socket_file_behind()
    assert box.has_socket_file

    await assert_unavailable(client)


async def test_a_box_that_says_nothing_is_unavailable_after_one_timeout(
    client: MediaClient, box: FakeMediaBox
) -> None:
    box.hang()
    began = time.monotonic()

    with pytest.raises(MediaUnavailableError, match="in time"):
        await client.start(ONE)

    took = time.monotonic() - began
    assert TIMEOUT_MS / 1000 <= took < TIMEOUT_MS / 1000 + SLACK_SECONDS
    assert await client.ping() is False


@pytest.mark.parametrize(
    "answer",
    [
        b"",
        b'{"ok":tr',
        b"not json\n",
        b"[]\n",
        b'"ok"\n',
        b'{"ok":true,"pad":"' + b"x" * 5000 + b'"}\n',
        b'{"ok":false,"error":"bad_request"}\n',
        b'{"ok":false,"error":"something else"}\n',
        b"{}\n",
    ],
    ids=[
        "no-answer-at-all",
        "an-answer-that-ends-early",
        "no-json",
        "a-list",
        "a-string",
        "five-kilobytes",
        "bad-request",
        "an-error-the-client-does-not-know",
        "an-object-that-says-nothing",
    ],
)
async def test_an_answer_that_is_none_is_a_box_that_is_unavailable(
    client: MediaClient, box: FakeMediaBox, answer: bytes
) -> None:
    assert len(b'{"ok":true,"pad":"' + b"x" * 5000 + b'"}') > ANSWER_LINE_BYTES
    box.raw_answer = answer

    await assert_unavailable(client)


@pytest.mark.parametrize(
    "answer",
    [
        b'{"ok":true}\n',
        b'{"ok":true,"next_seq":"1","playable":true}\n',
        b'{"ok":true,"next_seq":true,"playable":true}\n',
        b'{"ok":true,"next_seq":1}\n',
    ],
    ids=["no-next-seq", "a-next-seq-that-is-text", "a-next-seq-that-is-a-boolean", "no-playable"],
)
async def test_a_piece_is_not_taken_on_an_answer_that_does_not_say_what_comes_next(
    client: MediaClient, box: FakeMediaBox, answer: bytes
) -> None:
    box.raw_answer = answer

    with pytest.raises(MediaUnavailableError):
        await client.chunk(ONE, PART, 0, webm())


async def test_a_status_that_is_none_is_no_ping(client: MediaClient, box: FakeMediaBox) -> None:
    for answer in (
        b'{"ok":true}\n',
        b'{"ok":true,"capacity":"1","live":[]}\n',
        b'{"ok":true,"capacity":1,"live":[{"id":"no id","next_seq":0,"playable":false}]}\n',
        b'{"ok":true,"capacity":1,"live":[7]}\n',
        b'{"ok":false,"capacity":1,"live":[]}\n',
    ):
        box.raw_answer = answer
        with pytest.raises(MediaUnavailableError):
            await client.status()
        assert await client.ping() is False, answer


async def test_a_socket_path_longer_than_a_socket_takes_is_a_box_that_is_unavailable(
    base_settings: Settings, tmp_path: Path
) -> None:
    """What the client is given by mistake does not become another kind of error."""
    too_long = str(tmp_path / ("x" * 120) / "box.sock")
    client = MediaClient(
        base_settings.broadcast.model_copy(
            update={"media_socket": too_long, "media_timeout_ms": TIMEOUT_MS}
        )
    )

    with pytest.raises(MediaUnavailableError):
        await client.start(ONE)
    assert await client.ping() is False


async def test_requests_of_one_moment_each_have_a_connection_of_their_own(
    client: MediaClient, box: FakeMediaBox
) -> None:
    await client.start(ONE)
    pieces = [webm()] + [later() for _ in range(9)]

    for seq, data in enumerate(pieces):
        answer, status, pinged = await asyncio.gather(
            client.chunk(ONE, PART, seq, data), client.status(), client.ping()
        )
        assert (answer.next_seq, status.capacity, pinged) == (seq + 1, 1, True)

    assert box.bytes_of(ONE) == b"".join(pieces)
