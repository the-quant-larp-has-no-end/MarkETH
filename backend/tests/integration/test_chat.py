"""The live chat beside a market's live stream, at its service (SPEC §6).

The chat of a market is open exactly while the market has a broadcast that is LIVE.
Whoever is inside the market reads it and writes in it; nothing of it is kept after its
broadcast. These tests call `ChatService` as a route calls it, on the real Postgres and
Redis, with the lookup of `tests/support/chat.py` saying which broadcast is on. What is
pushed is read from a stream of the container's hub.

Sentences and limits are the service's constants and the settings; none is typed again.
"""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select, text

from app.api.validation import sentence_of
from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import BroadcastStatus, MarketStatus, UserRole
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.text import clean_line
from app.models import JournalEntry, Posting
from app.models.broadcast import Broadcast
from app.models.chat import ChatMessage
from app.schemas.chat import ChatMessageItem, ChatResponse, RemovedResponse
from app.schemas.live import BroadcastChangedEvent, ChatMessageEvent, ChatRemovedEvent
from app.services.audit import AuditAction
from app.services.chat import (
    CHAT_BUSY_MESSAGE,
    CHAT_CLOSED_MESSAGE,
    EMPTY_TEXT_MESSAGE,
    TEXT_FIELD,
    ChatService,
    chat_lock,
    too_long_message,
)
from tests.support.books import assert_books_balance
from tests.support.chat import (
    WAIT_SECONDS,
    audit_rows,
    broadcast_of,
    chat_of,
    end,
    go_live,
    listening,
    of,
    private_market,
    said,
    store_end,
    stored_messages,
    write_messages,
)
from tests.support.clock import FrozenClock
from tests.support.factories import DEFAULT_DEGREE_PROGRAMME, Factory
from tests.support.statements import recorded_statements, takes_a_row_lock, writes

pytestmark = pytest.mark.integration

PROGRAMME = CATALOGUE[0].programmes[0]
OTHER_PROGRAMME = CATALOGUE[0].programmes[1]


@pytest.fixture
def chat(container: Container) -> ChatService:
    return chat_of(container)


async def fill_the_chat(
    container: Container,
    factory: Factory,
    chat: ChatService,
    market_id: UUID,
    count: int,
    *,
    programme: str = DEFAULT_DEGREE_PROGRAMME,
) -> None:
    """Have `count` messages sent to the chat of a market, by as many users of
    `programme` as the rate of one user makes necessary."""
    per_user = container.settings.chat.message_limit_per_user
    sent = 0
    while sent < count:
        writer = factory.identity(await factory.user(degree_programme=programme))
        for _ in range(min(per_user, count - sent)):
            await chat.post(writer, market_id, said("Goal!"))
            sent += 1


async def someone_waits_for_an_advisory_lock(container: Container) -> None:
    """Return once a transaction waits for an advisory lock another one holds."""
    waiting = text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
    for _ in range(int(WAIT_SECONDS / 0.01)):
        async with container.uow_factory() as uow:
            if await uow.session.scalar(waiting):
                return
        await asyncio.sleep(0.01)
    pytest.fail("no transaction waits for an advisory lock")


# --- Open exactly while a broadcast is LIVE (C1, C7) --------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        None,
        BroadcastStatus.REQUESTED,
        BroadcastStatus.APPROVED,
        BroadcastStatus.REFUSED,
        BroadcastStatus.ENDED,
    ],
)
async def test_without_a_broadcast_that_is_live_the_chat_is_closed_and_takes_no_message(
    container: Container, factory: Factory, chat: ChatService, status: BroadcastStatus | None
) -> None:
    market = await factory.market()
    if status is not None:
        await go_live(container, market, status=status)
    member = factory.identity(await factory.user())
    admin = factory.identity(await factory.admin())

    assert await chat.read(member, market.id) == ChatResponse(
        open=False, broadcast_id=None, can_remove=False, items=[]
    )
    # What an admin may do is the admin's, whether or not the chat is open.
    assert await chat.read(admin, market.id) == ChatResponse(
        open=False, broadcast_id=None, can_remove=True, items=[]
    )
    for caller in (member, admin):
        with pytest.raises(ConflictError) as refused:
            await chat.post(caller, market.id, said("Is anybody here?"))
        assert refused.value.message == CHAT_CLOSED_MESSAGE
        assert refused.value.details == {}
    assert await stored_messages(container) == []


