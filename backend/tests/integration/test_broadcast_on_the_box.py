"""The broadcast service on the media box as it is built (SPEC §6, §9).

The other tests of a broadcast run on `FakeMediaBox`, which keeps the rules of the box
the API's answers depend on. Here the box of `backend/mediabox` itself stands behind the
socket, in this process and in front of the stand-in ffmpeg of its own tests, and the
application goes through one life of a live stream, a box that starts again, and a
start that hands a broadcast over. What the fake promises of the box is held to the box.

Postgres and Redis are the containers of the suite; no Docker image of the box and no
codec is needed.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Final
from uuid import UUID

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.core.enums import BroadcastEndReason, BroadcastStatus
from app.services.broadcasts import MEDIA_UNAVAILABLE_MESSAGE
from tests.support.broadcasts import PART, Calls, broadcasts_of, refusal, stored
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.media import (
    STAND_IN_SEGMENT_BYTES,
    BoxAsBuilt,
    behind,
    box_workspace,
    later,
    until,
    webm,
)

pytestmark = pytest.mark.integration

SEGMENT: Final[int] = STAND_IN_SEGMENT_BYTES
"""The stand-in for ffmpeg writes a segment per this many bytes it reads."""

OTHER_PART: Final[str] = "part-0002"


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    with box_workspace(monkeypatch) as directory:
        yield directory


@pytest.fixture
async def built(workspace: Path) -> AsyncIterator[BoxAsBuilt]:
    async with BoxAsBuilt(workspace) as listening:
        yield listening


@pytest.fixture
def container_settings(container_settings: Settings, built: BoxAsBuilt) -> Settings:
    """The settings of the application, with the box as built behind them."""
    return behind(container_settings, built)


@pytest.fixture
def calls(client: httpx.AsyncClient, as_user: ActAs) -> Calls:
    return Calls(client, as_user)


async def test_a_live_stream_from_its_start_to_its_end_on_the_box_as_built(
    calls: Calls, container: Container, factory: Factory, built: BoxAsBuilt
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market, other_market = await factory.market(), await factory.market()

    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    broadcast_id = UUID(live["id"])
    assert built.held() == [broadcast_id]

    # Three segments' worth of video: the box says playable once its playlist lists them.
    for seq, data in enumerate([webm(SEGMENT), later(SEGMENT), later(SEGMENT)]):
        answer = await calls.chunk(admin, broadcast_id, seq, data)
        assert answer.json() == {"restart": False, "next_seq": seq + 1}

    async def playable() -> bool:
        return (await container.media.status()).live[0].playable

    await until(playable)
    assert (await calls.chunk(admin, broadcast_id, 3, later(10))).json() == {
        "restart": False,
        "next_seq": 4,
    }
    seen = (await calls.read(member, market.id)).json()
    assert (seen["broadcast"]["on_air"], seen["viewer"]["can_watch"]) == (True, True)
    # The address a viewer plays is the file the box wrote: the proxy serves the
    # directory of the broadcast under it.
    assert seen["broadcast"]["playlist_url"] == f"/api/broadcasts/{broadcast_id}/hls/index.m3u8"
    assert "index.m3u8" in built.files_of(broadcast_id)
    assert (await calls.access(member, broadcast_id)).status_code == 200

    again = await calls.chunk(admin, broadcast_id, 3, later(10))
    after_a_gap = await calls.chunk(admin, broadcast_id, 9, later(10))
    assert again.json() == {"restart": False, "next_seq": 4}
    assert after_a_gap.json() == {"restart": True, "next_seq": 0}

    ended = (await calls.end(admin, broadcast_id)).json()["broadcast"]
    assert (ended["status"], ended["on_air"], ended["playlist_url"]) == ("ENDED", False, None)
    assert built.files_of(broadcast_id) == [], "nothing of the video is kept"
    assert built.box.finished.is_set(), "the box process ends with its last broadcast"

    # Until its container is back nobody answers: a start writes nothing.
    too_early = await calls.start_as_admin(admin, other_market.id)
    assert refusal(too_early) == (503, "SERVICE_UNAVAILABLE", MEDIA_UNAVAILABLE_MESSAGE, {})
    assert await broadcasts_of(container, other_market) == []

    await built.start_again()
    assert (await calls.start_as_admin(admin, other_market.id)).status_code == 201


async def test_a_box_that_started_again_holds_the_broadcast_again_after_the_next_piece(
    calls: Calls, factory: Factory, built: BoxAsBuilt
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    broadcast_id = UUID(live["id"])
    await calls.chunk(admin, broadcast_id, 0, webm(SEGMENT))
    await built.start_again()
    assert built.held() == []

    finds_out = await calls.chunk(admin, broadcast_id, 1, later(SEGMENT))
    fresh = await calls.chunk(admin, broadcast_id, 0, webm(SEGMENT), part=OTHER_PART)
    goes_on = await calls.chunk(admin, broadcast_id, 1, later(SEGMENT), part=OTHER_PART)

    assert finds_out.json() == {"restart": True, "next_seq": 0}
    assert built.held() == [broadcast_id]
    assert fresh.json() == {"restart": False, "next_seq": 1}
    assert goes_on.json() == {"restart": False, "next_seq": 2}


async def test_the_first_piece_of_a_part_is_taken_by_a_box_that_started_again(
    calls: Calls, factory: Factory, built: BoxAsBuilt
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = (await calls.start_as_admin(admin, market.id)).json()["broadcast"]
    await built.start_again()

    first = await calls.chunk(admin, live["id"], 0, webm(SEGMENT))

    assert first.json() == {"restart": False, "next_seq": 1}
    assert built.held() == [UUID(live["id"])]


async def test_a_start_hands_what_the_clock_ended_to_the_box_which_swaps_without_going_away(
    calls: Calls, container: Container, factory: Factory, built: BoxAsBuilt, clock: FrozenClock
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    first_market, second_market = await factory.market(), await factory.market()
    first = (await calls.start_as_admin(admin, first_market.id)).json()["broadcast"]
    await calls.chunk(admin, first["id"], 0, webm(SEGMENT))
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    second = (await calls.start_as_admin(other_admin, second_market.id)).json()["broadcast"]

    row = await stored(container, UUID(first["id"]))
    assert (row.status, row.end_reason) == (BroadcastStatus.ENDED, BroadcastEndReason.STALLED)
    assert built.held() == [UUID(second["id"])]
    assert built.files_of(UUID(first["id"])) == []
    assert not built.box.finished.is_set(), "the box did not go away in between"
    taken = await calls.chunk(other_admin, second["id"], 0, webm(SEGMENT), part=PART)
    assert taken.json() == {"restart": False, "next_seq": 1}
