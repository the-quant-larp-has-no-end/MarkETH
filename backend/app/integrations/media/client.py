"""The client of the media box, over its Unix socket (SPEC §9).

The box is a container without a network. It listens on a Unix stream socket in a
volume it shares with the API, and takes one request per connection: one line of JSON
and, for a piece of video, exactly `size` bytes after it. Its answer is one line of
JSON; then it closes the connection.

    {"op":"status"}                                         what the box holds
    {"op":"start","id":"<uuid>","stop":["<uuid>", ...]}     a place for a broadcast
    {"op":"chunk","id":"<uuid>","part":"<part>","seq":7,"size":183422} and the bytes
    {"op":"stop","id":"<uuid>"}                             end one

This module speaks those lines and shares no code with the box: the box is another
image, and the API never imports it. The standard library alone.

**When the box is down.** A socket file that is not there, nobody listening behind it,
a connection that closes without a line, an answer that is no line of JSON or says
something a request cannot be answered with, and anything that takes longer than
`BROADCAST_MEDIA_TIMEOUT_MS` are one thing here: `MediaUnavailableError`, after at most
one timeout. The box says nothing for about a second after its last broadcast was
stopped, while its container starts again empty. No live stream starts or runs
without the box, and everything else of the application works. `ping` raises nothing.
"""

import asyncio
import contextlib
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from app.core.config import BroadcastSettings

REQUEST_LINE_BYTES: Final[int] = 512
"""The longest request line the box takes, without its line feed."""

ANSWER_LINE_BYTES: Final[int] = 4096
"""The longest answer the client reads. A longer one is no answer of the box."""

STOP_IDS: Final[int] = 8
"""The most broadcasts one `start` can hand over."""

_BAD_REQUEST: Final[str] = "bad_request"
_FULL: Final[str] = "full"


class MediaUnavailableError(Exception):
    """The media box did not do what was asked: unreachable, too slow, or its answer
    is none."""


class ChunkRefusal(StrEnum):
    """Why the box did not take a piece."""

    RESTART = "restart"
    """The piece does not continue the part at hand: the studio begins another part."""

    BUSY = "busy"
    """The queue of the box is full. Nothing of the piece was taken: it is sent again."""

    UNKNOWN = "unknown"
    """The box does not hold this broadcast: it started again, or stopped the broadcast
    by its own timer. The broadcast takes a place again before a piece is sent."""


@dataclass(frozen=True, slots=True)
class ChunkAnswer:
    """What the box says about one piece of a broadcast."""

    refusal: ChunkRefusal | None
    """Why the piece is not taken. None when the box holds it: it took it now, or held
    it already. The box does not say which of the two."""

    next_seq: int
    """The `seq` the box takes next in this part; 0 with a refusal."""

    playable: bool
    """The playlist of the broadcast lists enough segments to play. False with a
    refusal."""

    @property
    def taken(self) -> bool:
        return self.refusal is None


@dataclass(frozen=True, slots=True)
class HeldBroadcast:
    """One broadcast the box holds, as its status reports it."""

    id: UUID
    next_seq: int
    playable: bool


@dataclass(frozen=True, slots=True)
class BoxStatus:
    """What the box holds."""

    capacity: int
    """How many broadcasts it holds at most: its `BROADCAST_MAX_LIVE`."""

    live: tuple[HeldBroadcast, ...]


