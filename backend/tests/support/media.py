"""The media box in a test: a server on a Unix socket that speaks the box's lines and
remembers what it was sent (SPEC §9).

The real box is another image with ffmpeg in it (`backend/mediabox`). `FakeMediaBox`
stands where it stands for the API: one request per connection, one line of JSON and,
for a piece, exactly `size` bytes after it; one line of JSON back, then the connection
closes. It keeps the rules of the box that the API's answers depend on:

* a broadcast has a place from `start` to `stop`, and at most `capacity` have one;
* a piece is taken when its `seq` is the one its part waits for, acknowledged when it
  was taken before, and `restart` otherwise; a part begins at `seq` 0 with an id the
  broadcast has not seen and with the first bytes of a WebM or MP4 file;
* a broadcast is playable from its `playable_after`-th piece on;
* a broadcast it does not hold is `unknown`.

and it can be told to misbehave: `start_full`, `chunk_error`, `raw_answer`, `hang`,
`restart`, `gone`. `before_answer` lets a test do something while a request is on its
way.

    async with FakeMediaBox() as box:
        client = MediaClient(behind(settings, box).broadcast)
        await client.start(broadcast_id)
        assert box.asked("start") == [{"op": "start", "id": str(broadcast_id)}]

`BoxAsBuilt` is the other thing a test can put behind the socket: the box of
`backend/mediabox` itself, in this process, in front of the stand-in ffmpeg of its own
tests. It holds the API to the box as it is, without Docker and without a codec:

    async with BoxAsBuilt(directory) as built:      # `directory` from `box_workspace`
        client = MediaClient(behind(settings, built).broadcast)

A Unix socket's path has at most 104 bytes on macOS and 108 on Linux, and pytest's
temporary directories are longer than that: the socket lies in a directory of its own
under `/tmp`, which is removed when the box closes.
"""

import asyncio
import contextlib
import json
import os
import shutil
import socket
import stat
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Protocol, Self, cast
from uuid import UUID

import pytest

from app.core.config import Settings
from mediabox import broadcast as box_broadcast
from mediabox.box import Box
from mediabox.server import serve
from mediabox.settings import Settings as BoxSettings
from tests.unit.mediabox.environment import box_environment

SOCKET_ROOT: Final[str] = "/tmp"
"""Where the socket directories of the tests lie: a path short enough for a socket."""

SOCKET_NAME: Final[str] = "box.sock"

REQUEST_LINE_BYTES: Final[int] = 512
"""The longest request line the box takes, without its line feed."""

PLAYABLE_AFTER: Final[int] = 3
"""The pieces after which a broadcast of the fake box is playable, unless a test says
otherwise. The real box says so with the third segment its playlist lists."""

EBML: Final[bytes] = b"\x1a\x45\xdf\xa3"
"""The first bytes of a WebM file."""

_MP4_BOXES: Final[frozenset[bytes]] = frozenset({b"ftyp", b"styp", b"moof"})

_BAD_REQUEST: Final[dict[str, Any]] = {"ok": False, "error": "bad_request"}

STAND_IN_FFMPEG: Final[Path] = (
    Path(__file__).resolve().parent.parent / "unit" / "mediabox" / "standin_ffmpeg.py"
)
"""The stand-in for ffmpeg of the box's own tests: it reads what it is fed and writes a
playlist and a segment file per `STAND_IN_SEGMENT_BYTES` bytes."""

STAND_IN_SEGMENT_BYTES: Final[int] = 1000

SHUT_DOWN_SECONDS: Final[float] = 20.0
"""What a box of a test gets to end what it started, before the test fails on it."""


def webm(size: int = 64, fill: bytes = b"w") -> bytes:
    """`size` bytes that begin as a WebM file does: what the first piece of a part
    looks like to the box. The rest is `fill`, over and over."""
    return (EBML + fill * size)[:size]


def later(size: int = 64, fill: bytes = b"c") -> bytes:
    """`size` bytes from the middle of a recording: a piece that continues its part."""
    return (fill * size)[:size]


class Listening(Protocol):
    """Anything that stands behind a socket where the media box stands."""

    @property
    def path(self) -> str: ...


def behind(settings: Settings, box: Listening, **broadcast: object) -> Settings:
    """`settings` whose media box is `box`. `broadcast` names other values of the
    section "Live video" a test runs with: `max_viewers=2`, `max_live=2`."""
    section = settings.broadcast.model_copy(update={"media_socket": box.path, **broadcast})
    return settings.model_copy(update={"broadcast": section})


@dataclass(frozen=True, slots=True)
class Piece:
    """One piece the box took."""

    broadcast_id: UUID
    part: str
    seq: int
    data: bytes


@dataclass(slots=True)
class _Held:
    """What the box knows of a broadcast that has a place."""

    part: str | None = None
    next_seq: int = 0
    parts_seen: set[str] = field(default_factory=set)
    taken: int = 0


