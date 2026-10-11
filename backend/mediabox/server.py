"""The socket of the media box and the frames that travel over it (SPEC §9).

A Unix stream socket. One request per connection: one line of JSON (an object, UTF-8, at
most REQUEST_LINE_BYTES bytes, ended by a line feed) and, for `chunk`, exactly `size`
bytes after it. The answer is one line of JSON; then the box closes the connection.
"""

import asyncio
import contextlib
import json
import os
import re
import socket
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeGuard

from mediabox.box import Box
from mediabox.broadcast import Answer
from mediabox.ffmpeg import is_broadcast_id

REQUEST_LINE_BYTES: Final = 512
# A request has this long to arrive whole.
REQUEST_SECONDS: Final = 5.0
# Ids a start may hand over at once.
STOP_IDS: Final = 8
SEQ_LIMIT: Final = 10**9

BAD_REQUEST: Final[Answer] = {"ok": False, "error": "bad_request"}

_PART: Final = re.compile(r"[A-Za-z0-9_-]{8,36}")

type PeerRule = Callable[[socket.socket], bool]


@dataclass(frozen=True, slots=True)
class _Request:
    op: str
    id: str = ""
    stop: tuple[str, ...] = ()
    part: str = ""
    seq: int = 0
    size: int = 0


class _BadRequestError(Exception):
    pass


def from_outside(peer: socket.socket) -> bool:
    """Whether the other end of a connection is somebody else than the box's own user.

    The socket's directory admits the box and the API. The API runs under another user id
    than the box, and so does nothing inside the box: a process in here that connects to
    the socket is ffmpeg, or whatever a hostile upload made of it. The kernel says who is
    at the other end (SO_PEERCRED), and such a peer is told the status and nothing else.
    """
    if sys.platform != "linux":
        # The box runs in a Linux container. Anywhere else there is nobody to tell apart.
        return True
    credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", credentials)
    return bool(uid != os.getuid())


async def serve(box: Box, path: Path | str, *, trusted: PeerRule = from_outside) -> asyncio.Server:
    """Listens at `path`. The file gets mode 0666: its directory admits two users only."""

    async def connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        answer: Answer | None
        op = "?"
        try:
            async with asyncio.timeout(REQUEST_SECONDS):
                request = _parse(await reader.readuntil(b"\n"), box.settings.chunk_max_bytes)
                op = request.op
                if op != "status" and not trusted(writer.get_extra_info("socket")):
                    raise _BadRequestError
                body = await reader.readexactly(request.size)
            answer = await _answer(box, request, body)
        except (
            _BadRequestError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            answer = BAD_REQUEST
        except OSError as error:
            # The box could not do what was asked (no room for a directory, for one), or the
            # other end went away. It says nothing, which the API reads as "unavailable",
            # and it goes on serving.
            box.log("request_failed", op=op, error=type(error).__name__)
            answer = None
        with contextlib.suppress(OSError):
            if answer is not None:
                writer.write(json.dumps(answer, separators=(",", ":")).encode() + b"\n")
                await writer.drain()
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_unix_server(connection, path=str(path))
    os.chmod(path, 0o666)
    return server


def _parse(line: bytes, chunk_max_bytes: int) -> _Request:
    if len(line) - 1 > REQUEST_LINE_BYTES:
        raise _BadRequestError
    try:
        fields = json.loads(line)
    except ValueError as error:
        raise _BadRequestError from error
    if not isinstance(fields, dict):
        raise _BadRequestError
    op = fields.get("op")
    if op == "status":
        return _Request(op="status")
    broadcast_id = fields.get("id")
    if not _is_id(broadcast_id):
        raise _BadRequestError
    if op == "stop":
        return _Request(op="stop", id=broadcast_id)
    if op == "start":
        stop = fields.get("stop", [])
        if not isinstance(stop, list) or len(stop) > STOP_IDS:
            raise _BadRequestError
        others = tuple(other for other in stop if _is_id(other))
        if len(others) != len(stop):
            raise _BadRequestError
        return _Request(op="start", id=broadcast_id, stop=others)
    if op == "chunk":
        part, seq, size = fields.get("part"), fields.get("seq"), fields.get("size")
        if not isinstance(part, str) or _PART.fullmatch(part) is None:
            raise _BadRequestError
        if not _is_whole(seq) or not 0 <= seq < SEQ_LIMIT:
            raise _BadRequestError
        if not _is_whole(size) or not 1 <= size <= chunk_max_bytes:
            raise _BadRequestError
        return _Request(op="chunk", id=broadcast_id, part=part, seq=seq, size=size)
    raise _BadRequestError


async def _answer(box: Box, request: _Request, body: bytes) -> Answer | None:
    """What the box says to a well-formed request. None: the box is ending and says nothing."""
    if box.ending:
        return None
    if request.op == "status":
        return box.status()
    if request.op == "start":
        return await box.start(request.id, request.stop)
    if request.op == "stop":
        return await box.stop(request.id)
    return box.chunk(request.id, request.part, request.seq, body)


def _is_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and is_broadcast_id(value)


def _is_whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
