"""The three operations of the live chat, through the application (SPEC §6, §10, A.2).

    GET  /api/markets/{market_id}/chat                               whoever is inside the market
    POST /api/markets/{market_id}/chat                               whoever is inside the market
    POST /api/admin/markets/{market_id}/chat/{message_id}/remove     an admin

Each test sends requests as a browser behind the portal does and reads the status, the
body and the one error shape `{code, message, details}`. The refusals of one operation
are checked in the order its checks run. Which broadcast is on says the lookup of
`tests/support/chat.py`; a pushed event is read from a stream of the container's hub.
"""

from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest

from app.api.validation import sentence_of
from app.container import Container
from app.core.enums import MarketStatus
from app.core.errors import ForbiddenError, NotFoundError, RateLimitedError
from app.core.text import CHAT_MESSAGE_STORED_MAX_LENGTH
from app.schemas.chat import RemovedResponse
from app.services.chat import (
    CHAT_BUSY_MESSAGE,
    CHAT_CLOSED_MESSAGE,
    EMPTY_TEXT_MESSAGE,
    TEXT_FIELD,
    ChatService,
    too_long_message,
)
from tests.support.chat import (
    audit_rows,
    chat_of,
    go_live,
    listening,
    of,
    said,
    stored_messages,
)
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

ERROR_KEYS: Final[frozenset[str]] = frozenset({"code", "message", "details"})
CLOSED: Final[dict[str, Any]] = {
    "open": False,
    "broadcast_id": None,
    "can_remove": False,
    "items": [],
}
"""What a read answers while the market has no broadcast that is LIVE."""


def chat_path(market_id: UUID) -> str:
    return f"/api/markets/{market_id}/chat"


def remove_path(market_id: UUID, message_id: UUID) -> str:
    return f"/api/admin/markets/{market_id}/chat/{message_id}/remove"


