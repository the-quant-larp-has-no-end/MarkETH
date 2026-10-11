"""The API's client and the media box as it is built, on one socket (SPEC §9).

The two share no code: the box is another image, and the client speaks the lines the
box documents. Here the box of `backend/mediabox` runs in this process, behind a Unix
socket, in front of the stand-in ffmpeg of its own tests, and `MediaClient` asks it what
the broadcast service asks. A line one side writes and the other does not read fails
here, without Docker and without a codec.

The socket lies in a directory of its own under `/tmp`: its path has to fit the 104
bytes macOS gives a Unix socket.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Final
from uuid import UUID

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
from tests.support.media import (
    SOCKET_NAME,
    STAND_IN_SEGMENT_BYTES,
    BoxAsBuilt,
    box_workspace,
    later,
    until,
    webm,
)

SEGMENT: Final[int] = STAND_IN_SEGMENT_BYTES
"""The stand-in for ffmpeg writes a segment per this many bytes it reads."""

ONE: Final[UUID] = UUID("0199c1d4-0000-7000-8000-000000000001")
TWO: Final[UUID] = UUID("0199c1d4-0000-7000-8000-000000000002")
THREE: Final[UUID] = UUID("0199c1d4-0000-7000-8000-000000000003")

PART: Final[str] = "part-0001"
OTHER_PART: Final[str] = "part-0002"

RESTART: Final[ChunkAnswer] = ChunkAnswer(refusal=ChunkRefusal.RESTART, next_seq=0, playable=False)
UNKNOWN: Final[ChunkAnswer] = ChunkAnswer(refusal=ChunkRefusal.UNKNOWN, next_seq=0, playable=False)
BUSY: Final[ChunkAnswer] = ChunkAnswer(refusal=ChunkRefusal.BUSY, next_seq=0, playable=False)


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A short directory for the socket and the HLS files, with the stand-in first on
    the PATH under the name `ffmpeg`."""
    with box_workspace(monkeypatch) as directory:
        yield directory


@pytest.fixture
async def built(workspace: Path) -> AsyncIterator[BoxAsBuilt]:
    """The box behind its socket, as the API reaches it."""
    async with BoxAsBuilt(workspace) as listening:
        yield listening


@pytest.fixture
def client(base_settings: Settings, workspace: Path) -> MediaClient:
    """The client the container builds, pointed at the socket of the test."""
    socket_path = str(workspace / SOCKET_NAME)
    return MediaClient(base_settings.broadcast.model_copy(update={"media_socket": socket_path}))


def taken(next_seq: int) -> ChunkAnswer:
    """What the box says of a piece it holds, before the broadcast is playable."""
    return ChunkAnswer(refusal=None, next_seq=next_seq, playable=False)


async def test_the_box_and_the_api_read_the_same_settings(
    base_settings: Settings, built: BoxAsBuilt
) -> None:
    """What the API refuses before it asks, the box would refuse too: both sides have
    the limits from the same variables."""
    api, box = base_settings.broadcast, built.settings

    assert (box.max_live, box.chunk_max_bytes) == (api.max_live, api.chunk_max_bytes)
    assert (box.stall_seconds, box.max_minutes) == (api.stall_seconds, api.max_minutes)
    assert (box.hls_segment_seconds, box.hls_playlist_segments) == (
        api.hls_segment_seconds,
        api.hls_playlist_segments,
    )


async def test_a_broadcast_takes_its_place_and_gives_it_up(
    client: MediaClient, built: BoxAsBuilt
) -> None:
    assert await client.ping() is True
    assert await client.status() == BoxStatus(capacity=1, live=())

    assert await client.start(ONE) is True
    assert await client.start(ONE) is True, "a broadcast the box holds keeps its place"
    assert await client.start(TWO) is False, "the box holds BROADCAST_MAX_LIVE at most"
    assert await client.status() == BoxStatus(
        capacity=1, live=(HeldBroadcast(id=ONE, next_seq=0, playable=False),)
    )

    assert await client.start(TWO, stop=[ONE]) is True, "handed over without going away"
    assert [held.id for held in (await client.status()).live] == [TWO]
    await client.stop(THREE)
    assert await client.ping() is True, "a stop for a broadcast it does not hold ends nothing"