@pytest.mark.parametrize(
    "status", [MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED, MarketStatus.RESOLVED]
)
async def test_the_chat_is_open_while_a_broadcast_is_live_whatever_the_status_of_the_market(
    container: Container, factory: Factory, chat: ChatService, status: MarketStatus
) -> None:
    """The chat asks for a broadcast that is LIVE and for nothing of the market's
    status: which markets can have a broadcast is the broadcast's rule."""
    market = await factory.market(status=status)
    broadcast = await go_live(container, market)
    member = factory.identity(await factory.user())

    item = await chat.post(member, market.id, said("Good evening."))

    assert await chat.read(member, market.id) == ChatResponse(
        open=True, broadcast_id=broadcast.id, can_remove=False, items=[item]
    )


# --- Reading and writing (C2, C6) --------------------------------------------------------


async def test_whoever_is_inside_the_market_reads_what_the_others_wrote_earliest_first(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    broadcast = await go_live(container, market)
    alice, bob, carol = await factory.user(), await factory.user(), await factory.user()

    first = await chat.post(factory.identity(alice), market.id, said("Kick-off!"))
    second = await chat.post(factory.identity(bob), market.id, said("Here we go."))
    third = await chat.post(factory.identity(proposer), market.id, said("Welcome, everybody."))

    assert first == ChatMessageItem(
        id=first.id, username=alice.username, role=UserRole.USER, text="Kick-off!", from_vis=False
    )
    assert [second.username, third.username] == [bob.username, proposer.username]
    assert first.id < second.id < third.id
    for reader in (alice, bob, carol, proposer):
        assert await chat.read(factory.identity(reader), market.id) == ChatResponse(
            open=True, broadcast_id=broadcast.id, can_remove=False, items=[first, second, third]
        )
    now = container.clock.now()
    assert [
        (row.id, row.market_id, row.broadcast_id, row.user_id, row.text, row.created_at)
        for row in await stored_messages(container)
    ] == [
        (first.id, market.id, broadcast.id, alice.id, "Kick-off!", now),
        (second.id, market.id, broadcast.id, bob.id, "Here we go.", now),
        (third.id, market.id, broadcast.id, proposer.id, "Welcome, everybody.", now),
    ]


async def test_a_read_holds_the_latest_messages_up_to_the_backlog(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    backlog = container.settings.chat.backlog_size
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = await factory.user()
    rows = await write_messages(
        container, broadcast, author, [f"Message {number}" for number in range(backlog + 5)]
    )

    read = await chat.read(factory.identity(author), market.id)

    assert len(read.items) == backlog
    assert [item.id for item in read.items] == [row.id for row in rows[5:]]
    assert read.items[-1] == ChatMessageItem(
        id=rows[-1].id,
        username=author.username,
        role=UserRole.USER,
        text=rows[-1].text,
        from_vis=False,
    )


async def test_a_message_names_its_author_with_the_current_username_and_role(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market = await factory.market()
    await go_live(container, market)
    author = await factory.user()
    await chat.post(factory.identity(author), market.id, said("Under my first name."))

    async with container.uow_factory() as uow:
        row = await uow.users.get(author.id, for_update=True)
        assert row is not None
        row.username = "renamed"
        row.role = UserRole.BARISTA

    [item] = (await chat.read(factory.identity(await factory.user()), market.id)).items
    assert item.username == "renamed"
    assert item.role is UserRole.BARISTA


async def test_messages_carry_effective_roles_including_bootstrap_admins_and_baristas(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """`from_vis` is the author's role in effect when the message is stored: a
    bootstrap admin by the address, another admin by the row. It is kept with the
    row."""
    market = await factory.market()
    await go_live(container, market)
    by_address = await factory.admin()
    by_row = await factory.user(role=UserRole.ADMIN)
    barista = await factory.barista()
    member = await factory.user()

    # A bootstrap admin remains an admin even when the stored role says USER.
    assert by_address.role is UserRole.USER

    items = [
        await chat.post(factory.identity(author), market.id, said("Hello."))
        for author in (by_address, by_row, barista, member)
    ]

    assert [item.role for item in items] == [
        UserRole.ADMIN,
        UserRole.ADMIN,
        UserRole.BARISTA,
        UserRole.USER,
    ]
    assert [item.from_vis for item in items] == [True, True, False, False]
    assert [row.from_vis for row in await stored_messages(container)] == [True, True, False, False]
    read = await chat.read(factory.identity(member), market.id)
    assert read.items == items
    assert not read.can_remove
    assert (await chat.read(factory.identity(by_address), market.id)).can_remove


# --- The text (C3, T1 to T4) -------------------------------------------------------------


async def test_a_message_is_stored_as_one_clean_line(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market = await factory.market()
    await go_live(container, market)
    member = factory.identity(await factory.user())
    raw = "  Great\tgoal,\n​what a‮ game  "
    markup = "<script>alert(1)</script> & https://example.org \U0001f389"

    cleaned = await chat.post(member, market.id, said(raw))
    kept = await chat.post(member, market.id, said(markup))

    assert cleaned.text == clean_line(raw) == "Great goal, what a game"
    assert kept.text == markup
    assert [row.text for row in await stored_messages(container)] == [cleaned.text, markup]
    assert (await chat.read(member, market.id)).items == [cleaned, kept]


async def test_a_message_is_measured_once_it_is_cleaned(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    limit = container.settings.chat.message_max_length
    market = await factory.market()
    await go_live(container, market)
    member = factory.identity(await factory.user())

    at_the_limit = await chat.post(member, market.id, said("​" + "x" * limit + "​"))
    assert at_the_limit.text == "x" * limit

    for body, sentence in [
        ("x" * (limit + 1), too_long_message(limit)),
        ("​‮", EMPTY_TEXT_MESSAGE),
    ]:
        with pytest.raises(ValidationFailedError) as refused:
            await chat.post(member, market.id, said(body))
        assert refused.value.message == sentence
        assert refused.value.details == {"field": TEXT_FIELD}
    assert len(await stored_messages(container)) == 1


async def test_a_length_reads_the_same_whoever_checks_it(base_settings: Settings) -> None:
    """The service words its two refusals as the table of `app/api/validation.py`
    words what a request model refuses."""
    for limit in (1, base_settings.chat.message_max_length):
        assert too_long_message(limit) == sentence_of(
            {"type": "string_too_long", "ctx": {"max_length": limit}}
        )
    assert sentence_of({"type": "string_too_short", "ctx": {"min_length": 1}}) == EMPTY_TEXT_MESSAGE


# --- The two rates (C4, C5) --------------------------------------------------------------


async def test_the_message_beyond_the_users_rate_is_refused_and_a_refused_text_does_not_count(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    limits = container.settings.chat
    market = await factory.market()
    await go_live(container, market)
    alice = factory.identity(await factory.user())
    bob = factory.identity(await factory.user())

    for _ in range(limits.message_limit_per_user):
        with pytest.raises(ValidationFailedError):
            await chat.post(alice, market.id, said("​"))
    for number in range(limits.message_limit_per_user):
        await chat.post(alice, market.id, said(f"Message {number}"))
    with pytest.raises(RateLimitedError) as refused:
        await chat.post(alice, market.id, said("One too many."))

    assert refused.value.message == RateLimitedError.default_message
    retry_after = refused.value.details["retry_after_seconds"]
    assert 1 <= retry_after <= limits.message_limit_per_user_window_seconds
    await chat.post(bob, market.id, said("Somebody else writes on."))
    assert len(await stored_messages(container)) == limits.message_limit_per_user + 1


async def test_a_chat_that_took_its_allowance_is_busy_for_everybody(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    limits = container.settings.chat
    market, elsewhere = await factory.market(), await factory.market()
    await go_live(container, market)
    await go_live(container, elsewhere)

    await fill_the_chat(container, factory, chat, market.id, limits.room_message_limit)
    late = factory.identity(await factory.user())
    with pytest.raises(RateLimitedError) as refused:
        await chat.post(late, market.id, said("What did I miss?"))

    assert refused.value.message == CHAT_BUSY_MESSAGE
    retry_after = refused.value.details["retry_after_seconds"]
    assert 1 <= retry_after <= limits.room_message_limit_window_seconds
    # The allowance is one chat's: the chat of another market takes the message.
    await chat.post(late, elsewhere.id, said("What did I miss?"))
    assert len(await stored_messages(container)) == limits.room_message_limit + 1


@pytest.mark.redis_down
async def test_without_redis_both_rates_are_counted_in_the_process(
    container: Container, factory: Factory, chat: ChatService, clock: FrozenClock
) -> None:
    limits = container.settings.chat
    market = await factory.market()
    await go_live(container, market)
    alice = factory.identity(await factory.user())

    for number in range(limits.message_limit_per_user):
        await chat.post(alice, market.id, said(f"Message {number}"))
    with pytest.raises(RateLimitedError) as refused:
        await chat.post(alice, market.id, said("One too many."))
    assert refused.value.message == RateLimitedError.default_message
    assert refused.value.details == {
        "retry_after_seconds": limits.message_limit_per_user_window_seconds
    }

    await fill_the_chat(
        container,
        factory,
        chat,
        market.id,
        limits.room_message_limit - limits.message_limit_per_user,
    )
    with pytest.raises(RateLimitedError) as busy:
        await chat.post(factory.identity(await factory.user()), market.id, said("Too late?"))
    assert busy.value.message == CHAT_BUSY_MESSAGE
    assert busy.value.details == {"retry_after_seconds": limits.room_message_limit_window_seconds}

    # Once both windows are over, the chat takes a message of the same user again.
    clock.advance(
        timedelta(
            seconds=max(
                limits.message_limit_per_user_window_seconds,
                limits.room_message_limit_window_seconds,
            )
        )
    )
    await chat.post(alice, market.id, said("Back again."))
    assert len(await stored_messages(container)) == limits.room_message_limit + 1


# --- Who is inside (U1, U2, C5) ----------------------------------------------------------


@pytest.mark.parametrize("status", [None, MarketStatus.PROPOSED, MarketStatus.REJECTED])
async def test_a_market_that_was_never_opened_has_no_chat_for_anybody(
    container: Container, factory: Factory, chat: ChatService, status: MarketStatus | None
) -> None:
    """An id no market has, and a proposal: 404 from every operation, to the proposer
    and to an admin too, also while a broadcast row says LIVE."""
    proposer = await factory.user()
    admin = factory.identity(await factory.admin())
    market_id = uuid7()
    if status is not None:
        market = await factory.market(status=status, created_by=proposer)
        await go_live(container, market)
        market_id = market.id

    for caller in (factory.identity(proposer), admin):
        with pytest.raises(NotFoundError):
            await chat.read(caller, market_id)
        with pytest.raises(NotFoundError):
            await chat.post(caller, market_id, said("Hello?"))
    with pytest.raises(NotFoundError):
        await chat.remove(admin, market_id, uuid7())
    assert await stored_messages(container) == []


async def test_a_caller_outside_a_private_market_reaches_nothing_of_its_chat(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """Every market the application makes is public. The chat asks
    `MarketAccess.get_inside` all the same: a market for one programme keeps its chat
    to its members, its proposer and the admins, and whoever is outside neither reads,
    writes, hears a message nor spends the chat's allowance."""
    limits = container.settings.chat
    proposer = await factory.user(degree_programme=OTHER_PROGRAMME)
    market = await private_market(container, proposer, PROGRAMME)
    broadcast = await go_live(container, market)
    member = factory.identity(await factory.user(degree_programme=PROGRAMME))
    outsider = factory.identity(await factory.user(degree_programme=OTHER_PROGRAMME))

    async with listening(container.live, outsider, of(market)) as heard:
        # As many messages as the chat takes in a window, each from outside.
        sent = 0
        while sent < limits.room_message_limit:
            stranger = factory.identity(await factory.user(degree_programme=OTHER_PROGRAMME))
            for _ in range(min(limits.message_limit_per_user, limits.room_message_limit - sent)):
                with pytest.raises(NotFoundError):
                    await chat.post(stranger, market.id, said("Let me in."))
                sent += 1
        with pytest.raises(NotFoundError):
            await chat.read(outsider, market.id)

        items = [
            await chat.post(member, market.id, said("Among ourselves.")),
            await chat.post(factory.identity(proposer), market.id, said("My market.")),
            await chat.post(factory.identity(await factory.admin()), market.id, said("VIS here.")),
        ]
        await heard.nothing_more()

    read = await chat.read(member, market.id)
    assert (read.broadcast_id, read.items) == (broadcast.id, items)
    # The chat's allowance is untouched by the strangers: it takes the rest of it from
    # inside, and is busy after that.
    await fill_the_chat(
        container,
        factory,
        chat,
        market.id,
        limits.room_message_limit - len(items),
        programme=PROGRAMME,
    )
    with pytest.raises(RateLimitedError) as busy:
        await chat.post(
            factory.identity(await factory.user(degree_programme=PROGRAMME)),
            market.id,
            said("One more."),
        )
    assert busy.value.message == CHAT_BUSY_MESSAGE


# --- Removing a message (M3 to M6) -------------------------------------------------------


async def test_an_admin_removes_a_message_and_its_audit_row_keeps_what_it_said(
    container: Container, factory: Factory, chat: ChatService, clock: FrozenClock
) -> None:
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = await factory.user()
    admin = await factory.admin()
    reader = factory.identity(await factory.user())
    kept = await chat.post(factory.identity(author), market.id, said("Fair play."))
    gone = await chat.post(factory.identity(author), market.id, said("Something rude."))
    written_at = clock.now()
    clock.advance(timedelta(seconds=30))

    async with listening(container.live, reader, of(market)) as heard:
        removed = await chat.remove(factory.identity(admin), market.id, gone.id)

        pushed = await heard.next()
        await heard.nothing_more()

    assert removed == RemovedResponse(id=gone.id, removed_at=clock.now())
    assert set(pushed) == {"type", "market_id", "broadcast_id", "message_id", "ts"}
    assert ChatRemovedEvent.model_validate(pushed) == ChatRemovedEvent(
        type="chat.removed",
        market_id=market.id,
        broadcast_id=broadcast.id,
        message_id=gone.id,
        ts=clock.now(),
    )
    assert [row.id for row in await stored_messages(container)] == [kept.id]
    assert (await chat.read(reader, market.id)).items == [kept]
    [audit] = await audit_rows(container)
    assert (audit.actor_id, audit.action, audit.target) == (
        admin.id,
        AuditAction.CHAT_MESSAGE_REMOVE,
        f"chat_message:{gone.id}",
    )
    assert audit.before == {
        "market_id": str(market.id),
        "broadcast_id": str(broadcast.id),
        "user_id": str(author.id),
        "text": "Something rude.",
        "from_vis": False,
        "created_at": written_at.isoformat(),
    }
    assert audit.after is None
    assert audit.created_at == clock.now()


async def test_nobody_but_an_admin_removes_a_message(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """The service holds the rule itself, for whoever calls it: the author, the
    market's proposer and a barista are refused before anything is read."""
    proposer = await factory.user()
    market = await factory.market(created_by=proposer)
    await go_live(container, market)
    author = await factory.user()
    item = await chat.post(factory.identity(author), market.id, said("Mine to keep."))

    async with listening(container.live, factory.identity(author), of(market)) as heard:
        for caller in (author, proposer, await factory.barista()):
            with pytest.raises(ForbiddenError) as refused:
                await chat.remove(factory.identity(caller), market.id, item.id)
            assert refused.value.message == ForbiddenError.default_message
            with pytest.raises(ForbiddenError):
                await chat.remove(factory.identity(caller), uuid7(), uuid7())
        await heard.nothing_more()

    assert [row.id for row in await stored_messages(container)] == [item.id]
    assert await audit_rows(container) == []


async def test_a_message_the_market_does_not_have_is_not_found(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """An id no message has, the message of another market's chat, and a message
    removed before: 404, and nothing else changes."""
    market, elsewhere = await factory.market(), await factory.market()
    await go_live(container, market)
    await go_live(container, elsewhere)
    author = factory.identity(await factory.user())
    admin = factory.identity(await factory.admin())
    here = await chat.post(author, market.id, said("In this chat."))
    there = await chat.post(author, elsewhere.id, said("In the other chat."))

    await chat.remove(admin, market.id, here.id)
    for message_id in (uuid7(), there.id, here.id):
        with pytest.raises(NotFoundError):
            await chat.remove(admin, market.id, message_id)

    assert [row.id for row in await stored_messages(container)] == [there.id]
    assert len(await audit_rows(container)) == 1


# --- Nothing is kept after the broadcast (C8, C9) ----------------------------------------


async def test_clear_deletes_the_messages_of_its_broadcast_and_no_others(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market, elsewhere = await factory.market(), await factory.market()
    broadcast = await go_live(container, market)
    await go_live(container, elsewhere)
    author = factory.identity(await factory.user())
    for number in range(3):
        await chat.post(author, market.id, said(f"Message {number}"))
    stays = await chat.post(author, elsewhere.id, said("Another chat."))

    async with listening(container.live, author, of(market), broadcast_of(market)) as heard:
        assert await end(container, chat, broadcast) == 3
        # The end of a broadcast is the broadcast's to announce, and no audit row is
        # written for a chat that ends.
        await heard.nothing_more()

    assert [row.id for row in await stored_messages(container)] == [stays.id]
    assert await audit_rows(container) == []
    assert await chat.read(author, market.id) == ChatResponse(
        open=False, broadcast_id=None, can_remove=False, items=[]
    )
    assert (await chat.read(author, elsewhere.id)).items == [stays]
    with pytest.raises(ConflictError) as refused:
        await chat.post(author, market.id, said("Too late."))
    assert refused.value.message == CHAT_CLOSED_MESSAGE


async def test_a_broadcast_that_starts_afterwards_on_the_same_market_reads_empty(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market = await factory.market()
    author = factory.identity(await factory.user())
    earlier = await go_live(container, market)
    await chat.post(author, market.id, said("During the first one."))
    assert await end(container, chat, earlier) == 1

    later = await go_live(container, market)

    assert later.id != earlier.id
    assert await chat.read(author, market.id) == ChatResponse(
        open=True, broadcast_id=later.id, can_remove=False, items=[]
    )
    item = await chat.post(author, market.id, said("During the second one."))
    [row] = await stored_messages(container)
    assert (row.id, row.broadcast_id) == (item.id, later.id)


async def test_a_message_that_waits_while_its_chat_is_cleared_is_refused(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """The end holds the lock of the chat. A message whose first look still found the
    broadcast LIVE waits for that lock, looks again once the end has committed, and
    is refused."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = factory.identity(await factory.user())
    await chat.post(author, market.id, said("Before the end."))

    async with container.uow_factory() as uow:
        await store_end(uow, container, broadcast)
        assert await chat.clear(uow, broadcast.id) == 1
        late = asyncio.create_task(chat.post(author, market.id, said("Just in time?")))
        await someone_waits_for_an_advisory_lock(container)
        assert not late.done()

    with pytest.raises(ConflictError) as refused:
        await asyncio.wait_for(late, timeout=WAIT_SECONDS)
    assert refused.value.message == CHAT_CLOSED_MESSAGE
    assert await stored_messages(container) == []


async def test_the_end_waits_for_a_message_that_is_being_stored_and_deletes_it(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """A transaction that holds the lock of the chat and has inserted stands for a
    message on its way. The end waits for it and takes its row with the others."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = await factory.user()
    await chat.post(factory.identity(author), market.id, said("Before the end."))

    async with container.uow_factory() as uow:
        await uow.advisory_lock(chat_lock(broadcast.id))
        await uow.chat_messages.add(
            ChatMessage(
                market_id=market.id,
                broadcast_id=broadcast.id,
                user_id=author.id,
                text="On its way.",
                from_vis=False,
                created_at=container.clock.now(),
            )
        )
        ending = asyncio.create_task(end(container, chat, broadcast))
        await someone_waits_for_an_advisory_lock(container)
        assert not ending.done()

    assert await asyncio.wait_for(ending, timeout=WAIT_SECONDS) == 2
    assert await stored_messages(container) == []


async def test_of_the_messages_sent_while_a_broadcast_ends_none_is_left(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """Each message sent in that instant is stored and deleted with the others, or
    refused; none outlasts its broadcast."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    senders = [factory.identity(await factory.user()) for _ in range(8)]

    async def send(sender: AuthenticatedUser) -> ChatMessageItem | ConflictError:
        try:
            return await chat.post(sender, market.id, said("Is it over?"))
        except ConflictError as refused:
            return refused

    sending = [asyncio.create_task(send(sender)) for sender in senders[:4]]
    ending = asyncio.create_task(end(container, chat, broadcast))
    sending += [asyncio.create_task(send(sender)) for sender in senders[4:]]
    outcomes = await asyncio.wait_for(asyncio.gather(*sending), timeout=2 * WAIT_SECONDS)
    deleted = await ending

    stored = [outcome for outcome in outcomes if isinstance(outcome, ChatMessageItem)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, ConflictError)]
    assert len(stored) + len(refused) == len(senders)
    assert {outcome.message for outcome in refused} <= {CHAT_CLOSED_MESSAGE}
    assert deleted == len(stored)
    assert await stored_messages(container) == []


# --- What is pushed (L1, test 26) ---------------------------------------------------------


async def test_the_message_that_is_pushed_is_the_item_the_post_answered(
    container: Container, factory: Factory, chat: ChatService, clock: FrozenClock
) -> None:
    market, elsewhere = await factory.market(), await factory.market()
    broadcast = await go_live(container, market)
    await go_live(container, elsewhere)
    author = factory.identity(await factory.user())
    admin = factory.identity(await factory.admin())
    watcher = factory.identity(await factory.user())

    async with (
        listening(container.live, watcher, of(market)) as heard,
        listening(container.live, watcher, of(elsewhere), tab="tab-other") as on_another_market,
        listening(container.live, watcher, broadcast_of(market), tab="tab-video") as on_the_video,
    ):
        item = await chat.post(author, market.id, said("  What a\tgoal!  "))
        pushed = await heard.next()
        from_vis = await chat.post(admin, market.id, said("Welcome from VIS."))
        second = await heard.next()
        # A message that is refused is pushed to nobody.
        with pytest.raises(ValidationFailedError):
            await chat.post(author, market.id, said("​"))
        await heard.nothing_more()
        await on_another_market.nothing_more()
        await on_the_video.nothing_more()

    assert set(pushed) == {"type", "market_id", "broadcast_id", "message", "ts"}
    assert pushed["message"] == item.model_dump(mode="json")
    assert pushed["message"] == {
        "id": str(item.id),
        "username": author.username,
        "role": author.role.value,
        "text": "What a goal!",
        "from_vis": False,
    }
    assert ChatMessageEvent.model_validate(pushed) == ChatMessageEvent(
        type="chat.message",
        market_id=market.id,
        broadcast_id=broadcast.id,
        message=item,
        ts=clock.now(),
    )
    assert second["message"] == from_vis.model_dump(mode="json")
    assert second["message"]["from_vis"] is True


async def test_a_stream_that_opens_later_is_brought_no_message_from_before(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """The hub keeps no message: a tab that opens its stream reads the chat."""
    market = await factory.market()
    await go_live(container, market)
    author = factory.identity(await factory.user())
    item = await chat.post(author, market.id, said("Said before you came."))

    async with listening(container.live, author, of(market)) as heard:
        await heard.nothing_more()
        assert (await chat.read(author, market.id)).items == [item]


# --- A suspension (C10) ------------------------------------------------------------------


async def test_remove_of_user_deletes_the_users_messages_of_every_chat_that_runs(
    container: Container, factory: Factory, chat: ChatService, clock: FrozenClock
) -> None:
    """What a suspension calls in its transaction: the user's messages go, everybody
    else's stay, one audit row per broadcast keeps what was said, and one
    `broadcast.changed` per market has the open chats read again."""
    first_market, second_market, quiet = [await factory.market() for _ in range(3)]
    first = await go_live(container, first_market)
    second = await go_live(container, second_market)
    await go_live(container, quiet)
    rude, polite = await factory.user(), await factory.user()
    admin = await factory.admin()
    one = await chat.post(factory.identity(rude), first_market.id, said("Rude, one."))
    stays = await chat.post(factory.identity(polite), first_market.id, said("Polite."))
    two = await chat.post(factory.identity(rude), first_market.id, said("Rude, two."))
    three = await chat.post(factory.identity(rude), second_market.id, said("Rude, three."))
    also_stays = await chat.post(factory.identity(polite), quiet.id, said("Quiet here."))
    written_at = clock.now()
    clock.advance(timedelta(minutes=1))
    watcher = factory.identity(polite)

    async with (
        listening(container.live, watcher, broadcast_of(first_market)) as on_first,
        listening(container.live, watcher, broadcast_of(second_market), tab="tab-two") as on_second,
        listening(container.live, watcher, broadcast_of(quiet), tab="tab-quiet") as on_quiet,
        listening(container.live, watcher, of(first_market), tab="tab-chat1") as on_the_chat,
    ):
        async with container.uow_factory() as uow:
            await chat.remove_of_user(uow, rude.id, admin.id)
            # Nothing is told before the suspension has committed.
            await on_first.nothing_more()
        pushed = [await on_first.next(), await on_second.next()]
        for stream in (on_first, on_second, on_quiet, on_the_chat):
            await stream.nothing_more()

    for body, market in zip(pushed, (first_market, second_market), strict=True):
        assert set(body) == {"type", "market_id", "ts"}
        assert BroadcastChangedEvent.model_validate(body) == BroadcastChangedEvent(
            type="broadcast.changed", market_id=market.id, ts=clock.now()
        )
    assert [row.id for row in await stored_messages(container)] == [stays.id, also_stays.id]
    audits = await audit_rows(container)
    assert [(row.actor_id, row.action, row.target, row.after) for row in audits] == [
        (admin.id, AuditAction.CHAT_MESSAGE_REMOVE_OF_USER, f"user:{rude.id}", None)
    ] * 2
    by_broadcast = {row.before["broadcast_id"]: row.before for row in audits if row.before}
    assert by_broadcast == {
        str(first.id): {
            "market_id": str(first_market.id),
            "broadcast_id": str(first.id),
            "messages": [
                {"id": str(one.id), "text": "Rude, one.", "created_at": written_at.isoformat()},
                {"id": str(two.id), "text": "Rude, two.", "created_at": written_at.isoformat()},
            ],
        },
        str(second.id): {
            "market_id": str(second_market.id),
            "broadcast_id": str(second.id),
            "messages": [
                {"id": str(three.id), "text": "Rude, three.", "created_at": written_at.isoformat()}
            ],
        },
    }


async def test_remove_of_user_leaves_no_trace_of_a_user_who_wrote_nothing(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market = await factory.market()
    await go_live(container, market)
    silent, talkative = await factory.user(), await factory.user()
    admin = await factory.admin()
    item = await chat.post(factory.identity(talkative), market.id, said("Only me."))

    async with listening(
        container.live, factory.identity(talkative), broadcast_of(market), of(market)
    ) as heard:
        async with container.uow_factory() as uow:
            await chat.remove_of_user(uow, silent.id, admin.id)
        await heard.nothing_more()

    assert [row.id for row in await stored_messages(container)] == [item.id]
    assert await audit_rows(container) == []


async def test_remove_of_user_is_undone_with_the_transaction_that_called_it(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """It runs in the caller's transaction: a suspension that fails keeps the
    messages, writes no audit row and tells nobody."""
    market = await factory.market()
    await go_live(container, market)
    author = await factory.user()
    admin = await factory.admin()
    item = await chat.post(factory.identity(author), market.id, said("Still here."))

    async def suspension_that_fails() -> None:
        async with container.uow_factory() as uow:
            await chat.remove_of_user(uow, author.id, admin.id)
            raise RuntimeError("the suspension fails")

    async with listening(container.live, factory.identity(author), broadcast_of(market)) as heard:
        with pytest.raises(RuntimeError, match="the suspension fails"):
            await suspension_that_fails()
        await heard.nothing_more()

    assert [row.id for row in await stored_messages(container)] == [item.id]
    assert await audit_rows(container) == []


# --- The chat as the container wires it ---------------------------------------------------
#
# The two tests below take no stand-in: `container.chat` asks the lookup of the live
# stream, and `container.broadcasts` clears the chat it was given.


async def test_the_chat_of_the_application_closes_when_its_broadcast_is_over_by_the_clock(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A broadcast that had no piece for `BROADCAST_STALL_SECONDS` is not LIVE to the
    chat, whether or not its end is stored yet: the chat reads closed and takes
    nothing, and what was written lies in the table until that end is stored."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    member = factory.identity(await factory.user())
    item = await container.chat.post(member, market.id, said("While it runs."))
    assert await container.chat.read(member, market.id) == ChatResponse(
        open=True, broadcast_id=broadcast.id, can_remove=False, items=[item]
    )

    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    assert await container.chat.read(member, market.id) == ChatResponse(
        open=False, broadcast_id=None, can_remove=False, items=[]
    )
    with pytest.raises(ConflictError) as refused:
        await container.chat.post(member, market.id, said("Is it over?"))
    assert refused.value.message == CHAT_CLOSED_MESSAGE
    assert [row.id for row in await stored_messages(container)] == [item.id]


async def test_the_end_of_a_broadcast_clears_its_chat_in_the_application(
    container: Container, factory: Factory
) -> None:
    """An admin ends the broadcast through `BroadcastService`; the transaction that
    stores the end deletes the messages, and the chat reads closed."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    member = factory.identity(await factory.user())
    admin = factory.identity(await factory.admin())
    await container.chat.post(member, market.id, said("One."))
    await container.chat.post(admin, market.id, said("Two."))

    await container.broadcasts.end(admin, broadcast.id)

    assert await stored_messages(container) == []
    assert await container.chat.read(member, market.id) == ChatResponse(
        open=False, broadcast_id=None, can_remove=False, items=[]
    )


# --- No lock a trade waits for, and no credit (K8, K9) ------------------------------------


async def test_no_operation_of_the_chat_locks_a_row_or_moves_a_credit(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = await factory.user(balance_micro=5_000_000)
    admin = await factory.admin()

    with recorded_statements(container) as statements:
        item = await chat.post(factory.identity(author), market.id, said("One."))
        await chat.post(factory.identity(author), market.id, said("Two."))
        await chat.read(factory.identity(author), market.id)
        await chat.remove(factory.identity(admin), market.id, item.id)
        async with container.uow_factory() as uow:
            await chat.remove_of_user(uow, author.id, admin.id)
        async with container.uow_factory() as uow:
            await chat.clear(uow, broadcast.id)

    assert not [statement for statement in statements if takes_a_row_lock(statement)]
    written = {statement.split()[2] for statement in statements if writes(statement)}
    assert written == {"chat_messages", "audit_log"}
    async with container.uow_factory() as uow:
        entries = await uow.session.scalar(select(func.count()).select_from(JournalEntry))
        postings = await uow.session.scalar(select(func.count()).select_from(Posting))
    # The one entry is the signup grant the factory booked for the author.
    assert (entries, postings) == (1, 2)
    assert await factory.balance_micro(author) == 5_000_000
    await assert_books_balance(container.uow_factory)


async def test_a_message_is_stored_while_a_trade_holds_its_market_and_its_authors_account(
    container: Container, factory: Factory, chat: ChatService
) -> None:
    """A trade locks the market row and then the trader's account; the end of a
    broadcast locks the broadcast's row. A message waits for none of the three."""
    market = await factory.market()
    broadcast = await go_live(container, market)
    author = await factory.user()

    async with container.uow_factory() as uow:
        assert await uow.markets.get_for_update(market.id) is not None
        assert await uow.accounts.get_user_account(author.id, for_update=True) is not None
        assert await uow.users.get(author.id, for_update=True) is not None
        locked = await uow.session.get(Broadcast, broadcast.id, with_for_update={"key_share": True})
        assert locked is not None
        item = await asyncio.wait_for(
            chat.post(factory.identity(author), market.id, said("While a trade runs.")),
            timeout=WAIT_SECONDS,
        )
        read = await asyncio.wait_for(
            chat.read(factory.identity(author), market.id), timeout=WAIT_SECONDS
        )

    assert read.items == [item]
