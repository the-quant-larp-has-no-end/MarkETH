"""What the route of a broadcast's pieces does with a body (SPEC §6, §10).

`POST /broadcasts/{id}/chunks` is the one operation whose body is no JSON. Its route
asks the service whether the caller may send before it reads a byte, reads no more
than one byte above what a piece may have, and hands nothing on of a body that did not
arrive whole. A recorder stands where `BroadcastService` is, so each test shows the
route alone.

Nothing here needs a data store: the application runs on a container that connects to
nothing.
"""

from collections.abc import AsyncIterator
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message, Scope

from app.api.deps import authenticate_request
from app.container import Container
from app.core.config import Settings
from app.core.enums import UserRole, UserStatus
from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.core.identity import AuthenticatedUser, Caller, PortalIdentity
from app.main import create_app
from app.schemas.broadcasts import BroadcastChunkResponse
from app.services.broadcasts import (
    ENDED_MESSAGE,
    NO_DATA_MESSAGE,
    NOT_THE_BROADCASTER_MESSAGE,
)
from tests.support.clock import FrozenClock
from tests.support.containers import LOOPBACK, closed_port

BROADCASTER: Final[AuthenticatedUser] = AuthenticatedUser(
    id=uuid7(),
    email="proposer@ethz.ch",
    name="Petra Proposer",
    username="petra",
    degree_programme="BSc Computer Science",
    role=UserRole.USER,
    status=UserStatus.ACTIVE,
)
"""Who every request of these tests comes from."""

PART: Final[str] = "part-0001"

PIECE_BYTES: Final[int] = 256 * 1024
"""The bytes one piece of a test body has as it arrives."""


class RecordedBroadcasts:
    """Stands in for `BroadcastService`: remembers what the route asked and handed over,
    and refuses the caller with `refusal` when one is set."""

    def __init__(self) -> None:
        self.refusal: AppError | None = None
        self.asked: list[tuple[AuthenticatedUser, UUID]] = []
        self.taken: list[tuple[AuthenticatedUser, UUID, str, int, bytes]] = []

    async def admit_upload(self, user: AuthenticatedUser, broadcast_id: UUID) -> None:
        self.asked.append((user, broadcast_id))
        if self.refusal is not None:
            raise self.refusal

    async def take_chunk(
        self, user: AuthenticatedUser, broadcast_id: UUID, part: str, seq: int, data: bytes
    ) -> BroadcastChunkResponse:
        self.taken.append((user, broadcast_id, part, seq, data))
        return BroadcastChunkResponse(restart=False, next_seq=seq + 1)


class Pieces:
    """A body that arrives piece by piece and counts the pieces that were asked for."""

    def __init__(self, pieces: list[bytes]) -> None:
        self._pieces = pieces
        self.handed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for piece in self._pieces:
            self.handed += 1
            yield piece


@pytest.fixture
def nowhere(base_settings: Settings) -> Settings:
    """Settings whose three stores are ports nothing listens on: no test here asks one."""
    return base_settings.model_copy(
        update={
            "postgres": base_settings.postgres.model_copy(
                update={"host": LOOPBACK, "port": closed_port()}
            ),
            "redis": base_settings.redis.model_copy(
                update={"host": LOOPBACK, "port": closed_port()}
            ),
            "scylla": base_settings.scylla.model_copy(
                update={"hosts": [LOOPBACK], "port": closed_port()}
            ),
        }
    )


@pytest.fixture
def broadcasts() -> RecordedBroadcasts:
    return RecordedBroadcasts()


@pytest.fixture
async def application(nowhere: Settings, broadcasts: RecordedBroadcasts) -> AsyncIterator[FastAPI]:
    """The real application, called by the broadcaster, with the recorder for its
    broadcast service."""
    container = Container.build(nowhere, clock=FrozenClock())
    container.broadcasts = broadcasts  # type: ignore[assignment]
    built = create_app(container)
    caller = Caller(
        identity=PortalIdentity(
            email=BROADCASTER.email, name=BROADCASTER.name, role=BROADCASTER.role
        ),
        user=BROADCASTER,
    )
    built.dependency_overrides[authenticate_request] = lambda: caller
    yield built
    await container.stop()


@pytest.fixture
async def api(application: FastAPI, nowhere: Settings) -> AsyncIterator[httpx.AsyncClient]:
    origin = nowhere.app.public_base_url
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url=origin,
        headers={"Origin": origin},
    ) as client:
        yield client


def chunks_of(broadcast_id: UUID) -> str:
    return f"/api/broadcasts/{broadcast_id}/chunks"


async def test_a_piece_reaches_the_service_whole_with_its_part_and_its_place(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts
) -> None:
    broadcast_id = uuid7()
    recorded = [b"\x1a\x45\xdf\xa3" + bytes(PIECE_BYTES), b"the rest of the piece"]

    response = await api.post(
        chunks_of(broadcast_id), params={"part": PART, "seq": 7}, content=Pieces(recorded)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"restart": False, "next_seq": 8}
    assert broadcasts.asked == [(BROADCASTER, broadcast_id)]
    assert broadcasts.taken == [(BROADCASTER, broadcast_id, PART, 7, b"".join(recorded))]