async def test_pieces_are_taken_in_order_acknowledged_when_repeated_and_refused_after_a_gap(
    client: MediaClient, built: BoxAsBuilt
) -> None:
    await client.start(ONE)

    assert await client.chunk(ONE, PART, 0, webm(400)) == taken(1)
    assert await client.chunk(ONE, PART, 1, later(300)) == taken(2)
    assert await client.chunk(ONE, PART, 1, later(300)) == taken(2), "a repeat"
    assert await client.chunk(ONE, PART, 0, webm(400)) == taken(2), "an earlier repeat"
    assert await client.chunk(ONE, PART, 5, later(300)) == RESTART, "a gap"
    assert await client.chunk(ONE, OTHER_PART, 3, later(300)) == RESTART
    assert await client.chunk(ONE, OTHER_PART, 0, later(300)) == RESTART, "no recording"
    assert await client.chunk(TWO, PART, 0, webm(400)) == UNKNOWN

    assert await client.chunk(ONE, OTHER_PART, 0, webm(400)) == taken(1), "another part"
    assert await client.chunk(ONE, PART, 2, later(300)) == RESTART, "the part before is over"
    assert (await client.status()).live == (HeldBroadcast(id=ONE, next_seq=1, playable=False),)


async def test_the_box_says_playable_with_the_third_segment_and_the_client_hears_it(
    client: MediaClient, built: BoxAsBuilt
) -> None:
    await client.start(ONE)

    async def playable() -> bool:
        return (await client.status()).live[0].playable

    answers = [
        await client.chunk(ONE, PART, 0, webm(SEGMENT)),
        await client.chunk(ONE, PART, 1, later(SEGMENT)),
    ]
    assert [answer.playable for answer in answers] == [False, False]
    assert await playable() is False

    await client.chunk(ONE, PART, 2, later(SEGMENT))
    await until(playable)

    assert await client.chunk(ONE, PART, 3, later(10)) == ChunkAnswer(
        refusal=None, next_seq=4, playable=True
    )


async def test_a_piece_of_the_largest_size_passes_and_a_larger_one_is_no_request_of_the_api(
    client: MediaClient, built: BoxAsBuilt, base_settings: Settings
) -> None:
    """The service refuses a larger body before it asks: a box that sees one answers
    `bad_request`, which to the client is a box that did not do what was asked."""
    most = base_settings.broadcast.chunk_max_bytes
    await client.start(ONE)

    assert await client.chunk(ONE, PART, 0, webm(most)) == taken(1)
    with pytest.raises(MediaUnavailableError):
        await client.chunk(ONE, PART, 1, later(most + 1))
    with pytest.raises(MediaUnavailableError):
        await client.chunk(ONE, "short", 1, later(10))


async def test_a_box_whose_queue_is_full_says_busy_and_takes_nothing(
    client: MediaClient, workspace: Path
) -> None:
    """The stand-in stops reading: what is sent then stays in the pipe and in the queue
    of the box, which holds four pieces of the largest size."""
    piece = 70_000
    async with BoxAsBuilt(workspace, chunk_max_bytes=piece):
        await client.start(ONE)
        assert await client.chunk(ONE, PART, 0, webm(SEGMENT, b"STOPREADING ")) == taken(1)
        seq = 1
        while (answer := await client.chunk(ONE, PART, seq, later(piece))) != BUSY:
            assert answer == taken(seq + 1)
            seq += 1
            assert seq < 40, "the queue of the box never filled"

        assert await client.chunk(ONE, PART, seq, later(piece)) == BUSY
        assert (await client.status()).live[0].next_seq == seq, "nothing of it was taken"
        assert await client.chunk(ONE, PART, seq - 1, later(piece)) == taken(seq), "a repeat"


async def test_the_stop_of_the_last_broadcast_is_answered_and_then_the_box_says_nothing(
    client: MediaClient, built: BoxAsBuilt
) -> None:
    """The box process ends with its last broadcast and its container starts again
    empty. Until then nobody answers: to the API, live video is unavailable."""
    await client.start(ONE)
    await client.chunk(ONE, PART, 0, webm(400))

    await client.stop(ONE)

    assert built.box.finished.is_set()
    assert await client.ping() is False
    for request in (
        client.status(),
        client.start(TWO),
        client.chunk(ONE, PART, 1, later(300)),
        client.stop(ONE),
    ):
        with pytest.raises(MediaUnavailableError, match="without an answer"):
            await request
