"""The frames of the socket (SPEC §9): what is no request changes nothing."""

import asyncio
import json
import socket
import sys
import time

import pytest

from mediabox.box import Box
from mediabox.server import REQUEST_LINE_BYTES, from_outside, serve
from mediabox.settings import Settings
from tests.unit.mediabox.conftest import ONE, SOCKET, TWO, Events, ask, request, webm
from tests.unit.mediabox.environment import box_environment

BAD_REQUEST = {"ok": False, "error": "bad_request"}
PART = "part-0001"
# One byte more than a piece may have with the values of `.example.env`.
ABOVE_THE_LIMIT = int(box_environment()["BROADCAST_CHUNK_MAX_BYTES"]) + 1


def chunk_header(**changes: object) -> dict[str, object]:
    return {"op": "chunk", "id": ONE, "part": PART, "seq": 0, "size": 400, **changes}


def padded_status(length: int) -> bytes:
    empty = len(json.dumps({"op": "status", "pad": ""}, separators=(",", ":")))
    return request(op="status", pad="x" * (length - empty))


async def test_status_of_an_empty_box(listening: Box) -> None:
    assert await ask(request(op="status")) == {"ok": True, "capacity": 1, "live": []}


async def test_one_request_then_the_connection_is_closed(listening: Box) -> None:
    reader, writer = await asyncio.open_unix_connection(SOCKET)
    writer.write(request(op="status") + request(op="status"))
    await writer.drain()
    assert json.loads(await reader.readline())["ok"] is True
    assert await reader.read() == b""
    writer.close()


async def test_a_header_may_have_512_bytes_and_not_one_more(listening: Box) -> None:
    longest = padded_status(REQUEST_LINE_BYTES)
    assert len(longest) - 1 == REQUEST_LINE_BYTES
    assert (await ask(longest))["ok"] is True
    assert await ask(padded_status(REQUEST_LINE_BYTES + 1)) == BAD_REQUEST


@pytest.mark.parametrize(
    "payload",
    [
        b"hello\n",
        b"[1,2,3]\n",
        b'"status"\n',
        request(op="reboot"),
        request(id=ONE),
        request(op="start", id="../../etc/passwd"),
        request(op="start", id="ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF"),
        request(op="start", id=ONE, stop="everything"),
        request(op="start", id=ONE, stop=[TWO] * 9),
        request(op="start", id=ONE, stop=["nobody"]),
        request(op="stop", id=7),
        request(**chunk_header(id="nobody")) + webm(),
        request(**chunk_header(part="short")) + webm(),
        request(**chunk_header(part="x" * 37)) + webm(),
        request(**chunk_header(part="no/slash/here")) + webm(),
        request(**chunk_header(seq=-1)) + webm(),
        request(**chunk_header(seq=10**9)) + webm(),
        request(**chunk_header(seq=1.5)) + webm(),
        request(**chunk_header(seq=True)) + webm(),
        request(**chunk_header(seq="0")) + webm(),
        request(**chunk_header(size=0)),
        request(**chunk_header(size=-5)) + webm(),
        request(**chunk_header(size=ABOVE_THE_LIMIT)) + webm(),
        request(**chunk_header(size="400")) + webm(),
    ],
)
async def test_what_is_no_request_is_refused_and_changes_nothing(
    listening: Box, payload: bytes
) -> None:
    await ask(request(op="start", id=ONE))
    before = await ask(request(op="status"))
    assert await ask(payload) == BAD_REQUEST
    assert await ask(request(op="status")) == before


async def test_a_body_that_ends_early_is_refused(listening: Box) -> None:
    await ask(request(op="start", id=ONE))
    assert await ask(request(**chunk_header()) + webm(100), hang_up=True) == BAD_REQUEST
    status = await ask(request(op="status"))
    assert status["live"][0]["next_seq"] == 0


async def test_a_request_has_five_seconds_to_arrive(
    listening: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mediabox.server.REQUEST_SECONDS", 0.3)
    began = time.monotonic()
    assert await ask(request(**chunk_header()) + webm(100)) == BAD_REQUEST
    assert 0.25 <= time.monotonic() - began < 4


async def test_a_size_above_the_limit_is_refused_without_reading_the_body(
    listening: Box, settings: Settings
) -> None:
    await ask(request(op="start", id=ONE))
    began = time.monotonic()
    # Only the header is sent: an answer at once shows that the box waits for no body.
    assert settings.chunk_max_bytes + 1 == ABOVE_THE_LIMIT
    assert await ask(request(**chunk_header(size=ABOVE_THE_LIMIT))) == BAD_REQUEST
    assert time.monotonic() - began < 3  # far from the five seconds a body is waited for


async def test_a_piece_of_the_largest_size_is_taken(listening: Box, settings: Settings) -> None:
    await ask(request(op="start", id=ONE))
    body = webm(settings.chunk_max_bytes)
    assert await ask(request(**chunk_header(size=len(body))) + body) == {
        "ok": True,
        "next_seq": 1,
        "playable": False,
    }


async def test_a_request_the_box_cannot_serve_gets_no_answer_and_the_box_goes_on(
    listening: Box, settings: Settings, events: Events
) -> None:
    # No room for the broadcast's directory: a file stands where the HLS volume should be.
    settings.hls_root.parent.mkdir(parents=True, exist_ok=True)
    settings.hls_root.write_text("in the way")
    assert await ask(request(op="start", id=ONE)) is None
    assert events.named("request_failed") == [
        {"event": "request_failed", "op": "start", "error": "NotADirectoryError"}
    ]
    settings.hls_root.unlink()
    assert await ask(request(op="start", id=ONE)) == {"ok": True}


async def test_the_socket_file_admits_everybody_its_directory_admits(listening: Box) -> None:
    import os
    import stat

    assert stat.S_IMODE(os.stat(SOCKET).st_mode) == 0o666


async def test_a_peer_from_inside_the_box_is_told_the_status_and_nothing_else(box: Box) -> None:
    server = await serve(box, SOCKET, trusted=lambda peer: False)
    try:
        assert (await ask(request(op="status")))["ok"] is True
        assert await ask(request(op="start", id=ONE)) == BAD_REQUEST
        assert await ask(request(op="stop", id=ONE)) == BAD_REQUEST
        assert await ask(request(**chunk_header()) + webm()) == BAD_REQUEST
        assert await ask(request(op="status")) == {"ok": True, "capacity": 1, "live": []}
    finally:
        server.close()
        await server.wait_closed()


def test_who_is_at_the_other_end_is_asked_of_the_kernel() -> None:
    # Both ends are this process. On Linux, where the box runs, the kernel says so and the
    # peer is one from inside; anywhere else there is nobody to tell apart.
    here, there = socket.socketpair()
    try:
        assert from_outside(here) is (sys.platform != "linux")
    finally:
        here.close()
        there.close()
