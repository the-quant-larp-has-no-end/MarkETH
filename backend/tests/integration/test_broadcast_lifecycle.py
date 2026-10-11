"""The life of a market's live stream: asking, deciding, going live and ending
(SPEC §6).

    (request)      -> REQUESTED
    REQUESTED      -> APPROVED (approve) | REFUSED (refuse)
    APPROVED       -> LIVE (start)       | REFUSED (refuse)
    (admin start)  -> LIVE
    LIVE           -> ENDED (end, the stall rule, the time limit)

The application runs on Postgres and Redis, with `FakeMediaBox` where the media box
stands (tests/support/media.py). Each test makes its requests through the routes, as
the user it names, and reads what the database holds afterwards.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable
from datetime import timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.core.enums import (
    BroadcastEndReason,
    BroadcastStatus,
    MarketStatus,
    UserRole,
)
from app.core.errors import AppError, ForbiddenError
from app.models import JournalEntry
from app.models.chat import ChatMessage
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.broadcasts import RefuseBroadcastRequest
from app.services import UowFactory
from app.services.broadcasts import (
    ADMINS_START_MESSAGE,
    DECIDED_MESSAGE,
    ENDED_MESSAGE,
    LIVE_NOT_REFUSED_MESSAGE,
    MEDIA_UNAVAILABLE_MESSAGE,
    NOT_APPROVED_MESSAGE,
    NOT_STARTED_MESSAGE,
    NOT_STREAMABLE_MESSAGE,
    NOT_THE_BROADCASTER_MESSAGE,
    NOT_THE_PROPOSER_MESSAGE,
    OPEN_BROADCAST_MESSAGE,
    REQUEST_LIMIT_MESSAGE,
    SLOT_TAKEN_MESSAGE,
)
from tests.support.books import assert_books_balance, count_rows
from tests.support.broadcasts import (
    PART,
    Calls,
    RecordedEnds,
    all_broadcasts,
    as_json,
    audit_rows,
    broadcast,
    broadcasts_of,
    refusal,
    service_with,
    stored,
)
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.media import FakeMediaBox, behind, later, webm
from tests.support.statements import recorded_statements, takes_a_row_lock, writes
from tests.support.streams import MARKETS, broadcast_of, listening, settled
from tests.support.units_of_work import CountedUnitsOfWork

pytestmark = pytest.mark.integration

NOT_FOUND: Final[tuple[int, str, str, dict[str, Any]]] = (404, "NOT_FOUND", "Not found.", {})
"""What an id nobody has is answered with, and a market the caller is not inside of."""

MEDIA_UNAVAILABLE: Final[tuple[int, str, str, dict[str, Any]]] = (
    503,
    "SERVICE_UNAVAILABLE",
    MEDIA_UNAVAILABLE_MESSAGE,
    {},
)
"""What a start is answered with while the media box cannot give it a place."""

NO_FLAG: Final[dict[str, bool]] = dict.fromkeys(
    (
        "is_broadcaster",
        "can_request",
        "can_start",
        "can_start_as_admin",
        "can_approve",
        "can_refuse",
        "can_end",
        "can_watch",
        "full",
    ),
    False,
)

SECOND: Final[timedelta] = timedelta(seconds=1)


@pytest.fixture
async def box() -> AsyncIterator[FakeMediaBox]:
    async with FakeMediaBox() as listening_box:
        yield listening_box


@pytest.fixture
def limits() -> dict[str, object]:
    """Other values of the section "Live video" a test runs with."""
    return {}


@pytest.fixture
def container_settings(
    container_settings: Settings, box: FakeMediaBox, limits: dict[str, object]
) -> Settings:
    """The settings of the application, with the media box of the test behind them."""
    return behind(container_settings, box, **limits)


@pytest.fixture
def calls(client: httpx.AsyncClient, as_user: ActAs) -> Calls:
    return Calls(client, as_user)


async def piece_taken_now(container: Container, broadcast_id: UUID) -> None:
    """Move `last_chunk_at` of a LIVE broadcast to now, as a piece that arrives does: its
    studio is sending, so only the time limit ends it."""
    async with container.uow_factory() as uow:
        await uow.broadcasts.heartbeat(broadcast_id, container.clock.now(), on_air=False)


def flags(response: httpx.Response) -> set[str]:
    """The flags of an answer that are true."""
    return {name for name, value in response.json()["viewer"].items() if value}


def shown(response: httpx.Response) -> dict[str, Any]:
    """The broadcast of an answer."""
    assert response.status_code in (200, 201), response.text
    found = response.json()["broadcast"]
    assert found is not None, "the answer shows no broadcast"
    return dict(found)


# --- An admin goes live --------------------------------------------------------------


async def test_an_admin_goes_live_on_a_market_without_asking(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()

    response = await calls.start_as_admin(admin, market.id)

    assert response.status_code == 201, response.text
    (row,) = await broadcasts_of(container, market)
    assert response.json() == {
        "broadcast": {
            "id": str(row.id),
            "market_id": str(market.id),
            "status": "LIVE",
            "on_air": False,
            "playlist_url": None,
            "requested_at": as_json(TEST_NOW),
            "started_at": as_json(TEST_NOW),
            "ended_at": None,
            "end_reason": None,
            "refusal_reason": None,
            "requested_by": {"username": "admin0", "email": admin.email},
        },
        "viewer": NO_FLAG | {"is_broadcaster": True, "can_end": True},
    }
    assert (row.status, row.broadcaster_id, row.decided_by) == (
        BroadcastStatus.LIVE,
        admin.id,
        admin.id,
    )
    assert row.requested_at == row.decided_at == row.started_at == row.last_chunk_at == TEST_NOW
    assert (row.on_air_at, row.ended_at, row.resumed_from) == (None, None, None)
    assert box.requests == [{"op": "start", "id": str(row.id)}]
    assert box.held == [row.id]
    (audit,) = await audit_rows(container)
    assert (audit.actor_id, audit.action, audit.target) == (
        admin.id,
        "broadcast.start",
        f"broadcast:{row.id}",
    )
    assert (audit.before, audit.after) == (None, {"status": "LIVE", "market_id": str(market.id)})


async def test_only_an_admin_goes_live_without_asking(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)

    through_the_route = await calls.start_as_admin(proposer, market.id)
    with pytest.raises(ForbiddenError):
        await container.broadcasts.start_as_admin(factory.identity(proposer), market.id)

    assert refusal(through_the_route)[:2] == (403, "FORBIDDEN")
    assert await all_broadcasts(container) == []
    assert box.requests == []


async def test_a_market_nobody_is_inside_of_has_no_live_stream(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """A proposal has no live stream, to its proposer and to an admin either, and an id
    no market has is answered the same."""
    admin = await factory.admin()
    proposer = await factory.user()
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    rejected = await factory.market(status=MarketStatus.REJECTED, created_by=proposer)

    for market_id in (proposal.id, rejected.id, uuid7()):
        assert refusal(await calls.start_as_admin(admin, market_id)) == NOT_FOUND
        assert refusal(await calls.read(admin, market_id)) == NOT_FOUND
        assert refusal(await calls.read(proposer, market_id)) == NOT_FOUND
        assert refusal(await calls.request(proposer, market_id)) == NOT_FOUND

    assert await all_broadcasts(container) == []
    assert box.requests == []


@pytest.mark.parametrize(
    ("status", "closes_in", "streamable"),
    [
        (MarketStatus.OPEN, timedelta(days=1), True),
        (MarketStatus.OPEN, -timedelta(hours=1), True),
        (MarketStatus.HALTED, timedelta(days=1), True),
        (MarketStatus.CLOSED, -timedelta(hours=1), True),
        (MarketStatus.RESOLVED, -timedelta(hours=1), False),
        (MarketStatus.VOID, -timedelta(hours=1), False),
    ],
    ids=["open", "open-past-its-close", "halted", "closed", "resolved", "void"],
)
async def test_a_live_stream_starts_while_a_market_is_open_paused_or_waits_for_its_result(
    calls: Calls,
    container: Container,
    factory: Factory,
    status: MarketStatus,
    closes_in: timedelta,
    streamable: bool,
) -> None:
    admin = await factory.admin()
    proposer = await factory.user()
    market = await factory.market(status=status, close_in=closes_in, created_by=proposer)

    before = await calls.read(admin, market.id)
    as_proposer = await calls.read(proposer, market.id)
    asked = await calls.request(proposer, market.id)
    started = await calls.start_as_admin(admin, market.id)

    assert flags(before) == ({"can_start_as_admin"} if streamable else set())
    assert flags(as_proposer) == ({"can_request"} if streamable else set())
    if streamable:
        assert (asked.status_code, started.status_code) == (201, 409)
        assert refusal(started) == (409, "CONFLICT", OPEN_BROADCAST_MESSAGE, {})
    else:
        assert refusal(asked) == (409, "CONFLICT", NOT_STREAMABLE_MESSAGE, {})
        assert refusal(started) == (409, "CONFLICT", NOT_STREAMABLE_MESSAGE, {})
        assert await all_broadcasts(container) == []


async def test_a_market_has_one_open_live_stream_at_a_time(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    proposer = await factory.user()
    asked_for = await factory.market(created_by=proposer)
    approved = await factory.market(created_by=proposer)
    on = await factory.market(created_by=proposer)
    await broadcast(container, asked_for, proposer, BroadcastStatus.REQUESTED)
    await broadcast(container, approved, proposer, BroadcastStatus.APPROVED, decided_by=admin)
    await broadcast(container, on, other_admin, BroadcastStatus.LIVE)

    for market in (asked_for, approved, on):
        response = await calls.start_as_admin(admin, market.id)
        assert refusal(response) == (409, "CONFLICT", OPEN_BROADCAST_MESSAGE, {})
        assert "can_start_as_admin" not in flags(await calls.read(admin, market.id))
    assert refusal(await calls.request(proposer, on.id)) == (
        409,
        "CONFLICT",
        OPEN_BROADCAST_MESSAGE,
        {},
    )

    assert len(await all_broadcasts(container)) == 3
    assert box.requests == []


async def test_a_start_that_is_sent_again_answers_the_same_live_stream_and_asks_the_box_nothing(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """The answer to a start can get lost."""
    admin = await factory.admin()
    market = await factory.market()

    first = await calls.start_as_admin(admin, market.id)
    again = await calls.start_as_admin(admin, market.id)

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert len(await broadcasts_of(container, market)) == 1
    assert len(await audit_rows(container)) == 1
    assert box.asked("start") == [{"op": "start", "id": shown(first)["id"]}]


async def test_one_live_stream_runs_at_a_time_and_the_sentence_names_no_market(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    first_market = await factory.market(question="Is the first market on?")
    second_market = await factory.market(question="Is the second market on?")
    assert container.settings.broadcast.max_live == 1
    first = shown(await calls.start_as_admin(admin, first_market.id))

    refused = await calls.start_as_admin(other_admin, second_market.id)

    assert refusal(refused) == (
        409,
        "LIMIT_EXCEEDED",
        SLOT_TAKEN_MESSAGE,
        {"limit": "MAX_LIVE_BROADCASTS"},
    )
    assert "first" not in refused.text
    assert await broadcasts_of(container, second_market) == []
    # The flag does not know of the limit: the operation is its judge.
    assert flags(await calls.read(other_admin, second_market.id)) == {"can_start_as_admin"}

    assert (await calls.end(admin, first["id"])).status_code == 200
    passed = await calls.start_as_admin(other_admin, second_market.id)

    assert passed.status_code == 201, passed.text
    assert box.held == [(await broadcasts_of(container, second_market))[0].id]


async def test_two_starts_at_once_end_with_one_live_stream(
    container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    markets = [await factory.market(), await factory.market()]

    outcomes = await asyncio.gather(
        container.broadcasts.start_as_admin(factory.identity(admin), markets[0].id),
        container.broadcasts.start_as_admin(factory.identity(other_admin), markets[1].id),
        return_exceptions=True,
    )

    refused = [outcome for outcome in outcomes if isinstance(outcome, AppError)]
    assert [(error.code, error.message) for error in refused] == [
        ("LIMIT_EXCEEDED", SLOT_TAKEN_MESSAGE)
    ]
    (row,) = await all_broadcasts(container)
    assert row.status is BroadcastStatus.LIVE
    assert box.held == [row.id]


@pytest.mark.parametrize("limits", [{"media_timeout_ms": 300}])
@pytest.mark.parametrize("trouble", ["gone", "silent", "full"])
async def test_a_start_that_cannot_have_its_place_in_the_box_writes_nothing(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, trouble: str
) -> None:
    admin = await factory.admin()
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    other_market = await factory.market(created_by=proposer)
    approved = await broadcast(
        container, other_market, proposer, BroadcastStatus.APPROVED, decided_by=admin
    )
    if trouble == "gone":
        await box.gone()
    elif trouble == "silent":
        box.hang()
    else:
        box.start_full = True

    async with listening(container.live, factory.identity(proposer), [MARKETS]) as heard:
        as_admin = await calls.start_as_admin(admin, market.id)
        as_proposer = await calls.start(proposer, approved.id)
        await settled()

    assert refusal(as_admin) == MEDIA_UNAVAILABLE
    assert refusal(as_proposer) == MEDIA_UNAVAILABLE
    assert await broadcasts_of(container, market) == []
    assert (await stored(container, approved.id)).status is BroadcastStatus.APPROVED
    assert (await stored(container, approved.id)).started_at is None
    assert await audit_rows(container) == []
    assert heard.names == []
    assert box.held == []


class CommitFailed(Exception):
    """What the commit of `units_whose_commit_fails` raises."""


def units_whose_commit_fails(container: Container) -> UowFactory:
    """Units of work that do everything but commit: Postgres went away at that moment."""

    def make() -> UnitOfWork:
        uow = container.uow_factory()

        async def fail() -> None:
            raise CommitFailed

        uow.session.commit = fail  # type: ignore[method-assign]
        return uow

    return make


async def test_a_start_whose_commit_fails_gives_its_place_in_the_box_back(
    container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """The box said yes and the row was never committed: the place is not kept for a
    broadcast no row calls LIVE, which would shut every other start out."""
    admin = await factory.admin()
    market = await factory.market()
    service = service_with(
        container, on_end=RecordedEnds(), uow_factory=units_whose_commit_fails(container)
    )

    with pytest.raises(CommitFailed):
        await service.start_as_admin(factory.identity(admin), market.id)

    assert await all_broadcasts(container) == []
    assert [request["op"] for request in box.requests] == ["start", "stop"]
    assert box.requests[0]["id"] == box.requests[1]["id"]
    assert box.held == []


async def test_a_start_that_is_cancelled_before_its_commit_gives_its_place_back(
    container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """The caller hangs up while the transaction of its start is on its way to the
    commit, after the box said yes."""
    reached, never = asyncio.Event(), asyncio.Event()

    def units_that_wait_at_the_commit() -> UnitOfWork:
        uow = container.uow_factory()

        async def wait() -> None:
            reached.set()
            await never.wait()

        uow.session.commit = wait  # type: ignore[method-assign]
        return uow

    admin = await factory.admin()
    market = await factory.market()
    service = service_with(
        container, on_end=RecordedEnds(), uow_factory=units_that_wait_at_the_commit
    )

    starting = asyncio.create_task(service.start_as_admin(factory.identity(admin), market.id))
    await reached.wait()
    assert [request["op"] for request in box.requests] == ["start"]
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting

    assert await all_broadcasts(container) == []
    assert [request["op"] for request in box.requests] == ["start", "stop"]
    assert box.held == []


# --- Ending ---------------------------------------------------------------------------


async def test_the_broadcaster_ends_their_live_stream(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = shown(await calls.start_as_admin(admin, market.id))
    ended_at = clock.advance(timedelta(minutes=1))

    response = await calls.end(admin, live["id"])

    assert shown(response) == live | {
        "status": "ENDED",
        "ended_at": as_json(ended_at),
        "end_reason": "BROADCASTER",
    }
    assert flags(response) == {"is_broadcaster", "can_start_as_admin"}
    row = await stored(container, live["id"])
    assert (row.status, row.ended_at, row.ended_by, row.end_reason) == (
        BroadcastStatus.ENDED,
        ended_at,
        admin.id,
        BroadcastEndReason.BROADCASTER,
    )
    assert box.requests == [
        {"op": "start", "id": live["id"]},
        {"op": "stop", "id": live["id"]},
    ]
    _, end = await audit_rows(container)
    assert (end.actor_id, end.action, end.target) == (
        admin.id,
        "broadcast.end",
        f"broadcast:{live['id']}",
    )
    assert end.before == {"status": "LIVE"}
    assert end.after == {
        "status": "ENDED",
        "market_id": str(market.id),
        "end_reason": "BROADCASTER",
    }


async def test_any_admin_ends_a_live_stream_and_nobody_else_who_is_not_its_broadcaster(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    proposer, member = await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    live = await broadcast(container, market, proposer, BroadcastStatus.LIVE, decided_by=admin)

    as_member = await calls.end(member, live.id)
    still_live = await stored(container, live.id)
    as_other_admin = await calls.end(other_admin, live.id)

    assert refusal(as_member) == NOT_FOUND
    assert still_live.status is BroadcastStatus.LIVE
    assert shown(as_other_admin)["end_reason"] == "ADMIN"
    row = await stored(container, live.id)
    assert (row.status, row.ended_by, row.end_reason) == (
        BroadcastStatus.ENDED,
        other_admin.id,
        BroadcastEndReason.ADMIN,
    )
    (audit,) = await audit_rows(container)
    assert (audit.actor_id, audit.action) == (other_admin.id, "broadcast.end")
    assert audit.after == {"status": "ENDED", "market_id": str(market.id), "end_reason": "ADMIN"}
    assert box.asked("stop") == [{"op": "stop", "id": str(live.id)}]


async def test_a_proposer_who_ends_their_own_live_stream_leaves_no_audit_row(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    live = await broadcast(container, market, proposer, BroadcastStatus.LIVE, decided_by=admin)

    response = await calls.end(proposer, live.id)

    assert shown(response)["end_reason"] == "BROADCASTER"
    assert flags(response) == {"is_broadcaster", "can_request"}, "the proposer asks again"
    assert await audit_rows(container) == []


async def test_ending_a_live_stream_that_has_ended_answers_it_again_and_changes_nothing(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    market = await factory.market()
    live = shown(await calls.start_as_admin(admin, market.id))
    first = await calls.end(admin, live["id"])
    clock.advance(timedelta(minutes=1))

    again = await calls.end(admin, live["id"])
    by_another = await calls.end(other_admin, live["id"])

    assert again.status_code == 200
    assert shown(again) == shown(first)
    assert shown(by_another)["end_reason"] == "BROADCASTER"
    assert (await stored(container, live["id"])).ended_by == admin.id
    assert len(await audit_rows(container)) == 2, "a start and one end"
    assert len(box.asked("stop")) == 1


@pytest.mark.parametrize(
    "status", [BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED, BroadcastStatus.REFUSED]
)
async def test_a_live_stream_that_has_not_started_is_not_ended(
    calls: Calls, container: Container, factory: Factory, status: BroadcastStatus
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, status, decided_by=admin)

    for who in (proposer, admin):
        assert refusal(await calls.end(who, row.id)) == (
            409,
            "CONFLICT",
            NOT_STARTED_MESSAGE,
            {"status": status.value},
        )
    assert (await stored(container, row.id)).status is status


async def test_the_transaction_that_stores_an_end_clears_the_chat_once_it_wrote_the_status(
    container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    ends = RecordedEnds()
    service = service_with(container, on_end=ends)
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE)

    await service.end(factory.identity(admin), live.id)
    await service.end(factory.identity(admin), live.id)

    assert ends.cleared == [live.id], "once, by the end that was stored"
    assert ends.seen == [
        {
            "status": BroadcastStatus.ENDED,
            "end_reason": BroadcastEndReason.BROADCASTER,
            "ended_by": admin.id,
            "ended_at": TEST_NOW,
        }
    ]


async def test_an_end_takes_the_messages_of_the_live_chat_with_it(
    calls: Calls, container: Container, factory: Factory
) -> None:
    """Through the chat the container wires: nothing of a chat is kept once its
    broadcast has ended."""
    admin, member = await factory.admin(), await factory.user()
    market, other_market = await factory.market(), await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE)
    earlier = await broadcast(container, other_market, admin, BroadcastStatus.ENDED)
    async with container.uow_factory() as uow:
        for row, text in ((live, "Hello"), (live, "Is this on?"), (earlier, "Kept by a test")):
            await uow.chat_messages.add(
                ChatMessage(
                    market_id=row.market_id,
                    broadcast_id=row.id,
                    user_id=member.id,
                    text=text,
                    from_vis=False,
                    created_at=TEST_NOW,
                )
            )

    assert (await calls.end(admin, live.id)).status_code == 200

    assert await count_rows(container.uow_factory, ChatMessage) == 1


async def test_an_end_whose_box_is_gone_still_commits(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = shown(await calls.start_as_admin(admin, market.id))
    await box.gone()

    response = await calls.end(admin, live["id"])

    assert shown(response)["status"] == "ENDED"
    assert (await stored(container, live["id"])).status is BroadcastStatus.ENDED


@pytest.mark.parametrize("becomes", [MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.VOID])
async def test_a_live_stream_goes_on_whatever_becomes_of_its_market(
    calls: Calls, container: Container, factory: Factory, becomes: MarketStatus
) -> None:
    """Asking, approving and starting need a market that can have a live stream; a
    broadcast that runs is ended by a person or by the clock, not by its market."""
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(status=becomes, created_by=proposer)
    live = await broadcast(
        container, market, proposer, BroadcastStatus.LIVE, decided_by=admin, on_air=True
    )

    as_member = await calls.read(member, market.id)
    watching = await calls.access(member, live.id)
    ended = await calls.end(proposer, live.id)

    assert shown(as_member)["status"] == "LIVE"
    assert flags(as_member) == {"can_watch"}
    assert watching.status_code == 200
    assert shown(ended)["status"] == "ENDED"
    settled_market = becomes is not MarketStatus.CLOSED
    assert ("can_request" in flags(ended)) is not settled_market


# --- An end the clock brings ----------------------------------------------------------


async def test_a_live_stream_whose_studio_sends_nothing_has_ended_and_the_first_read_stores_it(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market()
    ends = RecordedEnds()
    service_with(container, on_end=ends)
    live = shown(await calls.start_as_admin(admin, market.id))
    stall = timedelta(seconds=container.settings.broadcast.stall_seconds)

    clock.advance(stall - SECOND)
    just_before = await calls.read(member, market.id)
    assert shown(just_before)["status"] == "LIVE"
    assert (await stored(container, live["id"])).status is BroadcastStatus.LIVE

    clock.advance(timedelta(minutes=30))
    async with listening(container.live, factory.identity(member), [broadcast_of(market)]) as heard:
        as_member = await calls.read(member, market.id)
        as_admin = await calls.read(admin, market.id)
        await settled()

    assert as_member.json()["broadcast"] is None, "to a member a broadcast shows while LIVE"
    assert shown(as_admin) == live | {
        "status": "ENDED",
        "ended_at": as_json(TEST_NOW + stall),
        "end_reason": "STALLED",
    }
    row = await stored(container, live["id"])
    assert (row.status, row.ended_at, row.ended_by, row.end_reason) == (
        BroadcastStatus.ENDED,
        TEST_NOW + stall,
        None,
        BroadcastEndReason.STALLED,
    )
    assert ends.cleared == [row.id]
    assert ends.seen[0]["status"] is BroadcastStatus.ENDED
    assert box.asked("stop") == [{"op": "stop", "id": live["id"]}]
    assert heard.bodies == [
        {"type": "broadcast.changed", "market_id": str(market.id), "ts": as_json(clock.now())}
    ], "announced once, by the read that stored the end"
    assert [row.action for row in await audit_rows(container)] == ["broadcast.start"]


async def test_a_live_stream_that_ran_its_time_has_ended(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    limit = timedelta(minutes=container.settings.broadcast.max_minutes)
    clock.advance(limit - SECOND)
    await piece_taken_now(container, live.id)

    assert shown(await calls.read(admin, market.id))["status"] == "LIVE"
    clock.advance(SECOND)
    response = await calls.read(admin, market.id)

    assert shown(response)["status"] == "ENDED"
    assert shown(response)["end_reason"] == "TIME_LIMIT"
    assert shown(response)["ended_at"] == as_json(TEST_NOW + limit)
    assert (shown(response)["on_air"], shown(response)["playlist_url"]) == (False, None)
    assert (await stored(container, live.id)).end_reason is BroadcastEndReason.TIME_LIMIT


async def test_a_start_stores_the_end_of_what_the_clock_ended_and_hands_it_to_the_box(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    """Nobody read the first broadcast since it stalled. The start on another market
    meets it, stores its end, and the box swaps the two without going away."""
    admin, other_admin = await factory.admin(), await factory.admin(1)
    first_market, second_market = await factory.market(), await factory.market()
    first = shown(await calls.start_as_admin(admin, first_market.id))
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    second = shown(await calls.start_as_admin(other_admin, second_market.id))

    assert (await stored(container, first["id"])).end_reason is BroadcastEndReason.STALLED
    assert second["status"] == "LIVE"
    assert box.requests == [
        {"op": "start", "id": first["id"]},
        {"op": "start", "id": second["id"], "stop": [first["id"]]},
    ]
    assert [str(held) for held in box.held] == [second["id"]]


async def test_an_end_the_clock_brought_is_stored_even_when_the_request_that_met_it_is_refused(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE)
    await box.gone()
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    refused = await calls.start_as_admin(admin, market.id)

    assert refusal(refused)[:2] == (503, "SERVICE_UNAVAILABLE")
    rows = await broadcasts_of(container, market)
    assert [(row.id, row.status) for row in rows] == [(live.id, BroadcastStatus.ENDED)]
    assert flags(await calls.read(proposer, market.id)) == {"can_request"}


# --- Asking ---------------------------------------------------------------------------


async def test_the_proposer_of_a_market_asks_for_its_live_stream(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)

    response = await calls.request(proposer, market.id)

    assert response.status_code == 201, response.text
    (row,) = await broadcasts_of(container, market)
    assert response.json() == {
        "broadcast": {
            "id": str(row.id),
            "market_id": str(market.id),
            "status": "REQUESTED",
            "on_air": False,
            "playlist_url": None,
            "requested_at": as_json(TEST_NOW),
            "started_at": None,
            "ended_at": None,
            "end_reason": None,
            "refusal_reason": None,
            "requested_by": None,
        },
        "viewer": NO_FLAG | {"is_broadcaster": True},
    }
    assert (row.status, row.broadcaster_id, row.decided_by, row.decided_at) == (
        BroadcastStatus.REQUESTED,
        proposer.id,
        None,
        None,
    )
    as_admin = await calls.read(admin, market.id)
    assert shown(as_admin)["requested_by"] == {
        "username": proposer.username,
        "email": proposer.email,
    }
    assert flags(as_admin) == {"can_approve", "can_refuse"}
    as_member = await calls.read(member, market.id)
    assert as_member.json() == {"broadcast": None, "viewer": NO_FLAG}
    assert box.requests == []
    assert await audit_rows(container) == []


async def test_a_request_states_that_the_usage_policy_is_accepted(
    calls: Calls, container: Container, factory: Factory
) -> None:
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)

    for body in ({}, {"policy_accepted": False}, {"policy_accepted": True, "more": 1}):
        response = await calls.request(proposer, market.id, body)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")

    assert await all_broadcasts(container) == []


async def test_only_the_proposer_asks_and_an_admin_starts_instead(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    of_an_admin = await factory.market(created_by=admin)

    assert refusal(await calls.request(member, market.id)) == (
        403,
        "FORBIDDEN",
        NOT_THE_PROPOSER_MESSAGE,
        {},
    )
    for own_or_not in (market, of_an_admin):
        assert refusal(await calls.request(admin, own_or_not.id)) == (
            403,
            "FORBIDDEN",
            ADMINS_START_MESSAGE,
            {},
        )
    assert await all_broadcasts(container) == []


async def test_a_person_asks_a_few_times_a_day_and_again_the_next_day(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    limit = container.settings.broadcast.requests_per_user_per_day
    markets = [await factory.market(created_by=proposer) for _ in range(limit + 1)]

    asked = [await calls.request(proposer, market.id) for market in markets]

    assert [response.status_code for response in asked] == [201] * limit + [409]
    assert refusal(asked[-1]) == (
        409,
        "LIMIT_EXCEEDED",
        REQUEST_LIMIT_MESSAGE,
        {"limit": "MAX_BROADCAST_REQUESTS_PER_USER_PER_DAY"},
    )
    assert await broadcasts_of(container, markets[-1]) == []
    # A refusal frees the market for another request, and counts as one all the same.
    await calls.refuse(admin, shown(asked[0])["id"])
    assert refusal(await calls.request(proposer, markets[0].id))[1] == "LIMIT_EXCEEDED"

    # 14:00 in Zurich on the test's day: ten hours later the next day has begun there.
    clock.advance(timedelta(hours=10))
    next_day = await calls.request(proposer, markets[-1].id)

    assert next_day.status_code == 201, next_day.text


async def test_two_requests_for_one_market_at_once_end_with_one_row(
    container: Container, factory: Factory
) -> None:
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    caller = factory.identity(proposer)

    outcomes = await asyncio.gather(
        *(container.broadcasts.request(caller, market.id) for _ in range(4)),
        return_exceptions=True,
    )

    refused = [outcome for outcome in outcomes if isinstance(outcome, AppError)]
    assert [error.message for error in refused] == [OPEN_BROADCAST_MESSAGE] * 3
    assert len(await broadcasts_of(container, market)) == 1


# --- Deciding -------------------------------------------------------------------------


async def test_an_admin_approves_a_request(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    asked = shown(await calls.request(proposer, market.id))
    decided_at = clock.advance(timedelta(minutes=3))

    response = await calls.approve(admin, asked["id"])

    assert shown(response)["status"] == "APPROVED"
    assert flags(response) == {"can_refuse"}
    row = await stored(container, asked["id"])
    assert (row.status, row.decided_by, row.decided_at, row.started_at) == (
        BroadcastStatus.APPROVED,
        admin.id,
        decided_at,
        None,
    )
    as_proposer = await calls.read(proposer, market.id)
    assert flags(as_proposer) == {"is_broadcaster", "can_start"}
    (audit,) = await audit_rows(container)
    assert (audit.actor_id, audit.action, audit.target) == (
        admin.id,
        "broadcast.approve",
        f"broadcast:{row.id}",
    )
    assert audit.before == {"status": "REQUESTED"}
    assert audit.after == {"status": "APPROVED", "market_id": str(market.id)}
    assert box.requests == []


@pytest.mark.parametrize("status", [BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED])
async def test_an_admin_refuses_a_request_or_takes_an_approval_back_with_a_reason(
    calls: Calls, container: Container, factory: Factory, status: BroadcastStatus
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    proposer, member = await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, status, decided_by=other_admin)

    response = await calls.refuse(admin, row.id, "  The camera shows the exam hall.  ")

    assert shown(response)["status"] == "REFUSED"
    assert shown(response)["refusal_reason"] == "The camera shows the exam hall."
    assert flags(response) == {"can_start_as_admin"}
    refused = await stored(container, row.id)
    assert (refused.status, refused.decided_by, refused.refusal_reason) == (
        BroadcastStatus.REFUSED,
        admin.id,
        "The camera shows the exam hall.",
    )
    as_proposer = await calls.read(proposer, market.id)
    assert shown(as_proposer)["refusal_reason"] == "The camera shows the exam hall."
    assert flags(as_proposer) == {"is_broadcaster", "can_request"}
    assert (await calls.read(member, market.id)).json()["broadcast"] is None
    (audit,) = await audit_rows(container)
    assert (audit.actor_id, audit.action) == (admin.id, "broadcast.refuse")
    assert audit.before == {"status": status.value}
    assert audit.after == {
        "status": "REFUSED",
        "market_id": str(market.id),
        "reason": "The camera shows the exam hall.",
    }


async def test_a_refusal_needs_a_reason_that_can_be_stored(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, BroadcastStatus.REQUESTED)

    for reason in ("", "   ", "a\x00b", "x" * 5000):
        response = await calls.refuse(admin, row.id, reason)
        assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR"), reason

    assert (await stored(container, row.id)).status is BroadcastStatus.REQUESTED


@pytest.mark.parametrize(
    ("status", "approval", "refused_with"),
    [
        (BroadcastStatus.APPROVED, DECIDED_MESSAGE, None),
        (BroadcastStatus.REFUSED, DECIDED_MESSAGE, DECIDED_MESSAGE),
        (BroadcastStatus.LIVE, DECIDED_MESSAGE, LIVE_NOT_REFUSED_MESSAGE),
        (BroadcastStatus.ENDED, DECIDED_MESSAGE, DECIDED_MESSAGE),
    ],
    ids=["approved", "refused", "live", "ended"],
)
async def test_what_is_decided_is_not_decided_again(
    calls: Calls,
    container: Container,
    factory: Factory,
    status: BroadcastStatus,
    approval: str,
    refused_with: str | None,
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, status, decided_by=admin)

    assert refusal(await calls.approve(admin, row.id)) == (409, "CONFLICT", approval, {})
    assert (await stored(container, row.id)).status is status
    if refused_with is not None:
        assert refusal(await calls.refuse(admin, row.id)) == (409, "CONFLICT", refused_with, {})
        assert (await stored(container, row.id)).status is status
    assert await audit_rows(container) == []


async def test_a_request_is_approved_while_its_market_can_have_a_live_stream_and_refused_always(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    settled_market = await factory.market(status=MarketStatus.RESOLVED, created_by=proposer)
    row = await broadcast(container, settled_market, proposer, BroadcastStatus.REQUESTED)

    assert flags(await calls.read(admin, settled_market.id)) == {"can_refuse"}
    assert refusal(await calls.approve(admin, row.id)) == (
        409,
        "CONFLICT",
        NOT_STREAMABLE_MESSAGE,
        {},
    )
    assert shown(await calls.refuse(admin, row.id))["status"] == "REFUSED"


async def test_only_an_admin_decides(calls: Calls, container: Container, factory: Factory) -> None:
    proposer = await factory.user()
    barista = await factory.barista()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, BroadcastStatus.REQUESTED)
    plain = factory.identity(proposer)

    for who in (proposer, barista):
        assert refusal(await calls.approve(who, row.id))[:2] == (403, "FORBIDDEN")
        assert refusal(await calls.refuse(who, row.id))[:2] == (403, "FORBIDDEN")
        assert refusal(await calls.admin_list(who))[:2] == (403, "FORBIDDEN")
    with pytest.raises(ForbiddenError):
        await container.broadcasts.approve(plain, row.id)
    with pytest.raises(ForbiddenError):
        await container.broadcasts.refuse(plain, row.id, RefuseBroadcastRequest(reason="No."))
    with pytest.raises(ForbiddenError):
        await container.broadcasts.admin_list(plain)

    assert (await stored(container, row.id)).status is BroadcastStatus.REQUESTED


async def test_a_decision_on_a_broadcast_nobody_has_is_not_found(
    calls: Calls, factory: Factory
) -> None:
    admin = await factory.admin()

    for call in (calls.approve, calls.refuse, calls.start, calls.end):
        assert refusal(await call(admin, uuid7())) == NOT_FOUND


# --- The proposer goes live -----------------------------------------------------------


async def test_the_proposer_goes_live_once_an_admin_approved(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    asked = shown(await calls.request(proposer, market.id))
    await calls.approve(admin, asked["id"])
    started_at = clock.advance(timedelta(minutes=20))

    response = await calls.start(proposer, asked["id"])

    assert response.status_code == 200, response.text
    assert shown(response) == asked | {"status": "LIVE", "started_at": as_json(started_at)}
    assert flags(response) == {"is_broadcaster", "can_end"}
    (row,) = await broadcasts_of(container, market)
    assert (row.status, row.started_at, row.last_chunk_at, row.resumed_from) == (
        BroadcastStatus.LIVE,
        started_at,
        started_at,
        None,
    )
    assert box.requests == [{"op": "start", "id": asked["id"]}]
    assert shown(await calls.read(member, market.id))["status"] == "LIVE"
    assert [audit.action for audit in await audit_rows(container)] == ["broadcast.approve"], (
        "a proposer's start is no admin's action"
    )

    again = await calls.start(proposer, asked["id"])
    assert again.json() == response.json()
    assert len(box.asked("start")) == 1


async def test_a_start_by_a_broadcaster_who_is_an_admin_is_an_admins_action(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Somebody asked as a plain user and holds the role by the time they go live: what
    an admin does to a broadcast is on record, the start and the going on alike."""
    admin, promoted = await factory.admin(), await factory.admin(1)
    market = await factory.market(created_by=promoted)
    approved = await broadcast(
        container, market, promoted, BroadcastStatus.APPROVED, decided_by=admin
    )

    await calls.start(promoted, approved.id)
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    resumed = shown(await calls.start(promoted, approved.id))

    start, going_on = await audit_rows(container)
    assert (start.actor_id, start.action, start.target) == (
        promoted.id,
        "broadcast.start",
        f"broadcast:{approved.id}",
    )
    assert (start.before, start.after) == (
        {"status": "APPROVED"},
        {"status": "LIVE", "market_id": str(market.id)},
    )
    assert (going_on.actor_id, going_on.action, going_on.target) == (
        promoted.id,
        "broadcast.start",
        f"broadcast:{resumed['id']}",
    )
    assert (going_on.before, going_on.after) == (
        {"status": "ENDED"},
        {"status": "LIVE", "market_id": str(market.id), "resumed_from": str(approved.id)},
    )


