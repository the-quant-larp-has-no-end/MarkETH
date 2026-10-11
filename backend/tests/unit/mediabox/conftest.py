"""What the unit tests of the media box share: a place to work, a stand-in ffmpeg, a box.

A Unix socket's path may have 104 bytes on macOS and 108 on Linux; pytest's temporary
directories are longer than that. Every test therefore works inside its temporary
directory (`workspace` changes into it) and names its socket relative to it.

In this directory `settings` is the box's `Settings` and `clock` the box's clock: the box
shares nothing with the API, whose fixtures of those names (tests/conftest.py) are not
what a test of the box asks for.
"""

import asyncio
import json
import os
import shutil
import socket
import stat
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import pytest

from mediabox import broadcast as broadcast_module
from mediabox.box import Box
from mediabox.server import serve
from mediabox.settings import Settings
from tests.unit.mediabox.environment import box_environment

STAND_IN = Path(__file__).parent / "standin_ffmpeg.py"
SOCKET = "box.sock"

ONE = "11111111-1111-4111-8111-111111111111"
TWO = "22222222-2222-4222-8222-222222222222"
THREE = "33333333-3333-4333-8333-333333333333"


def webm(size: int = 400, fill: bytes = b"w") -> bytes:
    """Bytes that begin as a WebM file does."""
    return b"\x1a\x45\xdf\xa3" + fill * (size - 4)


def mp4(size: int = 400) -> bytes:
    """Bytes that begin as an MP4 file does."""
    return (24).to_bytes(4) + b"ftyp" + b"m" * (size - 8)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> Settings:
    """The values of `.example.env`, with socket and HLS volume in the test's own directory."""
    return replace(
        Settings.from_environment(box_environment()),
        socket_path=Path(SOCKET),
        hls_root=workspace / "hls",
    )


@dataclass
class StandIn:
    """The stand-in ffmpeg of one test, and what its runs left behind."""

    directory: Path

    def runs(self) -> list[dict[str, Any]]:
        try:
            lines = (self.directory / "runs.jsonl").read_text().splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines]

    def started(self) -> list[dict[str, Any]]:
        return [run for run in self.runs() if run["event"] == "start"]

    def ended(self) -> list[dict[str, Any]]:
        return [run for run in self.runs() if run["event"] == "end"]

    def bytes_read(self, pid: object) -> int:
        try:
            return int((self.directory / f"bytes-{pid}").read_text() or 0)
        except FileNotFoundError, ValueError:
            return 0

    def alive(self) -> list[int]:
        """The stand-ins of this test that still run."""
        alive = []
        for run in self.started():
            pid = int(str(run["pid"]))
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.append(pid)
        return alive


@pytest.fixture
def stand_in(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    directory = workspace / "bin"
    directory.mkdir()
    program = directory / "ffmpeg"
    shutil.copy(STAND_IN, program)
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return StandIn(directory)


@dataclass
class Clock:
    now: float = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Events:
    """What the box logged."""

    seen: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, event: str, /, **fields: object) -> None:
        self.seen.append({"event": event, **fields})

    def named(self, event: str) -> list[dict[str, object]]:
        return [entry for entry in self.seen if entry["event"] == event]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def events() -> Events:
    return Events()


@pytest.fixture
def grace(monkeypatch: pytest.MonkeyPatch) -> float:
    """An ffmpeg that is asked to end gets a fifth of a second in the tests, not two."""
    monkeypatch.setattr(broadcast_module, "ENCODER_GRACE_SECONDS", 0.2)
    return 0.2


@pytest.fixture
async def box(
    settings: Settings, stand_in: StandIn, clock: Clock, events: Events, grace: float
) -> AsyncIterator[Box]:
    made = Box(settings, log=events, clock=clock)
    yield made
    # With a limit: a box that cannot end what it started fails the test instead of holding it.
    await asyncio.wait_for(made.shut_down(), 20)
    assert stand_in.alive() == []


@pytest.fixture
def box_with(
    settings: Settings, stand_in: StandIn, clock: Clock, events: Events, grace: float
) -> Callable[..., Box]:
    """A box with other settings; the test shuts it down itself."""

    def make(**changes: Any) -> Box:
        return Box(replace(settings, **changes), log=events, clock=clock)

    return make


def held(box: Box) -> list[dict[str, Any]]:
    """What the box holds, as its status says it."""
    return cast(list[dict[str, Any]], box.status()["live"])


async def until(condition: Callable[[], object], seconds: float = 15.0) -> None:
    """Waits until a condition holds; fails the test when it never does."""
    async with asyncio.timeout(seconds):
        while not condition():  # noqa: ASYNC110 - what is waited for happens in another process
            await asyncio.sleep(0.01)


async def ask(payload: bytes, *, hang_up: bool = False, path: str = SOCKET) -> Any:
    """One request over the socket, byte for byte, and the answer line as JSON."""
    reader, writer = await asyncio.open_unix_connection(path)
    try:
        writer.write(payload)
        await writer.drain()
        if hang_up:
            writer.write_eof()
        line = await asyncio.wait_for(reader.readline(), 8)
    finally:
        writer.close()
    return json.loads(line) if line else None


def request(**fields: object) -> bytes:
    return json.dumps(fields, separators=(",", ":")).encode() + b"\n"


@pytest.fixture
async def listening(box: Box) -> AsyncIterator[Box]:
    """The box behind its socket, as the API reaches it. Every peer counts as the API."""
    server = await serve(box, SOCKET, trusted=lambda peer: True)
    yield box
    server.close()
    await server.wait_closed()


def peer_user(connection: socket.socket) -> int:
    return os.getuid()