class MediaClient:
    """The requests the API makes of the media box, each one connection to the socket
    `BROADCAST_MEDIA_SOCKET`, bounded by `BROADCAST_MEDIA_TIMEOUT_MS` from the
    connection to the answer.

    When the box is unreachable: the request that finds out raises
    `MediaUnavailableError` after at most one timeout. `ping` raises nothing and
    answers False.
    """

    def __init__(self, settings: BroadcastSettings) -> None:
        self._socket = settings.media_socket
        self._timeout_seconds = settings.media_timeout_ms / 1000

    async def status(self) -> BoxStatus:
        """What the box holds: its capacity and its broadcasts."""
        answer = await self._ask({"op": "status"})
        try:
            if answer["ok"] is not True:
                raise MediaUnavailableError("the media box refused to say what it holds")
            return BoxStatus(
                capacity=_whole(answer["capacity"]),
                live=tuple(
                    HeldBroadcast(
                        id=UUID(held["id"]),
                        next_seq=_whole(held["next_seq"]),
                        playable=held["playable"] is True,
                    )
                    for held in answer["live"]
                ),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise MediaUnavailableError("the status of the media box is not one") from error

    async def start(self, broadcast_id: UUID, stop: Collection[UUID] = ()) -> bool:
        """Give `broadcast_id` a place in the box, after the ids of `stop` gave theirs
        back: the box swaps them without going away in between. False when the box
        has no place. A broadcast the box holds already keeps its place: True.

        `stop` takes at most `STOP_IDS` ids; more is a `ValueError`, before anything
        is sent.
        """
        if len(stop) > STOP_IDS:
            raise ValueError(f"a start hands over at most {STOP_IDS} broadcasts, not {len(stop)}")
        request: dict[str, Any] = {"op": "start", "id": str(broadcast_id)}
        if stop:
            request["stop"] = [str(other) for other in stop]
        answer = await self._ask(request)
        if answer.get("ok") is True:
            return True
        if answer.get("error") == _FULL:
            return False
        raise _not_understood("start", answer)

    async def chunk(self, broadcast_id: UUID, part: str, seq: int, data: bytes) -> ChunkAnswer:
        """Hand one piece of a part to the box. `data` holds at least one byte and at
        most `BROADCAST_CHUNK_MAX_BYTES`; the box refuses anything else unread."""
        request = {
            "op": "chunk",
            "id": str(broadcast_id),
            "part": part,
            "seq": seq,
            "size": len(data),
        }
        answer = await self._ask(request, data)
        if answer.get("ok") is True:
            try:
                return ChunkAnswer(
                    refusal=None,
                    next_seq=_whole(answer["next_seq"]),
                    playable=answer["playable"] is True,
                )
            except (KeyError, TypeError) as error:
                raise _not_understood("chunk", answer) from error
        try:
            refusal = ChunkRefusal(str(answer.get("error")))
        except ValueError:
            raise _not_understood("chunk", answer) from None
        return ChunkAnswer(refusal=refusal, next_seq=0, playable=False)

    async def stop(self, broadcast_id: UUID) -> None:
        """End what the box holds of `broadcast_id`: its encoder, its playlist and its
        segments. An id it does not hold is no error."""
        answer = await self._ask({"op": "stop", "id": str(broadcast_id)})
        if answer.get("ok") is not True:
            raise _not_understood("stop", answer)

    async def ping(self) -> bool:
        """Whether the box answers. Never raises."""
        try:
            await self.status()
        except MediaUnavailableError:
            return False
        return True

    async def _ask(self, request: Mapping[str, Any], body: bytes = b"") -> dict[str, Any]:
        """Send one request and read its answer: a JSON object.

        Everything that keeps the answer away is `MediaUnavailableError`, and the whole
        exchange has `BROADCAST_MEDIA_TIMEOUT_MS`. The box's `bad_request` is among
        them: it means that the box and the API do not speak the same lines.
        """
        line = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        if len(line) - 1 > REQUEST_LINE_BYTES:
            raise ValueError(
                f"a request line of {len(line) - 1} bytes is longer than the box takes"
            )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                answered = await self._exchange(line, body)
        except TimeoutError as error:
            raise MediaUnavailableError("the media box did not answer in time") from error
        except OSError as error:
            raise MediaUnavailableError("the media box is not reachable") from error
        if not answered.endswith(b"\n"):
            # No line at all: the box is ending, or it could not do what was asked.
            raise MediaUnavailableError("the media box closed the connection without an answer")
        try:
            answer = json.loads(answered)
        except ValueError as error:
            raise MediaUnavailableError("the answer of the media box is no JSON") from error
        if not isinstance(answer, dict):
            raise MediaUnavailableError("the answer of the media box is no JSON object")
        if answer.get("error") == _BAD_REQUEST:
            raise MediaUnavailableError(f"the media box did not understand {request.get('op')!r}")
        return answer

    async def _exchange(self, line: bytes, body: bytes) -> bytes:
        """One connection: the request goes out, the answer line comes back. Empty
        bytes, or bytes without a line feed at their end, when the box closed first."""
        reader, writer = await asyncio.open_unix_connection(self._socket, limit=ANSWER_LINE_BYTES)
        try:
            writer.write(line)
            if body:
                writer.write(body)
            await writer.drain()
            try:
                return await reader.readline()
            except ValueError:
                # More than ANSWER_LINE_BYTES without a line feed.
                return b""
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{value!r} is no whole number")
    return value


def _not_understood(op: str, answer: Mapping[str, Any]) -> MediaUnavailableError:
    return MediaUnavailableError(
        f"the media box answered {op!r} with something that is no answer to it: "
        f"{json.dumps(answer)[:200]}"
    )
