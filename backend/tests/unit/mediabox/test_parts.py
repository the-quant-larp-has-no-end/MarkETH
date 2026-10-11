"""Parts and sequence numbers, the queue, and the encoder's end."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

from mediabox import playlist
from mediabox.box import Box
from mediabox.broadcast import BUSY, ENCODER_PAUSE_SECONDS, ENCODER_SILENT_SECONDS, RESTART
from mediabox.settings import Settings
from tests.unit.mediabox.conftest import ONE, Clock, Events, StandIn, held, mp4, until, webm

A = "part-aaaa"
B = "part-bbbb"
C = "part-cccc"


def taken(next_seq: int, playable: bool = False) -> dict[str, object]:
    return {"ok": True, "next_seq": next_seq, "playable": playable}


async def started(box: Box) -> Box:
    assert await box.start(ONE, []) == {"ok": True}
    return box


async def test_seq_in_order_is_taken_and_reaches_the_encoder_once(
    box: Box, stand_in: StandIn
) -> None:
    await started(box)
    assert box.chunk(ONE, A, 0, webm(400)) == taken(1)
    assert box.chunk(ONE, A, 1, b"x" * 300) == taken(2)
    assert box.chunk(ONE, A, 2, b"y" * 300) == taken(3)
    await until(lambda: stand_in.started())
    pid = stand_in.started()[0]["pid"]
    await until(lambda: stand_in.bytes_read(pid) == 1000)


async def test_a_repeat_is_acknowledged_and_not_written_twice(box: Box, stand_in: StandIn) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400))
    box.chunk(ONE, A, 1, b"x" * 300)
    assert box.chunk(ONE, A, 1, b"x" * 300) == taken(2)
    assert box.chunk(ONE, A, 0, webm(400)) == taken(2)
    assert box.chunk(ONE, A, 2, b"y" * 300) == taken(3)
    await until(lambda: stand_in.started())
    pid = stand_in.started()[0]["pid"]
    await until(lambda: stand_in.bytes_read(pid) == 1000)
    assert held(box)[0]["next_seq"] == 3


async def test_a_higher_seq_is_restart_and_takes_nothing(box: Box, stand_in: StandIn) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400))
    assert box.chunk(ONE, A, 2, b"x" * 300) == RESTART
    # The part at hand stays the part at hand: the piece it waits for is still taken.
    assert box.chunk(ONE, A, 1, b"x" * 300) == taken(2)
    await until(lambda: stand_in.started())
    await until(lambda: stand_in.bytes_read(stand_in.started()[0]["pid"]) == 700)


async def test_a_part_that_begins_ends_the_encoder_and_starts_another(
    box: Box, stand_in: StandIn, settings: Settings
) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(1000))
    box.chunk(ONE, A, 1, b"x" * 1000)
    await until(lambda: len(list((settings.hls_root / ONE).glob("seg-*.ts"))) == 2)
    assert box.chunk(ONE, B, 0, mp4(1000)) == taken(1)
    await until(lambda: len(stand_in.started()) == 2)
    first, second = stand_in.started()
    # The first one read its part and nothing of the next; it ended because its input was closed.
    await until(lambda: len(stand_in.ended()) == 1)
    assert stand_in.ended()[0] == {
        "pid": first["pid"],
        "event": "end",
        "bytes": 2000,
        "why": "end of input",
    }
    await until(lambda: stand_in.bytes_read(second["pid"]) == 1000)
    await until(lambda: "seg-000002.ts" in playlist.listed(settings.hls_root / ONE))
    # The second continues the playlist of the first, and is told which container it reads.
    first_argv, second_argv = first["argv"], second["argv"]
    assert "append_list" not in first_argv[first_argv.index("-hls_flags") + 1]
    assert "append_list" in second_argv[second_argv.index("-hls_flags") + 1]
    assert first_argv[first_argv.index("-i") - 1] == "matroska"
    assert second_argv[second_argv.index("-i") - 1] == "mov"
    written = (settings.hls_root / ONE / "index.m3u8").read_text()
    assert written.count("#EXT-X-DISCONTINUITY") == 1
    assert [line for line in written.splitlines() if line.endswith(".ts")] == [
        "seg-000000.ts",
        "seg-000001.ts",
        "seg-000002.ts",
    ]


async def test_a_part_seen_before_is_restart(box: Box) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm())
    box.chunk(ONE, B, 0, webm())
    assert box.chunk(ONE, A, 0, webm()) == RESTART
    assert box.chunk(ONE, A, 1, b"x" * 300) == RESTART
    assert box.chunk(ONE, B, 1, b"x" * 300) == taken(2)


async def test_a_part_that_does_not_begin_at_zero_is_restart(box: Box, stand_in: StandIn) -> None:
    await started(box)
    assert box.chunk(ONE, A, 1, webm()) == RESTART
    assert box.chunk(ONE, A, 0, webm()) == taken(1)
    assert box.chunk(ONE, B, 3, webm()) == RESTART
    assert held(box)[0]["part"] == A


async def test_a_part_that_is_no_webm_and_no_mp4_starts_no_encoder(
    box: Box, stand_in: StandIn, events: Events
) -> None:
    await started(box)
    for body in (
        b"\x00" * 400,
        b"#EXTM3U\n" + b"x" * 400,
        b"\x89PNG\r\n\x1a\n" + b"x" * 400,
        b"abc",
    ):
        assert box.chunk(ONE, A, 0, body) == RESTART
    assert held(box)[0] == {
        "id": ONE,
        "part": None,
        "next_seq": 0,
        "playable": False,
        "queued_bytes": 0,
        "encoder": "idle",
    }
    assert stand_in.started() == []
    assert len(events.named("part_refused")) == 4
    # Such a refusal does not use the part id up, and does not end the part at hand.
    assert box.chunk(ONE, A, 0, webm()) == taken(1)
    assert box.chunk(ONE, B, 0, b"\x00" * 400) == RESTART
    assert box.chunk(ONE, A, 1, b"x" * 300) == taken(2)


async def test_the_ids_of_the_latest_64_parts_are_remembered(box: Box) -> None:
    await started(box)
    for number in range(66):
        assert box.chunk(ONE, f"part-{number:04d}", 0, webm())["ok"] is True
    assert box.chunk(ONE, "part-0002", 0, webm()) == RESTART
    assert box.chunk(ONE, "part-0000", 0, webm())["ok"] is True


async def test_busy_when_the_queue_is_full_and_nothing_of_that_request_is_taken(
    box_with: Callable[..., Box], stand_in: StandIn
) -> None:
    box = box_with(chunk_max_bytes=70_000)
    await box.start(ONE, [])
    # The stand-in stops reading: what is sent then stays in the pipe and in the queue.
    assert box.chunk(ONE, A, 0, webm(1000, b"STOPREADING ")) == taken(1)
    seq = 1
    while (answer := box.chunk(ONE, A, seq, b"z" * 70_000)) != BUSY:
        assert answer == taken(seq + 1)
        seq += 1
        assert seq < 40
        if seq % 2 == 0:
            await until(lambda: True)
    live = held(box)[0]
    assert live["next_seq"] == seq
    assert 4 * 70_000 - 70_000 < live["queued_bytes"] <= 4 * 70_000
    assert box.chunk(ONE, A, seq, b"z" * 70_000) == BUSY
    assert held(box)[0] == live
    # A repeat is still acknowledged while the queue is full.
    assert box.chunk(ONE, A, seq - 1, b"z" * 70_000) == taken(seq)
    await box.shut_down()
    assert stand_in.alive() == []


async def test_playable_turns_true_with_the_third_listed_segment(
    box: Box, settings: Settings
) -> None:
    await started(box)
    directory = settings.hls_root / ONE
    assert box.chunk(ONE, A, 0, webm(1000)) == taken(1)
    await until(lambda: len(playlist.listed(directory)) == 1)
    assert box.chunk(ONE, A, 1, b"x" * 1000) == taken(2)
    await until(lambda: len(playlist.listed(directory)) == 2)
    assert held(box)[0]["playable"] is False
    assert box.chunk(ONE, A, 2, b"x" * 1000) == taken(3)
    await until(lambda: len(playlist.listed(directory)) == 3)
    assert held(box)[0]["playable"] is True
    assert box.chunk(ONE, A, 3, b"x" * 10) == taken(4, playable=True)
    # Once playable, a broadcast stays playable through the next part.
    assert box.chunk(ONE, B, 0, webm(10)) == taken(1, playable=True)


async def test_an_encoder_that_ends_by_itself_makes_its_part_restart(
    box: Box, stand_in: StandIn, events: Events
) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400))
    assert box.chunk(ONE, A, 1, b"EXIT3" + b"x" * 300) == taken(2)
    await until(lambda: held(box)[0]["encoder"] == "exited")
    assert box.chunk(ONE, A, 2, b"x" * 300) == RESTART
    assert box.chunk(ONE, A, 1, b"x" * 300) == RESTART  # a repeat of a dead part, too
    assert events.named("encoder_exited")[0]["code"] == 3
    assert events.named("encoder_exited")[0]["by_itself"] is True
    assert events.named("encoder_says")[0]["line"] == "stand-in: asked to fail"
    # The next part starts clean.
    assert box.chunk(ONE, B, 0, webm(400)) == taken(1)
    await until(lambda: len(stand_in.started()) == 2)


async def test_a_part_whose_encoder_cannot_be_started_is_dead(
    box: Box, stand_in: StandIn, events: Events
) -> None:
    (stand_in.directory / "ffmpeg").unlink()
    await started(box)
    assert box.chunk(ONE, A, 0, webm(400)) == taken(1)
    await until(lambda: held(box)[0]["encoder"] == "exited")
    assert events.named("encoder_failed") == [
        {"event": "encoder_failed", "error": "FileNotFoundError", "id": ONE, "part": A}
    ]
    assert box.chunk(ONE, A, 1, b"x" * 300) == RESTART


async def test_an_encoder_that_does_not_end_is_killed_when_the_next_part_begins(
    box: Box, stand_in: StandIn, events: Events, grace: float
) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400, b"HANG"))
    await until(lambda: any(run["event"] == "hang" for run in stand_in.runs()))
    assert box.chunk(ONE, B, 0, webm(400)) == taken(1)
    await until(lambda: len(stand_in.started()) == 2)
    exited = events.named("encoder_exited")
    assert exited[0]["code"] == -9
    assert exited[0]["by_itself"] is False
    assert len(stand_in.alive()) == 1


async def test_an_encoder_that_takes_pieces_and_writes_nothing_is_killed_and_its_part_is_dead(
    box: Box, stand_in: StandIn, events: Events, clock: Clock
) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400, b"STOPREADING "))
    await until(lambda: any(run["event"] == "stopped reading" for run in stand_in.runs()))
    seq = 1
    # A piece a second: after fourteen of them it has been fed for fourteen seconds.
    for _ in range(14):
        clock.now += 1
        assert box.chunk(ONE, A, seq, b"x" * 100) == taken(seq + 1)
        seq += 1
        await box.check_timers()
    assert held(box)[0]["encoder"] == "running"
    clock.now += 1
    assert box.chunk(ONE, A, seq, b"x" * 100) == taken(seq + 1)
    await box.check_timers()
    assert held(box)[0]["encoder"] == "exited"
    assert stand_in.alive() == []
    assert events.named("encoder_silent") == [
        {"event": "encoder_silent", "id": ONE, "part": A, "seconds": ENCODER_SILENT_SECONDS}
    ]
    assert box.chunk(ONE, A, seq + 1, b"x" * 100) == RESTART
    assert box.chunk(ONE, B, 0, webm(1000)) == taken(1)
    await until(lambda: len(stand_in.started()) == 2)


async def test_a_pause_between_two_pieces_counts_for_five_seconds_and_no_more(
    box: Box, stand_in: StandIn, events: Events, clock: Clock
) -> None:
    await started(box)
    box.chunk(ONE, A, 0, webm(400, b"STOPREADING "))
    await until(lambda: any(run["event"] == "stopped reading" for run in stand_in.runs()))
    # The network is gone for 14 s twice: 10 s count, and the encoder is left alone.
    for seq in (1, 2):
        clock.now += 14
        assert box.chunk(ONE, A, seq, b"x" * 100) == taken(seq + 1)
        await box.check_timers()
    assert held(box)[0]["encoder"] == "running"
    # A third pause makes it fifteen seconds of being fed without a segment.
    clock.now += ENCODER_PAUSE_SECONDS
    assert box.chunk(ONE, A, 3, b"x" * 100) == taken(4)
    await box.check_timers()
    assert held(box)[0]["encoder"] == "exited"
    assert len(events.named("encoder_silent")) == 1


async def test_an_encoder_that_writes_segments_is_left_alone(
    box: Box, stand_in: StandIn, events: Events, clock: Clock, settings: Settings
) -> None:
    await started(box)
    directory = settings.hls_root / ONE
    box.chunk(ONE, A, 0, webm(1000))

    def listed(name: str) -> bool:
        return name in playlist.listed(directory)

    for seq in range(1, 40):
        clock.now += 1
        box.chunk(ONE, A, seq, b"x" * 1000)
        await until(partial(listed, f"seg-{seq:06d}.ts"))
        await box.check_timers()
    assert held(box)[0]["encoder"] == "running"
    assert events.named("encoder_silent") == []


async def test_an_encoder_that_is_given_nothing_is_not_taken_for_stuck(
    box: Box, stand_in: StandIn, events: Events, clock: Clock, settings: Settings
) -> None:
    await started(box)
    directory = settings.hls_root / ONE
    # Half a segment's worth: the stand-in writes nothing for it, as ffmpeg would not.
    box.chunk(ONE, A, 0, webm(500))
    await until(lambda: stand_in.started() != [])
    await until(lambda: stand_in.bytes_read(stand_in.started()[0]["pid"]) == 500)
    await box.check_timers()
    # The broadcaster is silent for a while: that is the stall rule's business, not this one's.
    clock.now += ENCODER_SILENT_SECONDS * 3
    await box.check_timers()
    assert held(box)[0]["encoder"] == "running"
    assert events.named("encoder_silent") == []
    assert not (directory / "index.m3u8").exists()


async def test_files_the_playlist_does_not_list_are_swept_when_a_part_begins(
    box: Box, stand_in: StandIn, settings: Settings, events: Events
) -> None:
    await started(box)
    directory = settings.hls_root / ONE
    box.chunk(ONE, A, 0, webm(2000))
    await until(lambda: len(playlist.listed(directory)) == 2)
    # What a killed ffmpeg and its delete threshold leave behind.
    (directory / "seg-000900.ts").write_bytes(b"left over")
    (directory / "seg-000002.ts.tmp").write_bytes(b"half")
    box.chunk(ONE, B, 0, webm(1000))
    await until(lambda: len(stand_in.started()) == 2)
    await until(lambda: len(playlist.listed(directory)) == 3)
    await until(lambda: not list(directory.glob("*.tmp")))
    assert sorted(entry.name for entry in directory.iterdir()) == [
        "index.m3u8",
        "seg-000000.ts",
        "seg-000001.ts",
        "seg-000002.ts",
    ]
    assert events.named("swept") == [{"event": "swept", "id": ONE, "files": 2}]


async def test_the_encoder_gets_the_search_path_and_nothing_else(
    box: Box, stand_in: StandIn, monkeypatch: object, workspace: Path
) -> None:
    import os

    os.environ["SECRET_OF_THE_TEST"] = "do not pass on"
    try:
        probe = stand_in.directory / "ffmpeg"
        probe.write_text('#!/bin/sh\nenv > "$(dirname "$0")/environment"\ncat > /dev/null\n')
        await started(box)
        box.chunk(ONE, A, 0, webm())
        await until(lambda: (stand_in.directory / "environment").exists())
        await until(lambda: (stand_in.directory / "environment").read_text().strip() != "")
        names = {
            line.split("=")[0]
            for line in (stand_in.directory / "environment").read_text().splitlines()
        }
        assert "SECRET_OF_THE_TEST" not in names
        assert names <= {"PATH", "PWD", "SHLVL", "_", "OLDPWD", "__CF_USER_TEXT_ENCODING"}
    finally:
        del os.environ["SECRET_OF_THE_TEST"]
