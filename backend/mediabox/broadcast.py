"""One broadcast in the box: its parts, the queue in front of ffmpeg, and ffmpeg itself.

The bytes of one recorder run are one part. A part has one ffmpeg
process, which reads the part from its standard input. A request is taken whole into the
queue or not at all and answered at once; writing to ffmpeg happens behind the answer.
"""

import asyncio
import contextlib
import os
import shutil
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Final, Literal, Protocol

from mediabox import ffmpeg, playlist
from mediabox.settings import Settings
from mediabox.sniff import Container, container_of

# An ffmpeg that is asked to end has this long, then it is killed.
ENCODER_GRACE_SECONDS: Final = 2.0
# A playlist can be played from the moment it lists this many segments.
PLAYABLE_SEGMENTS: Final = 3
# The part ids a broadcast remembers, so that none is used twice.
PARTS_REMEMBERED: Final = 64
# An ffmpeg that has been fed for this long and written no segment is stuck: bytes that are
# no video can make its MP4 reader skip everything that follows, without a word and
# without an end. It is killed, and its part is dead like that of one that ended by itself.
ENCODER_SILENT_SECONDS: Final = 15.0
# The time between two pieces counts towards that for at most this much: an ffmpeg that is
# given nothing is not stuck, and a broadcaster who is silent is the stall rule's business.
ENCODER_PAUSE_SECONDS: Final = 5.0
# What is logged of one ffmpeg's own messages. Its loglevel is `error`: there are few.
ENCODER_LINES_LOGGED: Final = 20
ENCODER_LINE_LENGTH: Final = 300

type EncoderState = Literal["idle", "running", "exited"]
type Answer = dict[str, object]
type Spawn = Callable[[Sequence[str]], Awaitable[asyncio.subprocess.Process]]
type Clock = Callable[[], float]

RESTART: Final[Answer] = {"ok": False, "error": "restart"}
BUSY: Final[Answer] = {"ok": False, "error": "busy"}


class Log(Protocol):
    def __call__(self, event: str, /, **fields: object) -> None: ...