async def test_nobody_but_its_broadcaster_starts_a_live_stream(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    approved = await broadcast(
        container, market, proposer, BroadcastStatus.APPROVED, decided_by=admin
    )

    assert refusal(await calls.start(admin, approved.id)) == (
        403,
        "FORBIDDEN",
        NOT_THE_BROADCASTER_MESSAGE,
        {},
    )
    assert refusal(await calls.start(member, approved.id)) == NOT_FOUND
    assert (await stored(container, approved.id)).status is BroadcastStatus.APPROVED
    assert box.requests == []


@pytest.mark.parametrize(
    ("status", "answered"),
    [
        (BroadcastStatus.REQUESTED, (409, "CONFLICT", NOT_APPROVED_MESSAGE, {})),
        (BroadcastStatus.REFUSED, (409, "CONFLICT", NOT_APPROVED_MESSAGE, {})),
        (
            BroadcastStatus.ENDED,
            (409, "CONFLICT", ENDED_MESSAGE, {"status": "ENDED", "end_reason": "BROADCASTER"}),
        ),
    ],
    ids=["requested", "refused", "ended-by-its-broadcaster"],
)
async def test_a_live_stream_that_is_not_approved_or_was_ended_by_a_person_does_not_start(
    calls: Calls,
    container: Container,
    factory: Factory,
    box: FakeMediaBox,
    status: BroadcastStatus,
    answered: tuple[int, str, str, dict[str, Any]],
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    row = await broadcast(container, market, proposer, status, decided_by=admin)

    assert refusal(await calls.start(proposer, row.id)) == answered
    assert "can_start" not in flags(await calls.read(proposer, market.id))
    assert len(await broadcasts_of(container, market)) == 1
    assert box.requests == []


async def test_an_approved_live_stream_does_not_start_in_a_market_that_is_settled(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(status=MarketStatus.VOID, created_by=proposer)
    approved = await broadcast(
        container, market, proposer, BroadcastStatus.APPROVED, decided_by=admin
    )

    assert refusal(await calls.start(proposer, approved.id)) == (
        409,
        "CONFLICT",
        NOT_STREAMABLE_MESSAGE,
        {},
    )
    assert flags(await calls.read(proposer, market.id)) == {"is_broadcaster"}
    assert box.requests == []


async def test_the_proposer_waits_while_another_live_stream_is_on(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market, other_market = await factory.market(created_by=proposer), await factory.market()
    approved = await broadcast(
        container, market, proposer, BroadcastStatus.APPROVED, decided_by=admin
    )
    on = shown(await calls.start_as_admin(admin, other_market.id))

    refused = await calls.start(proposer, approved.id)

    assert refusal(refused) == (
        409,
        "LIMIT_EXCEEDED",
        SLOT_TAKEN_MESSAGE,
        {"limit": "MAX_LIVE_BROADCASTS"},
    )
    assert flags(await calls.read(proposer, market.id)) == {"is_broadcaster", "can_start"}
    await calls.end(admin, on["id"])
    assert (await calls.start(proposer, approved.id)).status_code == 200


# --- Going live again after an end the clock brought ----------------------------------


@pytest.mark.parametrize("ends_by", ["STALLED", "TIME_LIMIT"])
async def test_after_an_end_by_itself_the_proposer_goes_live_again_without_asking(
    calls: Calls,
    container: Container,
    factory: Factory,
    box: FakeMediaBox,
    clock: FrozenClock,
    ends_by: str,
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    asked = shown(await calls.request(proposer, market.id))
    await calls.approve(admin, asked["id"])
    decided_at = clock.now()
    clock.advance(timedelta(minutes=1))
    await calls.start(proposer, asked["id"])
    if ends_by == "STALLED":
        clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    else:
        clock.advance(timedelta(minutes=container.settings.broadcast.max_minutes) - SECOND)
        await piece_taken_now(container, UUID(asked["id"]))
        clock.advance(SECOND)
    assert flags(await calls.read(proposer, market.id)) == {
        "is_broadcaster",
        "can_request",
        "can_start",
    }
    resumed_at = clock.advance(timedelta(minutes=2))

    response = await calls.start(proposer, asked["id"])

    assert response.status_code == 200, response.text
    ended, live = await broadcasts_of(container, market)
    assert (ended.id, ended.status, ended.end_reason, ended.ended_by) == (
        ended.id,
        BroadcastStatus.ENDED,
        BroadcastEndReason(ends_by),
        None,
    )
    assert str(ended.id) == asked["id"]
    assert shown(response)["id"] == str(live.id) != asked["id"]
    assert shown(response)["status"] == "LIVE"
    assert flags(response) == {"is_broadcaster", "can_end"}
    assert (live.status, live.broadcaster_id, live.resumed_from) == (
        BroadcastStatus.LIVE,
        proposer.id,
        ended.id,
    )
    assert (live.decided_by, live.decided_at) == (admin.id, decided_at)
    assert live.requested_at == live.started_at == live.last_chunk_at == resumed_at
    assert live.on_air_at is None
    assert box.asked("start")[-1]["id"] == str(live.id)
    assert [str(held) for held in box.held] == [str(live.id)]
    assert shown(await calls.read(member, market.id))["id"] == str(live.id)

    # The answer to that start can get lost: sent again, it answers the same one.
    again = await calls.start(proposer, asked["id"])
    assert again.json() == response.json()
    assert len(await broadcasts_of(container, market)) == 2


async def test_a_live_stream_that_goes_on_does_not_count_as_asked_for(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    limit = container.settings.broadcast.requests_per_user_per_day
    market = await factory.market(created_by=proposer)
    stall = timedelta(seconds=container.settings.broadcast.stall_seconds)
    asked = shown(await calls.request(proposer, market.id))
    await calls.approve(admin, asked["id"])
    current = asked["id"]
    for _ in range(limit + 1):
        current = shown(await calls.start(proposer, current))["id"]
        clock.advance(stall)
    assert len(await broadcasts_of(container, market)) == limit + 1
    await calls.read(proposer, market.id)

    others = [await factory.market(created_by=proposer) for _ in range(limit - 1)]
    asked_again = [await calls.request(proposer, other.id) for other in others]

    assert [response.status_code for response in asked_again] == [201] * (limit - 1)
    assert refusal(await calls.request(proposer, market.id))[1] == "LIMIT_EXCEEDED"


@pytest.mark.parametrize("ended_by", ["BROADCASTER", "ADMIN"])
async def test_after_an_end_by_a_person_the_proposer_asks_again(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, ended_by: str
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    live = await broadcast(container, market, proposer, BroadcastStatus.LIVE, decided_by=admin)
    await calls.end(proposer if ended_by == "BROADCASTER" else admin, live.id)

    refused = await calls.start(proposer, live.id)

    assert refusal(refused) == (
        409,
        "CONFLICT",
        ENDED_MESSAGE,
        {"status": "ENDED", "end_reason": ended_by},
    )
    assert flags(await calls.read(proposer, market.id)) == {"is_broadcaster", "can_request"}
    assert (await calls.request(proposer, market.id)).status_code == 201
    assert box.asked("start") == []


async def test_only_the_latest_live_stream_of_a_market_goes_on(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    """The proposer asked again after the stall: the request is what counts now."""
    admin, proposer = await factory.admin(), await factory.user()
    market = await factory.market(created_by=proposer)
    stalled = await broadcast(
        container,
        market,
        proposer,
        BroadcastStatus.ENDED,
        decided_by=admin,
        end_reason=BroadcastEndReason.STALLED,
    )
    clock.advance(SECOND)
    assert flags(await calls.read(proposer, market.id)) >= {"can_start", "can_request"}
    await calls.request(proposer, market.id)

    refused = await calls.start(proposer, stalled.id)

    assert refusal(refused) == (
        409,
        "CONFLICT",
        ENDED_MESSAGE,
        {"status": "ENDED", "end_reason": "STALLED"},
    )
    assert flags(await calls.read(proposer, market.id)) == {"is_broadcaster"}
    assert box.requests == []


async def test_an_admins_own_live_stream_that_ended_by_itself_is_started_as_another_one(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    first = shown(await calls.start_as_admin(admin, market.id))
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    as_it_stands = await calls.read(admin, market.id)
    resumed = await calls.start(admin, first["id"])
    started = await calls.start_as_admin(admin, market.id)

    assert flags(as_it_stands) == {"is_broadcaster", "can_start_as_admin"}
    assert refusal(resumed) == (
        409,
        "CONFLICT",
        ENDED_MESSAGE,
        {"status": "ENDED", "end_reason": "STALLED"},
    )
    assert started.status_code == 201
    ended, live = await broadcasts_of(container, market)
    assert (ended.status, live.status, live.resumed_from) == (
        BroadcastStatus.ENDED,
        BroadcastStatus.LIVE,
        None,
    )


# --- Told to the open tabs ------------------------------------------------------------


async def test_asking_and_deciding_reach_the_broadcaster_and_the_admins_and_nobody_else(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    hub, of_the_market = container.live, broadcast_of(market)
    changed = {"type": "broadcast.changed", "market_id": str(market.id), "ts": as_json(TEST_NOW)}

    async with (
        listening(hub, factory.identity(proposer), [of_the_market], tab="tab-0001") as own,
        listening(hub, factory.identity(proposer), [MARKETS], tab="tab-0002") as own_lists,
        listening(hub, factory.identity(admin), [of_the_market], tab="tab-0001") as desk,
        listening(hub, factory.identity(admin), [MARKETS], tab="tab-0002") as desk_lists,
        listening(hub, factory.identity(member), [of_the_market, MARKETS]) as others,
    ):
        asked = shown(await calls.request(proposer, market.id))
        await settled()
        assert (own.bodies, desk.bodies, desk_lists.bodies) == ([changed], [changed], [changed])
        assert (own_lists.bodies, others.bodies) == ([], [])

        await calls.approve(admin, asked["id"])
        await settled()
        assert [len(heard.bodies) for heard in (own, desk, desk_lists)] == [2, 2, 2]

        await calls.refuse(admin, asked["id"])
        await settled()
        assert [len(heard.bodies) for heard in (own, desk, desk_lists)] == [3, 3, 3]
        assert (own_lists.bodies, others.bodies) == ([], [])


async def test_going_live_and_ending_reach_everybody_inside_the_market(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market, other_market = await factory.market(created_by=proposer), await factory.market()
    approved = await broadcast(
        container, market, proposer, BroadcastStatus.APPROVED, decided_by=admin
    )
    hub = container.live
    changed = {"type": "broadcast.changed", "market_id": str(market.id), "ts": as_json(TEST_NOW)}

    async with (
        listening(hub, factory.identity(member), [broadcast_of(market)], tab="tab-0001") as tab,
        listening(hub, factory.identity(member), [MARKETS], tab="tab-0002") as lists,
        listening(
            hub, factory.identity(member), [broadcast_of(other_market)], tab="tab-0003"
        ) as elsewhere,
        listening(hub, factory.identity(admin), [broadcast_of(market)]) as desk,
    ):
        await calls.start(proposer, approved.id)
        await settled()
        assert (tab.bodies, lists.bodies, desk.bodies) == ([changed], [changed], [changed])

        await calls.end(proposer, approved.id)
        await settled()
        assert (tab.bodies, lists.bodies, desk.bodies) == ([changed] * 2,) * 3

        await calls.start_as_admin(admin, market.id)
        await settled()
        assert (tab.bodies, lists.bodies, desk.bodies) == ([changed] * 3,) * 3
        assert elsewhere.bodies == []


async def test_a_refused_request_announces_nothing(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market(status=MarketStatus.RESOLVED)

    async with listening(
        container.live, factory.identity(admin), [broadcast_of(market), MARKETS]
    ) as heard:
        await calls.start_as_admin(admin, market.id)
        await calls.request(member, market.id)
        await settled()

    assert heard.bodies == []


# --- Transactions and locks -----------------------------------------------------------


def locks_of(statements: list[str]) -> list[str]:
    """The statements that take a lock, in the order they were sent, each by its kind."""
    kinds = []
    for statement in statements:
        if "pg_advisory_xact_lock" in statement:
            kinds.append("advisory")
        elif takes_a_row_lock(statement):
            table = statement.split(" FROM ")[1].split()[0]
            kinds.append(f"row of {table}")
    return kinds


async def test_a_write_takes_the_lock_of_the_lifecycle_then_its_row_and_no_market_or_account(
    container: Container, factory: Factory
) -> None:
    """The order every write of a broadcast's life keeps, so that no two of them wait
    for each other, and no trade waits for a broadcast."""
    admin, proposer = await factory.admin(), await factory.user()
    market, other_market = await factory.market(created_by=proposer), await factory.market()
    as_admin, as_proposer = factory.identity(admin), factory.identity(proposer)
    service = container.broadcasts
    taken: dict[str, list[str]] = {}

    with recorded_statements(container) as statements:
        asked = await service.request(as_proposer, market.id)
    taken["request"] = locks_of(statements)
    assert asked.broadcast is not None
    with recorded_statements(container) as statements:
        await service.approve(as_admin, asked.broadcast.id)
    taken["approve"] = locks_of(statements)
    with recorded_statements(container) as statements:
        await service.start(as_proposer, asked.broadcast.id)
    taken["start"] = locks_of(statements)
    with recorded_statements(container) as statements:
        await service.end(as_admin, asked.broadcast.id)
    taken["end"] = locks_of(statements)
    with recorded_statements(container) as statements:
        started = await service.start_as_admin(as_admin, other_market.id)
    taken["start_as_admin"] = locks_of(statements)
    assert started.broadcast is not None
    await service.end(as_admin, started.broadcast.id)
    refused = await broadcast(container, other_market, proposer, BroadcastStatus.REQUESTED)
    with recorded_statements(container) as statements:
        await service.refuse(as_admin, refused.id, RefuseBroadcastRequest(reason="No."))
    taken["refuse"] = locks_of(statements)

    assert taken == {
        "request": ["advisory"],
        "approve": ["advisory", "row of broadcasts"],
        "start": ["advisory", "row of broadcasts"],
        # The third is the chat's own lock, taken once the status is written.
        "end": ["advisory", "row of broadcasts", "advisory"],
        "start_as_admin": ["advisory"],
        "refuse": ["advisory", "row of broadcasts"],
    }


async def test_an_end_the_clock_brought_is_stored_under_the_same_locks(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    await broadcast(container, market, admin, BroadcastStatus.LIVE)
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    with recorded_statements(container) as statements:
        await container.broadcasts.for_market(factory.identity(admin), market.id)

    assert locks_of(statements) == ["advisory", "row of broadcasts", "advisory"]
    written = [statement.split()[:2] for statement in statements if writes(statement)]
    assert written == [["UPDATE", "broadcasts"], ["DELETE", "FROM"]]


async def test_a_read_locks_nothing_and_writes_nothing(
    container: Container, factory: Factory
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    service = container.broadcasts

    with recorded_statements(container) as statements:
        await service.for_market(factory.identity(member), market.id)
        await service.for_market(factory.identity(admin), market.id)
        await service.live(factory.identity(member))
        await service.admin_list(factory.identity(admin))
        await service.admit(factory.identity(member), live.id)
        await service.admit_upload(factory.identity(admin), live.id)

    assert locks_of(statements) == []
    assert not any(writes(statement) for statement in statements)


async def test_every_operation_is_one_transaction_and_answers_from_inside_it(
    container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    """Nothing is read once a write has committed: a start that took effect is never
    answered as one that failed. And what a viewer costs is one transaction a question."""
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    market = await factory.market(created_by=proposer)
    units = CountedUnitsOfWork(container.uow_factory)
    service = service_with(container, on_end=RecordedEnds(), uow_factory=units)
    as_admin, as_proposer = factory.identity(admin), factory.identity(proposer)
    opened: dict[str, int] = {}

    async def counted(name: str, operation: Awaitable[object]) -> Any:
        before = units.opened
        answer = await operation
        opened[name] = units.opened - before
        return answer

    asked = await counted("request", service.request(as_proposer, market.id))
    row_id = asked.broadcast.id
    await counted("approve", service.approve(as_admin, row_id))
    await counted("start", service.start(as_proposer, row_id))
    # A recorder hands its first piece over a moment after the start.
    clock.advance(SECOND)
    await counted("the first piece", service.take_chunk(as_proposer, row_id, PART, 0, webm()))
    await counted("a later piece", service.take_chunk(as_proposer, row_id, PART, 1, later()))
    await counted("on air", service.take_chunk(as_proposer, row_id, PART, 2, later()))
    await counted("read", service.for_market(factory.identity(member), market.id))
    await counted("live now", service.live(factory.identity(member)))
    await counted("the admin's list", service.admin_list(as_admin))
    await counted("the proxy's question", service.admit(factory.identity(member), row_id))
    await counted("end", service.end(as_proposer, row_id))
    await counted("start as an admin", service.start_as_admin(as_admin, market.id))

    assert opened == {
        "request": 1,
        "approve": 1,
        "start": 1,
        # The caller and the broadcast are judged, then the sign of life is written.
        "the first piece": 2,
        "a later piece": 1,
        "on air": 2,
        "read": 1,
        "live now": 1,
        "the admin's list": 1,
        "the proxy's question": 1,
        "end": 1,
        "start as an admin": 1,
    }


# --- No token moves -------------------------------------------------------------------


async def test_a_whole_life_of_a_live_stream_moves_no_token(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    proposer = await factory.user(balance_micro=5_000_000)
    market = await factory.market(created_by=proposer)
    entries_before = await count_rows(container.uow_factory, JournalEntry)

    asked = shown(await calls.request(proposer, market.id))
    await calls.refuse(admin, asked["id"])
    asked = shown(await calls.request(proposer, market.id))
    await calls.approve(admin, asked["id"])
    await calls.start(proposer, asked["id"])
    for seq, data in enumerate([webm(), later(), later()]):
        assert (await calls.chunk(proposer, asked["id"], seq, data)).status_code == 200
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    live = shown(await calls.start(proposer, asked["id"]))
    await calls.end(admin, live["id"])
    await calls.start_as_admin(admin, market.id)

    assert [row.status.value for row in await broadcasts_of(container, market)] == [
        "REFUSED",
        "ENDED",
        "ENDED",
        "LIVE",
    ]
    assert await count_rows(container.uow_factory, JournalEntry) == entries_before
    assert await factory.balance_micro(proposer) == 5_000_000
    await assert_books_balance(container.uow_factory)


async def test_no_answer_says_how_many_people_watch(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market()
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    await calls.access(member, live.id)

    answers = [
        await calls.read(admin, market.id),
        await calls.read(member, market.id),
        await calls.live(member),
        await calls.admin_list(admin),
        await calls.chunk(admin, live.id, 0, webm()),
    ]

    for answer in answers:
        assert answer.status_code == 200, answer.text
        assert "viewers" not in answer.text
        assert "count" not in answer.text


async def test_a_role_that_is_named_for_a_guard_is_judged_by_the_service_as_well(
    container: Container, factory: Factory
) -> None:
    """An admin by the row alone is an admin; a suspended one is none."""
    by_the_row = await factory.user(role=UserRole.ADMIN)
    market = await factory.market()

    answer = await container.broadcasts.start_as_admin(factory.identity(by_the_row), market.id)

    assert answer.broadcast is not None
    assert answer.broadcast.status is BroadcastStatus.LIVE