class FakeMediaBox:
    def __init__(self, *, capacity: int = 1, playable_after: int = PLAYABLE_AFTER) -> None:
        self._directory = Path(tempfile.mkdtemp(prefix="marketh-box-", dir=SOCKET_ROOT))
        self._path = str(self._directory / SOCKET_NAME)
        self.capacity = capacity
        self.playable_after = playable_after

        self.lines: list[bytes] = []
        """Every request line that arrived, as it arrived, without its line feed."""

        self.requests: list[dict[str, Any]] = []
        """The same lines, read as JSON, in the order they arrived."""

        self.pieces: list[Piece] = []
        """Every piece the box took, once: a piece it only acknowledged is not here."""

        self.start_full = False
        """Every `start` of a broadcast the box does not hold is answered `full`."""

        self.chunk_error: str | None = None
        """Every piece of a broadcast the box holds is answered with this error:
        `busy`, `restart`, `unknown`."""

        self.raw_answer: bytes | None = None
        """Every request is answered with exactly these bytes, and nothing is done."""

        self.before_answer: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        """Called with every request the box read, before the box does what it asks: a
        test puts here what happens elsewhere while the request is on its way."""

        self._held: dict[str, _Held] = {}
        self._hanging = False
        self._released = asyncio.Event()
        self._server: asyncio.Server | None = None

    @property
    def path(self) -> str:
        """Where the box listens: the `BROADCAST_MEDIA_SOCKET` of its client."""
        return self._path

    async def __aenter__(self) -> Self:
        await self.listen()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.gone()
        shutil.rmtree(self._directory, ignore_errors=True)

    async def listen(self) -> None:
        """Listen at `path`: the box is there, and holds nothing."""
        self._held.clear()
        self._released.clear()
        self._server = await asyncio.start_unix_server(self._connection, path=self.path)

    async def gone(self) -> None:
        """The box is not there: nobody listens and the socket file is removed. What it
        held is lost, as with the container."""
        server, self._server = self._server, None
        self._released.set()
        if server is not None:
            server.close()
            await server.wait_closed()
        self._remove_socket_file()
        self._held.clear()

    def leave_socket_file_behind(self) -> None:
        """Put a socket file at `path` that nobody listens behind: what a box process
        that ended leaves until the next one starts."""
        with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as ended:
            ended.bind(self.path)

    @property
    def has_socket_file(self) -> bool:
        return Path(self.path).exists()

    def _remove_socket_file(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            Path(self.path).unlink()

    def restart(self) -> None:
        """The box started again between two requests: it listens and holds nothing."""
        self._held.clear()

    def hang(self) -> None:
        """From now on the box reads every request and answers none."""
        self._hanging = True

    @property
    def held(self) -> list[UUID]:
        """The broadcasts that have a place, in the order they took it."""
        return [UUID(broadcast_id) for broadcast_id in self._held]

    def asked(self, op: str) -> list[dict[str, Any]]:
        """The requests of one kind, in the order they arrived."""
        return [request for request in self.requests if request.get("op") == op]

    def bytes_of(self, broadcast_id: UUID) -> bytes:
        """Everything the box took of a broadcast, in the order it took it."""
        return b"".join(piece.data for piece in self.pieces if piece.broadcast_id == broadcast_id)

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readuntil(b"\n"))[:-1]
            request, body = None, b""
            if len(line) <= REQUEST_LINE_BYTES:
                with contextlib.suppress(ValueError):
                    request = json.loads(line)
            if isinstance(request, dict) and request.get("op") == "chunk":
                body = await reader.readexactly(int(request.get("size", 0)))
            self.lines.append(line)
            if isinstance(request, dict):
                self.requests.append(request)
            if self._hanging:
                await self._released.wait()
                return
            if self.before_answer is not None and isinstance(request, dict):
                await self.before_answer(request)
            if self.raw_answer is not None:
                writer.write(self.raw_answer)
            else:
                answer = self._answer(request, body) if isinstance(request, dict) else _BAD_REQUEST
                writer.write(json.dumps(answer, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
        except asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError:
            # The other end went away, or sent something that is no request.
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    def _answer(self, request: dict[str, Any], body: bytes) -> dict[str, Any]:
        op, broadcast_id = request.get("op"), request.get("id")
        if op == "status":
            return {
                "ok": True,
                "capacity": self.capacity,
                "live": [
                    {
                        "id": held_id,
                        "part": held.part,
                        "next_seq": held.next_seq,
                        "playable": held.taken >= self.playable_after,
                        "queued_bytes": 0,
                        "encoder": "running" if held.part else "idle",
                    }
                    for held_id, held in self._held.items()
                ],
            }
        if not isinstance(broadcast_id, str) or str(_uuid_or_none(broadcast_id)) != broadcast_id:
            return _BAD_REQUEST
        if op == "start":
            for other in request.get("stop", []):
                if other != broadcast_id:
                    self._held.pop(other, None)
            if broadcast_id in self._held:
                return {"ok": True}
            if self.start_full or len(self._held) >= self.capacity:
                return {"ok": False, "error": "full"}
            self._held[broadcast_id] = _Held()
            return {"ok": True}
        if op == "stop":
            self._held.pop(broadcast_id, None)
            return {"ok": True}
        if op == "chunk":
            return self._take(broadcast_id, request, body)
        return _BAD_REQUEST

    def _take(self, broadcast_id: str, request: dict[str, Any], body: bytes) -> dict[str, Any]:
        part, seq = request.get("part"), request.get("seq")
        if not isinstance(part, str) or not isinstance(seq, int) or not body:
            return _BAD_REQUEST
        held = self._held.get(broadcast_id)
        if held is None:
            return {"ok": False, "error": "unknown"}
        if self.chunk_error is not None:
            return {"ok": False, "error": self.chunk_error}
        if part == held.part:
            if seq > held.next_seq:
                return {"ok": False, "error": "restart"}
            repeat = seq < held.next_seq
        else:
            if seq != 0 or part in held.parts_seen or not _begins_a_recording(body):
                return {"ok": False, "error": "restart"}
            held.part, held.next_seq, repeat = part, 0, False
            held.parts_seen.add(part)
        if not repeat:
            self.pieces.append(Piece(UUID(broadcast_id), part, seq, body))
            held.next_seq += 1
            held.taken += 1
        return {
            "ok": True,
            "next_seq": held.next_seq,
            "playable": held.taken >= self.playable_after,
        }


def _uuid_or_none(text: str) -> UUID | None:
    try:
        return UUID(text)
    except ValueError:
        return None


def _begins_a_recording(data: bytes) -> bool:
    """Whether `data` begins as a WebM or an MP4 file does: what the box looks at before
    it starts an encoder for a part."""
    return data.startswith(EBML) or data[4:8] in _MP4_BOXES


@contextlib.contextmanager
def box_workspace(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A directory under `/tmp` for the socket and the HLS files of a `BoxAsBuilt`, with
    the stand-in first on the PATH under the name `ffmpeg`. The directory is removed
    when the block ends.

    An encoder that is asked to end gets a fifth of a second in a test, not two.
    """
    directory = Path(tempfile.mkdtemp(prefix="marketh-box-", dir=SOCKET_ROOT))
    programs = directory / "bin"
    programs.mkdir()
    program = programs / "ffmpeg"
    shutil.copy(STAND_IN_FFMPEG, program)
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{programs}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(box_broadcast, "ENCODER_GRACE_SECONDS", 0.2)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def box_settings_in(directory: Path, **changes: Any) -> BoxSettings:
    """What Compose hands the box from `.example.env`, with its socket and its HLS
    volume in `directory`. `changes` names what a test sets differently."""
    return replace(
        BoxSettings.from_environment(box_environment()),
        socket_path=directory / SOCKET_NAME,
        hls_root=directory / "hls",
        **changes,
    )


def _log_nothing(event: str, /, **fields: object) -> None:
    """The log of a box nobody reads."""


class BoxAsBuilt:
    """The media box of `backend/mediabox` behind its socket, as the API reaches it.
    Every peer counts as the API."""

    def __init__(self, directory: Path, **changes: Any) -> None:
        self.settings = box_settings_in(directory, **changes)
        self.box = Box(self.settings, log=_log_nothing)
        self._server: asyncio.Server | None = None

    @property
    def path(self) -> str:
        return str(self.settings.socket_path)

    async def __aenter__(self) -> Self:
        self._server = await serve(self.box, self.settings.socket_path, trusted=_everybody)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._close()

    async def start_again(self) -> None:
        """The container starts again: another box process behind the same socket,
        which holds nothing of what the one before held."""
        await self._close()
        self._remove_socket_file()
        self.box = Box(self.settings, log=_log_nothing)
        self._server = await serve(self.box, self.settings.socket_path, trusted=_everybody)

    def held(self) -> list[UUID]:
        """The broadcasts the box holds."""
        live = cast(list[dict[str, Any]], self.box.status()["live"])
        return [UUID(held["id"]) for held in live]

    def files_of(self, broadcast_id: UUID) -> list[str]:
        """The names of what the box wrote for a broadcast, sorted; none when its
        directory is not there."""
        directory = self.settings.hls_root / str(broadcast_id)
        return sorted(entry.name for entry in directory.iterdir()) if directory.is_dir() else []

    async def _close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()
        await asyncio.wait_for(self.box.shut_down(), SHUT_DOWN_SECONDS)

    def _remove_socket_file(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.settings.socket_path.unlink()


def _everybody(peer: socket.socket) -> bool:
    return True


async def until(condition: Callable[[], Awaitable[bool]], seconds: float = 15.0) -> None:
    """Wait until a condition holds; fail the test when it never does. For what an
    encoder does a moment after the box answered: it runs in a process of its own."""
    async with asyncio.timeout(seconds):
        while not await condition():  # noqa: ASYNC110 - it happens in another process
            await asyncio.sleep(0.01)