async def spawn_ffmpeg(argv: Sequence[str]) -> asyncio.subprocess.Process:
    """Starts the encoder with the search path and nothing else in its environment."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
    )


class Encoder:
    """One ffmpeg process and the bytes on their way into it."""

    def __init__(self, *, spawn: Spawn, log: Log, broadcast_id: str, part: str) -> None:
        self._spawn = spawn
        self._log = log
        self._names = {"id": broadcast_id, "part": part}
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._process: asyncio.subprocess.Process | None = None
        self._writer: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._asked_to_end = False
        self._given_up = False
        self._gone = asyncio.Event()
        self.queued_bytes = 0
        # True once it is gone without having been asked to go: its part is dead.
        self.ended_by_itself = False

    @property
    def state(self) -> EncoderState:
        if self._gone.is_set():
            return "exited"
        return "idle" if self._process is None else "running"

    def start(self, *, after: Encoder | None, prepare: Callable[[], Sequence[str]]) -> None:
        """Begins once `after` is gone. `prepare` clears the way and says the command."""
        self._task = asyncio.create_task(self._run(after, prepare))

    def feed(self, data: bytes) -> None:
        self.queued_bytes += len(data)
        self._chunks.put_nowait(data)

    async def finish(self) -> None:
        """Closes its input and waits until it is gone.

        ffmpeg then writes its last segment and ends, within milliseconds. One that has
        not ended after ENCODER_GRACE_SECONDS is killed.
        """
        self._asked_to_end = True
        self._close_input()
        try:
            async with asyncio.timeout(ENCODER_GRACE_SECONDS):
                await self._gone.wait()
        except TimeoutError:
            await self.kill()

    async def kill(self) -> None:
        """Ends it at once (SIGKILL) and waits until it is gone.

        For a broadcast that is stopped: nothing of its output is wanted. SIGTERM would
        not do: an ffmpeg that waits for input on its pipe does not act on it.
        """
        self._asked_to_end = True
        self._close_input()
        process = self._process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await self._gone.wait()

    async def _run(self, after: Encoder | None, prepare: Callable[[], Sequence[str]]) -> None:
        try:
            if after is not None:
                await after.finish()
            if self._asked_to_end:
                return
            process = await self._spawn(prepare())
            self._process = process
            self._log("encoder_started", pid=process.pid, **self._names)
            self._writer = asyncio.create_task(self._write(process))
            errors = asyncio.create_task(self._read_errors(process))
            if self._asked_to_end:
                # Asked to end while it was being started.
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            code = await process.wait()
            by_itself = not self._asked_to_end
            self._writer.cancel()
            await asyncio.gather(self._writer, errors, return_exceptions=True)
            self._log("encoder_exited", code=code, by_itself=by_itself, **self._names)
        except OSError as error:
            # No ffmpeg to start, or no room for another process.
            self._log("encoder_failed", error=type(error).__name__, **self._names)
        finally:
            # However it came to its end: unless it was asked to, its part is dead.
            self.ended_by_itself = self._given_up or not self._asked_to_end
            self._gone.set()

    async def _write(self, process: asyncio.subprocess.Process) -> None:
        stdin = process.stdin
        assert stdin is not None
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            while True:
                data = await self._chunks.get()
                stdin.write(data)
                await stdin.drain()
                self.queued_bytes -= len(data)

    async def _read_errors(self, process: asyncio.subprocess.Process) -> None:
        stderr = process.stderr
        assert stderr is not None
        logged = 0
        rest = ""
        while chunk := await stderr.read(4096):
            if logged >= ENCODER_LINES_LOGGED:
                continue
            *lines, rest = (rest + chunk.decode("ascii", "replace")).replace("\r", "\n").split("\n")
            for line in lines:
                text = "".join(letter if letter.isprintable() else "?" for letter in line).strip()
                if text and logged < ENCODER_LINES_LOGGED:
                    logged += 1
                    self._log("encoder_says", line=text[:ENCODER_LINE_LENGTH], **self._names)

    async def give_up(self) -> None:
        """Kills an ffmpeg that is stuck. Its part is dead, as if it had ended by itself."""
        self._given_up = True
        await self.kill()

    def _close_input(self) -> None:
        if self._writer is not None:
            self._writer.cancel()
        process = self._process
        if process is not None and process.stdin is not None:
            process.stdin.close()


class Broadcast:
    """What the box holds of one broadcast id."""

    def __init__(
        self, broadcast_id: str, settings: Settings, *, clock: Clock, spawn: Spawn, log: Log
    ) -> None:
        self.id = broadcast_id
        self.directory = ffmpeg.directory(settings, broadcast_id)
        self.part: str | None = None
        self.next_seq = 0
        self.started_at = clock()
        self.last_taken_at = self.started_at
        self._settings = settings
        self._clock = clock
        self._spawn = spawn
        self._log = log
        self._parts_seen: deque[str] = deque(maxlen=PARTS_REMEMBERED)
        self._encoder: Encoder | None = None
        self._encoders: list[Encoder] = []
        self._playable = False
        # For how long the encoder at hand has been fed since the playlist was last written.
        self._fed_without_output = 0.0
        self._fed_at: float | None = None
        self._playlist_seen: int | None = None
        self.directory.mkdir(mode=0o755, parents=True, exist_ok=True)

    @property
    def playable(self) -> bool:
        """True from the moment the playlist lists PLAYABLE_SEGMENTS segments, and then for good."""
        if not self._playable:
            self._playable = len(playlist.listed(self.directory)) >= PLAYABLE_SEGMENTS
        return self._playable

    def take(self, part: str, seq: int, data: bytes) -> Answer:
        """One upload request: taken whole, acknowledged as a repeat, or refused."""
        encoder = self._encoder
        if encoder is not None and part == self.part:
            if encoder.ended_by_itself:
                return RESTART
            if seq < self.next_seq:
                return self._taken()
            if seq > self.next_seq:
                return RESTART
            if encoder.queued_bytes + len(data) > self._settings.queue_max_bytes:
                return BUSY
        else:
            if seq != 0 or part in self._parts_seen:
                return RESTART
            container = container_of(data)
            if container is None:
                self._log("part_refused", id=self.id, part=part, reason="not WebM or MP4")
                return RESTART
            encoder = self._begin(part, container)
        encoder.feed(data)
        self.next_seq = seq + 1
        self.last_taken_at = self._clock()
        if self._fed_at is not None:
            pause = self.last_taken_at - self._fed_at
            self._fed_without_output += min(pause, ENCODER_PAUSE_SECONDS)
        self._fed_at = self.last_taken_at
        return self._taken()

    async def check_encoder(self) -> None:
        """Ends an ffmpeg that takes pieces and writes nothing. Looked at by the box's timer."""
        encoder = self._encoder
        if encoder is None or encoder.state != "running":
            return
        written = self._playlist_written()
        if written != self._playlist_seen:
            self._playlist_seen = written
            self._fed_without_output = 0.0
        elif self._fed_without_output >= ENCODER_SILENT_SECONDS:
            self._fed_without_output = 0.0
            self._log("encoder_silent", id=self.id, part=self.part, seconds=ENCODER_SILENT_SECONDS)
            await encoder.give_up()

    def describe(self) -> Answer:
        encoder = self._encoder
        return {
            "id": self.id,
            "part": self.part,
            "next_seq": self.next_seq,
            "playable": self.playable,
            "queued_bytes": 0 if encoder is None else encoder.queued_bytes,
            "encoder": "idle" if encoder is None else encoder.state,
        }

    async def close(self) -> None:
        """Kills every ffmpeg of this broadcast and deletes what it wrote."""
        await asyncio.gather(*(encoder.kill() for encoder in self._encoders))
        shutil.rmtree(self.directory, ignore_errors=True)

    def _taken(self) -> Answer:
        return {"ok": True, "next_seq": self.next_seq, "playable": self.playable}

    def _playlist_written(self) -> int | None:
        """When the playlist was last written (ffmpeg replaces the file each time), or None."""
        try:
            return (self.directory / ffmpeg.PLAYLIST_NAME).stat().st_mtime_ns
        except OSError:
            return None

    def _begin(self, part: str, container: Container) -> Encoder:
        """A part that begins ends the part before it."""
        previous = self._encoder
        encoder = Encoder(spawn=self._spawn, log=self._log, broadcast_id=self.id, part=part)
        self._encoders = [other for other in self._encoders if other.state != "exited"]
        self._encoders.append(encoder)
        self._encoder = encoder
        self._parts_seen.append(part)
        self.part = part
        self.next_seq = 0
        self._fed_without_output = 0.0
        self._fed_at = None
        self._playlist_seen = self._playlist_written()
        self._log("part", id=self.id, part=part, container=container)

        def prepare() -> Sequence[str]:
            # No ffmpeg of this broadcast runs at this moment: the one before is gone.
            swept = playlist.sweep(self.directory)
            continues = bool(playlist.listed(self.directory))
            if swept:
                self._log("swept", id=self.id, files=swept)
            return ffmpeg.arguments(self._settings, self.directory, container, continues=continues)

        encoder.start(after=previous, prepare=prepare)
        return encoder
