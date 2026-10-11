"""Starting and ending the process: `python -m mediabox`."""

import asyncio
import os
import signal
import socket
from pathlib import Path

import pytest

from mediabox import __main__ as main_module
from mediabox.box import Box
from mediabox.server import serve
from mediabox.settings import Settings
from tests.unit.mediabox.conftest import ONE, SOCKET, Events, StandIn, ask, request, until, webm


def socket_file_is_there() -> bool:
    return Path(SOCKET).exists()


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_module, "log", lambda event, **fields: logged.append({"event": event, **fields})
    )
    return logged


async def test_the_hls_directory_is_wiped_and_a_socket_left_behind_is_no_obstacle(
    settings: Settings, stand_in: StandIn, quiet: list[dict[str, object]], grace: float
) -> None:
    stale = settings.hls_root / ONE
    stale.mkdir(parents=True)
    (stale / "index.m3u8").write_text("#EXTM3U\n")
    (stale / "seg-000001.ts").write_bytes(b"left over")
    (settings.hls_root / "stray").write_text("x")
    (settings.hls_root / "link").symlink_to(stale)
    left = socket.socket(socket.AF_UNIX)
    left.bind(SOCKET)
    left.close()
    assert socket_file_is_there()

    running = asyncio.create_task(main_module.run(settings))
    await until(lambda: any(entry["event"] == "listening" for entry in quiet))
    assert list(settings.hls_root.iterdir()) == []
    assert await ask(request(op="status")) == {"ok": True, "capacity": 1, "live": []}

    # SIGTERM ends it, and it takes its socket file with it.
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(running, 5)
    assert not socket_file_is_there()
    assert quiet[-1] == {"event": "exit"}
    # The loop outlives the run under the tests: the two signals are its own again.
    loop = asyncio.get_running_loop()
    assert [loop.remove_signal_handler(number) for number in main_module.STOP_SIGNALS] == [
        False,
        False,
    ]


async def test_the_process_ends_after_the_stop_of_its_last_broadcast_and_answers_it(
    settings: Settings,
    stand_in: StandIn,
    quiet: list[dict[str, object]],
    grace: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Under the tests the box and its peer are one user: every peer counts as the API.
    monkeypatch.setattr(
        "mediabox.__main__.serve", lambda box, path: serve(box, path, trusted=lambda peer: True)
    )
    running = asyncio.create_task(main_module.run(settings))
    await until(lambda: any(entry["event"] == "listening" for entry in quiet))
    assert await ask(request(op="start", id=ONE)) == {"ok": True}
    part = "part-aaaa"
    body = webm(1000)
    assert (await ask(request(op="chunk", id=ONE, part=part, seq=0, size=len(body)) + body))[
        "ok"
    ] is True
    await until(lambda: stand_in.started() != [])
    assert await ask(request(op="stop", id=ONE)) == {"ok": True}
    await asyncio.wait_for(running, 5)
    assert stand_in.alive() == []
    assert not (settings.hls_root / ONE).exists()
    assert not socket_file_is_there()


async def test_a_box_that_is_ending_answers_nothing(
    settings: Settings, stand_in: StandIn, events: Events, grace: float
) -> None:
    box = Box(settings, log=events)
    server = await serve(box, SOCKET, trusted=lambda peer: True)
    try:
        await ask(request(op="start", id=ONE))
        box.ending = True
        assert await ask(request(op="status")) is None
        assert await ask(request(op="start", id=ONE)) is None
    finally:
        box.ending = False
        await box.shut_down()
        server.close()
        await server.wait_closed()


def test_a_broken_setting_ends_the_process_with_its_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(os.environ):
        if name.startswith(("BROADCAST_", "MEDIA_")):
            monkeypatch.delenv(name)
    assert main_module.main() == 2
    assert capsys.readouterr().err.startswith("BROADCAST_MEDIA_SOCKET: is not set")


def test_only_process_one_clears_the_container_out() -> None:
    # `kill(-1)` reaches every process of the user anywhere but in process 1 of a
    # container. The line that hands it to the box says so.
    source = Path(main_module.__file__).read_text()
    assert "clear_out=kill_everything_else if os.getpid() == 1 else None" in source
    assert source.count("kill_everything_else") == 2