def assert_error(
    response: httpx.Response, status_code: int, code: str, message: str
) -> dict[str, Any]:
    """The response is that error in the one error shape; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert (body["code"], body["message"]) == (code, message)
    details: dict[str, Any] = body["details"]
    return details


@pytest.fixture(autouse=True)
def chat(container: Container) -> ChatService:
    """The chat the routes of `app` reach in every test here, asking the lookup of the
    tests which broadcast is on."""
    return chat_of(container)


async def test_a_market_without_a_live_broadcast_reads_closed_and_refuses_a_message_with_409(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    market = await factory.market()
    as_user(await factory.user())

    read = await client.get(chat_path(market.id))
    written = await client.post(chat_path(market.id), json={"text": "Is anybody here?"})

    assert (read.status_code, read.json()) == (200, CLOSED)
    assert assert_error(written, 409, "CONFLICT", CHAT_CLOSED_MESSAGE) == {}
    assert await stored_messages(container) == []
    as_user(await factory.admin())
    as_an_admin = await client.get(chat_path(market.id))
    assert (as_an_admin.status_code, as_an_admin.json()) == (200, CLOSED | {"can_remove": True})


async def test_a_message_is_answered_201_with_its_item_and_everybody_inside_reads_it(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    market = await factory.market()
    broadcast = await go_live(container, market)
    alice, bob, admin = await factory.user(), await factory.user(), await factory.admin()

    as_user(alice)
    written = await client.post(chat_path(market.id), json={"text": "  Kick-off!\n​ "})
    as_user(admin)
    from_vis = await client.post(chat_path(market.id), json={"text": "Welcome from VIS."})

    assert written.status_code == from_vis.status_code == 201, written.text
    [first, second] = await stored_messages(container)
    assert written.json() == {
        "id": str(first.id),
        "username": alice.username,
        "text": "Kick-off!",
        "from_vis": False,
    }
    assert from_vis.json() == {
        "id": str(second.id),
        "username": admin.username,
        "text": "Welcome from VIS.",
        "from_vis": True,
    }
    for reader, can_remove in ((alice, False), (bob, False), (admin, True)):
        as_user(reader)
        read = await client.get(chat_path(market.id))
        assert (read.status_code, read.json()) == (
            200,
            {
                "open": True,
                "broadcast_id": str(broadcast.id),
                "can_remove": can_remove,
                "items": [written.json(), from_vis.json()],
            },
        )


async def test_the_message_that_is_pushed_is_the_body_the_post_answered(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    market = await factory.market()
    broadcast = await go_live(container, market)
    watcher = factory.identity(await factory.user())
    as_user(await factory.user())

    async with listening(container.live, watcher, of(market)) as heard:
        written = await client.post(chat_path(market.id), json={"text": "Did you see that?"})
        pushed = await heard.next()
        await heard.nothing_more()

    assert written.status_code == 201, written.text
    assert pushed["message"] == written.json()
    assert (pushed["type"], pushed["market_id"], pushed["broadcast_id"]) == (
        "chat.message",
        str(market.id),
        str(broadcast.id),
    )


async def test_a_text_that_is_no_message_is_answered_422_on_the_field_text(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """The request model takes what the database takes; the service cleans the text
    and holds it to `CHAT_MESSAGE_MAX_LENGTH`. Whoever refuses, the sentence is the
    same and the field is named."""
    limit = container.settings.chat.message_max_length
    market = await factory.market()
    await go_live(container, market)
    as_user(await factory.user())

    # Refused by the service, once the text is cleaned.
    for text, sentence in (
        ("​‮", EMPTY_TEXT_MESSAGE),
        ("x" * (limit + 1), too_long_message(limit)),
    ):
        response = await client.post(chat_path(market.id), json={"text": text})
        details = assert_error(response, 422, "VALIDATION_ERROR", sentence)
        assert details == {"field": TEXT_FIELD}

    # Refused by the request model, before the service is asked.
    for body, kind, sentence in (
        ({"text": "   "}, "string_too_short", EMPTY_TEXT_MESSAGE),
        (
            {"text": "x" * (CHAT_MESSAGE_STORED_MAX_LENGTH + 1)},
            "string_too_long",
            too_long_message(CHAT_MESSAGE_STORED_MAX_LENGTH),
        ),
        ({}, "missing", sentence_of({"type": "missing"})),
    ):
        response = await client.post(chat_path(market.id), json=body)
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert response.json()["details"]["errors"] == [
            {"loc": ["body", TEXT_FIELD], "msg": sentence, "type": kind}
        ]
    unknown_field = await client.post(chat_path(market.id), json={"text": "Hi", "from_vis": True})
    assert unknown_field.status_code == 422
    assert unknown_field.json()["details"]["errors"][0]["loc"] == ["body", "from_vis"]

    at_the_limit = await client.post(chat_path(market.id), json={"text": "x" * limit})
    assert at_the_limit.status_code == 201, at_the_limit.text
    assert [row.text for row in await stored_messages(container)] == ["x" * limit]


async def test_the_message_beyond_a_rate_is_answered_429_with_the_seconds_to_wait(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    limits = container.settings.chat
    market = await factory.market()
    await go_live(container, market)
    alice = await factory.user()
    as_user(alice)

    for number in range(limits.message_limit_per_user):
        accepted = await client.post(chat_path(market.id), json={"text": f"Message {number}"})
        assert accepted.status_code == 201, accepted.text
    too_many = await client.post(chat_path(market.id), json={"text": "One too many."})

    details = assert_error(too_many, 429, "RATE_LIMITED", RateLimitedError.default_message)
    assert set(details) == {"retry_after_seconds"}
    wait = details["retry_after_seconds"]
    assert 1 <= wait <= limits.message_limit_per_user_window_seconds
    assert too_many.headers["Retry-After"] == str(wait)

    # The chat's own allowance, used up by others: the sentence says that it is the chat.
    sent = limits.message_limit_per_user
    while sent < limits.room_message_limit:
        as_user(await factory.user())
        for _ in range(min(limits.message_limit_per_user, limits.room_message_limit - sent)):
            accepted = await client.post(chat_path(market.id), json={"text": "Goal!"})
            assert accepted.status_code == 201, accepted.text
            sent += 1
    as_user(await factory.user())
    busy = await client.post(chat_path(market.id), json={"text": "What did I miss?"})

    details = assert_error(busy, 429, "RATE_LIMITED", CHAT_BUSY_MESSAGE)
    assert set(details) == {"retry_after_seconds"}
    assert 1 <= details["retry_after_seconds"] <= limits.room_message_limit_window_seconds
    assert busy.headers["Retry-After"] == str(details["retry_after_seconds"])
    assert len(await stored_messages(container)) == limits.room_message_limit


@pytest.mark.parametrize("status", [None, MarketStatus.PROPOSED, MarketStatus.REJECTED])
async def test_a_market_that_was_never_opened_is_answered_404_by_the_three_operations(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    status: MarketStatus | None,
) -> None:
    """An id no market has and a proposal get the same answer, the proposal's own
    proposer and an admin included."""
    proposer = await factory.user()
    admin = await factory.admin()
    market_id = uuid7()
    if status is not None:
        market_id = (await factory.market(status=status, created_by=proposer)).id
    not_found = NotFoundError.default_message

    for caller in (proposer, admin):
        as_user(caller)
        read = await client.get(chat_path(market_id))
        written = await client.post(chat_path(market_id), json={"text": "Hello?"})
        assert assert_error(read, 404, "NOT_FOUND", not_found) == {}
        assert assert_error(written, 404, "NOT_FOUND", not_found) == {}
    removed = await client.post(remove_path(market_id, uuid7()))
    assert assert_error(removed, 404, "NOT_FOUND", not_found) == {}


async def test_the_refusals_of_a_message_come_in_the_order_of_its_checks(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    """The text, then the user's rate, then the market, then the chat: an answer names
    the first that fails."""
    limits = container.settings.chat
    nowhere = chat_path(uuid7())
    closed = chat_path((await factory.market()).id)
    as_user(await factory.user())

    # The text before the market: a text that is no message on a market that is not there.
    unreadable = await client.post(nowhere, json={"text": "​"})
    assert_error(unreadable, 422, "VALIDATION_ERROR", EMPTY_TEXT_MESSAGE)
    # The market before the chat: 404 for the id no market has, 409 for the closed chat.
    assert_error(
        await client.post(nowhere, json={"text": "Hello?"}),
        404,
        "NOT_FOUND",
        NotFoundError.default_message,
    )
    assert_error(
        await client.post(closed, json={"text": "Hello?"}), 409, "CONFLICT", CHAT_CLOSED_MESSAGE
    )
    # The user's rate before both: each message counts, wherever it was sent to.
    for _ in range(limits.message_limit_per_user - 2):
        await client.post(closed, json={"text": "Hello?"})
    for path in (nowhere, closed):
        assert_error(
            await client.post(path, json={"text": "Hello?"}),
            429,
            "RATE_LIMITED",
            RateLimitedError.default_message,
        )
    # And still the text first.
    assert_error(
        await client.post(closed, json={"text": "​"}), 422, "VALIDATION_ERROR", EMPTY_TEXT_MESSAGE
    )


async def test_an_admin_removes_a_message_and_everybody_stops_reading_it(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    chat: ChatService,
    clock: FrozenClock,
) -> None:
    market = await factory.market()
    broadcast = await go_live(container, market)
    author, admin = await factory.user(), await factory.admin()
    kept = await chat.post(factory.identity(author), market.id, said("Fair play."))
    gone = await chat.post(factory.identity(author), market.id, said("Something rude."))
    watcher = factory.identity(await factory.user())

    as_user(admin)
    async with listening(container.live, watcher, of(market)) as heard:
        removed = await client.post(remove_path(market.id, gone.id))
        pushed = await heard.next()
        await heard.nothing_more()

    assert removed.status_code == 200, removed.text
    assert set(removed.json()) == {"id", "removed_at"}
    assert RemovedResponse.model_validate(removed.json()) == RemovedResponse(
        id=gone.id, removed_at=clock.now()
    )
    assert {key: value for key, value in pushed.items() if key != "ts"} == {
        "type": "chat.removed",
        "market_id": str(market.id),
        "broadcast_id": str(broadcast.id),
        "message_id": str(gone.id),
    }
    as_user(author)
    read = await client.get(chat_path(market.id))
    assert read.json()["items"] == [kept.model_dump(mode="json")]
    [audit] = await audit_rows(container)
    assert (audit.actor_id, audit.target) == (admin.id, f"chat_message:{gone.id}")
    assert audit.before is not None
    assert (audit.before["text"], audit.before["user_id"]) == ("Something rude.", str(author.id))

    # A second removal, an id no message has, and the message under another market.
    as_user(admin)
    elsewhere = await factory.market()
    for path in (
        remove_path(market.id, gone.id),
        remove_path(market.id, uuid7()),
        remove_path(elsewhere.id, kept.id),
    ):
        again = await client.post(path)
        assert assert_error(again, 404, "NOT_FOUND", NotFoundError.default_message) == {}
    assert [row.id for row in await stored_messages(container)] == [kept.id]
    assert len(await audit_rows(container)) == 1


async def test_nobody_but_an_admin_reaches_the_removal(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    chat: ChatService,
) -> None:
    """The guard answers before the market or the message is looked at: a message that
    is there and ids nothing has get the same 403."""
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    await go_live(container, market)
    author = await factory.user()
    item = await chat.post(factory.identity(author), market.id, said("Mine to keep."))

    for caller in (author, proposer, await factory.barista()):
        as_user(caller)
        for path in (remove_path(market.id, item.id), remove_path(uuid7(), uuid7())):
            refused = await client.post(path)
            assert assert_error(refused, 403, "FORBIDDEN", ForbiddenError.default_message) == {}

    assert [row.id for row in await stored_messages(container)] == [item.id]
    assert await audit_rows(container) == []