@pytest.mark.parametrize(
    ("refusal", "status_code", "code"),
    [
        (NotFoundError(), 404, "NOT_FOUND"),
        (ForbiddenError(NOT_THE_BROADCASTER_MESSAGE), 403, "FORBIDDEN"),
    ],
    ids=["nobody-to-this-broadcast", "an-admin-who-is-not-its-broadcaster"],
)
async def test_a_caller_who_may_not_send_is_answered_before_a_byte_of_the_body_is_read(
    api: httpx.AsyncClient,
    broadcasts: RecordedBroadcasts,
    refusal: AppError,
    status_code: int,
    code: str,
) -> None:
    broadcasts.refusal = refusal
    body = Pieces([bytes(PIECE_BYTES)] * 4)

    response = await api.post(chunks_of(uuid7()), params={"part": PART, "seq": 0}, content=body)

    assert (response.status_code, response.json()["code"]) == (status_code, code)
    assert response.json()["message"] == refusal.message
    assert body.handed == 0, "the body was read for a caller who may not send"
    assert broadcasts.taken == []


async def test_a_broadcast_that_is_not_live_is_answered_once_the_body_was_read_and_dropped(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts
) -> None:
    """The studio receives the refusal with its details, not a connection that broke
    while it sent, and nothing of the body goes anywhere."""
    where_it_stands = {"status": "ENDED", "end_reason": "STALLED"}
    broadcasts.refusal = ConflictError(ENDED_MESSAGE, details=where_it_stands)
    body = Pieces([bytes(PIECE_BYTES)] * 3)

    response = await api.post(chunks_of(uuid7()), params={"part": PART, "seq": 3}, content=body)

    assert response.status_code == 409, response.text
    assert response.json() == {
        "code": "CONFLICT",
        "message": ENDED_MESSAGE,
        "details": where_it_stands,
    }
    assert body.handed == 3
    assert broadcasts.taken == []


async def test_of_a_body_above_what_a_piece_may_have_one_byte_more_is_read_and_no_more(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts, nowhere: Settings
) -> None:
    """The service is handed one byte more than a piece may have, which is how it knows
    that the body is too large. What follows the piece that crossed the line is not
    read, whether the caller may send or not."""
    most = nowhere.broadcast.chunk_max_bytes
    crossing = most // PIECE_BYTES + 1
    assert crossing * PIECE_BYTES > most

    for refusal in (None, ConflictError(ENDED_MESSAGE)):
        broadcasts.refusal = refusal
        body = Pieces([b"\x07" * PIECE_BYTES] * (crossing + 5))
        await api.post(chunks_of(uuid7()), params={"part": PART, "seq": 0}, content=body)
        assert body.handed == crossing, refusal

    ((_, _, _, _, data),) = broadcasts.taken
    assert len(data) == most + 1


async def test_a_body_as_large_as_a_piece_may_be_is_handed_over_whole(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts, nowhere: Settings
) -> None:
    most = nowhere.broadcast.chunk_max_bytes

    await api.post(chunks_of(uuid7()), params={"part": PART, "seq": 0}, content=bytes(most))

    ((_, _, _, _, data),) = broadcasts.taken
    assert len(data) == most


async def test_an_empty_body_reaches_the_service_as_no_bytes(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts
) -> None:
    """Whether a piece without bytes is one is the service's to say (`NO_DATA_MESSAGE`):
    the route hands over what arrived."""
    assert NO_DATA_MESSAGE

    for sent in (None, b"", Pieces([])):
        await api.post(chunks_of(uuid7()), params={"part": PART, "seq": 0}, content=sent)

    assert [data for *_, data in broadcasts.taken] == [b"", b"", b""]


@pytest.mark.parametrize(
    "query",
    [{"seq": 0}, {"part": PART}, {"part": "short", "seq": 0}, {"part": PART, "seq": -1}],
    ids=["no-part", "no-seq", "a-part-that-is-none", "a-place-before-the-first"],
)
async def test_a_piece_that_does_not_say_which_it_is_never_reaches_the_service(
    api: httpx.AsyncClient, broadcasts: RecordedBroadcasts, query: dict[str, Any]
) -> None:
    body = Pieces([bytes(PIECE_BYTES)])

    response = await api.post(chunks_of(uuid7()), params=query, content=body)

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")
    assert (broadcasts.asked, broadcasts.taken, body.handed) == ([], [], 0)


async def test_nothing_of_a_body_that_did_not_arrive_whole_reaches_the_service(
    application: FastAPI, broadcasts: RecordedBroadcasts, nowhere: Settings
) -> None:
    """A studio's connection breaks while it sends. The piece it sent a part of is not
    handed on as if it were whole: under the same `seq` the studio sends all of it
    again."""
    arriving: list[Message] = [
        {"type": "http.request", "body": bytes(PIECE_BYTES), "more_body": True},
        {"type": "http.disconnect"},
    ]
    answered: list[Message] = []

    async def receive() -> Message:
        return arriving.pop(0)

    async def send(message: Message) -> None:
        answered.append(message)

    broadcast_id = uuid7()
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": chunks_of(broadcast_id),
        "raw_path": chunks_of(broadcast_id).encode(),
        "query_string": f"part={PART}&seq=4".encode(),
        "root_path": "",
        "headers": [
            (b"origin", nowhere.app.public_base_url.encode()),
            (b"content-type", b"application/octet-stream"),
            (b"content-length", str(4 * PIECE_BYTES).encode()),
        ],
        "client": (LOOPBACK, 50000),
        "server": (LOOPBACK, 8000),
    }

    await application(scope, receive, send)

    assert broadcasts.asked == [(BROADCASTER, broadcast_id)]
    assert broadcasts.taken == []
    assert arriving == [], "the route stopped reading before the connection broke"
    (started,) = [message for message in answered if message["type"] == "http.response.start"]
    assert started["status"] == 503
