"""Places, stops and the two timers: when the box holds what, and when its process ends."""

import asyncio
from collections.abc import Callable

from mediabox.box import Box
from mediabox.settings import Settings
from tests.unit.mediabox.conftest import ONE, THREE, TWO, Clock, Events, StandIn, held, until, webm

A = "part-aaaa"


async def test_a_start_beyond_the_capacity_is_full(box: Box) -> None:
    assert await box.start(ONE, []) == {"ok": True}
    assert await box.start(TWO, []) == {"ok": False, "error": "full"}
    assert [entry["id"] for entry in held(box)] == [ONE]


async def test_a_start_for_an_id_the_box_holds_is_fine_and_changes_nothing(
    box: Box, stand_in: StandIn
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm())
    before = box.status()
    assert await box.start(ONE, []) == {"ok": True}
    assert box.status() == before


async def test_a_start_with_stop_frees_the_place_without_the_process_ending(
    box: Box, stand_in: StandIn, settings: Settings, events: Events
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm(1000))
    await until(lambda: (settings.hls_root / ONE / "seg-000000.ts").exists())
    assert await box.start(TWO, [ONE, THREE]) == {"ok": True}
    assert [entry["id"] for entry in held(box)] == [TWO]
    assert not (settings.hls_root / ONE).exists()
    assert (settings.hls_root / TWO).is_dir()
    assert stand_in.alive() == []
    assert not box.finished.is_set()
    assert not box.ending
    assert box.chunk(ONE, A, 1, b"x" * 100) == {"ok": False, "error": "unknown"}
    assert box.chunk(TWO, A, 0, webm())["ok"] is True
    assert {"event": "stop", "id": ONE, "reason": "handed over"} in events.seen


async def test_an_empty_box_is_cleared_out_before_a_handed_over_place_is_given_away(
    settings: Settings, stand_in: StandIn, events: Events, grace: float
) -> None:
    cleared: list[list[str]] = []
    box = Box(
        settings,
        log=events,
        clear_out=lambda: cleared.append([e["id"] for e in held(box)]),
    )
    await box.start(ONE, [])
    await box.start(TWO, [])  # full: nothing is cleared out
    assert cleared == []
    await box.start(TWO, [ONE])
    # Cleared out once, at the moment the box held nobody: whoever takes the place is not in yet.
    assert cleared == [[]]
    await box.shut_down()


async def test_a_box_that_still_holds_somebody_is_not_cleared_out(
    box_with: Callable[..., Box], stand_in: StandIn, events: Events
) -> None:
    cleared: list[int] = []
    box = box_with(max_live=2)
    box._clear_out = lambda: cleared.append(1)
    await box.start(ONE, [])
    await box.start(TWO, [])
    assert await box.start(THREE, [ONE]) == {"ok": True}
    assert cleared == []
    await box.shut_down()


async def test_a_start_does_not_hand_itself_over(box: Box, settings: Settings) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm())
    assert await box.start(ONE, [ONE]) == {"ok": True}
    assert held(box)[0]["next_seq"] == 1


async def test_stop_deletes_the_directory_and_ends_the_process_when_it_was_the_last(
    box: Box, stand_in: StandIn, settings: Settings
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm(2000))
    await until(lambda: (settings.hls_root / ONE / "index.m3u8").exists())
    assert await box.stop(ONE) == {"ok": True}
    assert not (settings.hls_root / ONE).exists()
    assert stand_in.alive() == []
    assert box.finished.is_set()
    assert box.ending


async def test_stop_of_one_of_two_does_not_end_the_process(
    box_with: Callable[..., Box], stand_in: StandIn
) -> None:
    box = box_with(max_live=2)
    await box.start(ONE, [])
    await box.start(TWO, [])
    assert await box.stop(ONE) == {"ok": True}
    assert not box.finished.is_set()
    assert [entry["id"] for entry in held(box)] == [TWO]
    assert await box.stop(TWO) == {"ok": True}
    assert box.finished.is_set()


async def test_stop_of_an_id_the_box_does_not_hold_is_fine_and_ends_nothing(box: Box) -> None:
    assert await box.stop(ONE) == {"ok": True}
    assert not box.finished.is_set()
    await box.start(TWO, [])
    assert await box.stop(ONE) == {"ok": True}
    assert not box.finished.is_set()
    assert [entry["id"] for entry in held(box)] == [TWO]


async def test_a_stop_kills_an_encoder_that_would_not_end(
    box: Box, stand_in: StandIn, events: Events
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm(400, b"HANG"))
    await until(lambda: any(run["event"] == "hang" for run in stand_in.runs()))
    await asyncio.wait_for(box.stop(ONE), 5)
    assert stand_in.alive() == []
    assert events.named("encoder_exited")[0]["code"] == -9


async def test_a_stop_while_an_encoder_is_still_being_started_leaves_none_behind(
    box: Box, stand_in: StandIn
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm())
    box.chunk(ONE, "part-bbbb", 0, webm())
    box.chunk(ONE, "part-cccc", 0, webm())
    await box.stop(ONE)
    assert stand_in.alive() == []


async def test_the_idle_timer_stops_a_broadcast_and_ends_the_process(
    box: Box, clock: Clock, settings: Settings, events: Events
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm())
    clock.now += settings.stall_seconds - 1
    await box.check_timers()
    assert held(box) != []
    # A repeat is no piece taken: it does not keep a broadcast alive.
    box.chunk(ONE, A, 0, webm())
    clock.now += 1
    await box.check_timers()
    assert held(box) == []
    assert box.finished.is_set()
    assert {"event": "stop", "id": ONE, "reason": "stalled"} in events.seen


async def test_a_piece_taken_starts_the_idle_time_again(
    box: Box, clock: Clock, settings: Settings
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm())
    clock.now += settings.stall_seconds - 1
    box.chunk(ONE, A, 1, b"x" * 100)
    clock.now += settings.stall_seconds - 1
    await box.check_timers()
    assert held(box) != []


async def test_the_age_timer_stops_a_broadcast_a_minute_after_the_limit(
    box: Box, clock: Clock, settings: Settings, events: Events
) -> None:
    await box.start(ONE, [])
    seq = 0
    for _ in range(settings.max_minutes + 1):
        clock.now += 59.5
        box.chunk(ONE, A, seq, webm() if seq == 0 else b"x" * 100)
        seq += 1
        await box.check_timers()
    assert held(box) != []
    clock.now += 61
    box.chunk(ONE, A, seq, b"x" * 100)
    await box.check_timers()
    assert held(box) == []
    assert {"event": "stop", "id": ONE, "reason": "time limit"} in events.seen
    assert box.finished.is_set()


async def test_shutting_down_ends_everything_it_holds(
    box: Box, stand_in: StandIn, settings: Settings
) -> None:
    await box.start(ONE, [])
    box.chunk(ONE, A, 0, webm(1000))
    await until(lambda: stand_in.started() != [])
    await box.shut_down()
    assert stand_in.alive() == []
    assert not (settings.hls_root / ONE).exists()
    assert box.finished.is_set()
