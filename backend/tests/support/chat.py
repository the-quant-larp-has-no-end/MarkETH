"""The live chat in a test: a broadcast that is on, the lookup that says so, and what a
stream brings.

The chat of a market is open exactly while the market has a broadcast in status LIVE,
and it asks one object which broadcast that is (`BroadcastLookup.live_broadcast_id`).
The tests of the chat do not go live through the broadcast service. They write the row a
start leaves behind, and give the chat a lookup of their own, `StoredLookup`, which
reads the stored status and nothing else: no stall, no time limit, no clock.

    chat = chat_of(container)                       # asks `StoredLookup`; the routes use it too
    broadcast = await go_live(container, market)    # the row of a broadcast that is on
    await chat.post(factory.identity(user), market.id, said("Hello"))
    await end(container, chat, broadcast)           # what every stored end of a broadcast does

A pushed event is read from a stream of the container's hub, the one a unit of work of
the container announces to:

    async with listening(container.live, viewer, of(market)) as heard:
        item = await chat.post(...)
        assert await heard.next() == {...}
        await heard.nothing_more()
"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select

from app.container import Container
from app.core.enums import BroadcastEndReason, BroadcastStatus, MarketStatus, MarketTier
from app.core.identity import AuthenticatedUser
from app.core.live import Topic, TopicKind
from app.models import Market, User
from app.models.broadcast import Broadcast
from app.models.chat import ChatMessage
from app.models.ops import AuditLogEntry
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.chat import PostChatMessageRequest
from app.services.broadcast_view import BroadcastLookup
from app.services.chat import ChatService
from app.services.live_hub import LiveFrame, LiveHub

WAIT_SECONDS: Final[float] = 5.0
"""How long a test waits for an event that has to come."""

QUIET_TURNS: Final[int] = 20
"""How many turns of the event loop a test gives an event that must not come. The hub
puts an event on a stream's queue before the commit that announces it returns, so one
that is not there after a few turns never arrives."""


class StoredLookup(BroadcastLookup):
    """The lookup of the chat's tests: the market's broadcast whose row says LIVE."""

    async def live_broadcast_id(self, uow: UnitOfWork, market_id: UUID) -> UUID | None:
        statement = select(Broadcast.id).where(
            Broadcast.market_id == market_id, Broadcast.status == BroadcastStatus.LIVE
        )
        found: UUID | None = await uow.session.scalar(statement)
        return found


def chat_of(container: Container) -> ChatService:
    """The chat service as the container wires it, asking a `StoredLookup` which
    broadcast is on. It stands where the routes find it from then on, so a request to
    the application reaches it."""
    chat = ChatService(
        container.uow_factory,
        container.settings,
        container.clock,
        container.market_access,
        StoredLookup(container.broadcast_view, container.clock),
        container.audit,
        container.rate_limiter,
        container.fallback_rate_limiter,
        container.live_events,
        container.email_policy,
    )
    container.chat = chat
    return chat


def said(text: str) -> PostChatMessageRequest:
    """The body of a message, as the route hands it to the service."""
    return PostChatMessageRequest(text=text)


def of(market: Market) -> Topic:
    """The topic the messages of a market's chat go out on."""
    return Topic(TopicKind.MARKET, market.id)


def broadcast_of(market: Market) -> Topic:
    """The topic of a market's live stream."""
    return Topic(TopicKind.BROADCAST, market.id)


async def go_live(
    container: Container, market: Market, *, status: BroadcastStatus = BroadcastStatus.LIVE
) -> Broadcast:
    """A broadcast of `market` in `status`, as its proposer's request, an admin's
    approval and a start leave the row. LIVE unless the test says otherwise; the
    proposer stands in as the admin who decided."""
    now = container.clock.now()
    row = Broadcast(
        market_id=market.id,
        broadcaster_id=market.created_by,
        status=status,
        requested_at=now,
    )
    if status is not BroadcastStatus.REQUESTED:
        row.decided_by = market.created_by
        row.decided_at = now
    if status is BroadcastStatus.REFUSED:
        row.refusal_reason = "Refused in a test."
    if status in (BroadcastStatus.LIVE, BroadcastStatus.ENDED):
        row.started_at = now
        row.on_air_at = now
        row.last_chunk_at = now
    if status is BroadcastStatus.ENDED:
        row.ended_at = now
        row.end_reason = BroadcastEndReason.BROADCASTER
    async with container.uow_factory() as uow:
        await uow.broadcasts.add(row)
    return row


async def store_end(uow: UnitOfWork, container: Container, broadcast: Broadcast) -> None:
    """Write the end of `broadcast` in the transaction of `uow`, as an admin's end
    writes it. The chat is not cleared: that is the next thing the caller does."""
    row = await uow.session.get(Broadcast, broadcast.id, populate_existing=True)
    assert row is not None
    row.status = BroadcastStatus.ENDED
    row.ended_at = container.clock.now()
    row.end_reason = BroadcastEndReason.ADMIN
    await uow.flush()


async def end(container: Container, chat: ChatService, broadcast: Broadcast) -> int:
    """What every transaction that stores the end of a broadcast does: write the
    status, then clear the chat. Returns how many messages the chat had."""
    async with container.uow_factory() as uow:
        await store_end(uow, container, broadcast)
        return await chat.clear(uow, broadcast.id)


async def write_messages(
    container: Container, broadcast: Broadcast, author: User, texts: Sequence[str]
) -> list[ChatMessage]:
    """Rows of `chat_messages` written straight to the table, in the order of `texts`:
    for a test that needs more messages than the rate limits let anybody send."""
    now = container.clock.now()
    rows = [
        ChatMessage(
            market_id=broadcast.market_id,
            broadcast_id=broadcast.id,
            user_id=author.id,
            text=text,
            from_vis=False,
            created_at=now,
        )
        for text in texts
    ]
    async with container.uow_factory() as uow:
        for row in rows:
            await uow.chat_messages.add(row)
    return rows


async def stored_messages(container: Container) -> list[ChatMessage]:
    """Every row of `chat_messages`, in the order the rows were stored in."""
    async with container.uow_factory() as uow:
        rows = await uow.session.scalars(select(ChatMessage).order_by(ChatMessage.id))
        return list(rows.all())


async def audit_rows(container: Container) -> list[AuditLogEntry]:
    """Every row of `audit_log`, earliest first."""
    async with container.uow_factory() as uow:
        rows = await uow.session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))
        return list(rows.all())


async def private_market(container: Container, proposer: User, programme: str) -> Market:
    """An OPEN market for the members of `programme`, as an approval leaves it. The
    application has no way to make one yet; the chat asks `MarketAccess.get_inside`
    all the same, and a test holds it to that."""
    now = container.clock.now()
    pricing = container.settings.pricing
    b_micro = pricing.tier_b_micro(MarketTier.DEFAULT)
    market = Market(
        question="Will the exam of Analysis I be held in the HPH?",
        yes_def="It is held there.",
        no_def="It is held somewhere else.",
        resolution_criteria="The exam plan decides.",
        resolution_source="ethz.ch/exams",
        status=MarketStatus.OPEN,
        tier=MarketTier.DEFAULT,
        b_micro=b_micro,
        budget_micro=lmsr.max_loss_micro(b_micro, precision=pricing.decimal_precision),
        close_at=now + timedelta(days=7),
        resolve_by=now + timedelta(days=14),
        created_by=proposer.id,
        approved_by=proposer.id,
        audience_programme=programme,
        created_at=now,
        opened_at=now,
    )
    async with container.uow_factory() as uow:
        await uow.markets.add(market)
        await uow.accounts.create_market_account(market.id)
    return market


class Heard:
    """What one stream of the hub brought after its `ready`, read in a task of its own
    as a route reads it."""

    def __init__(self) -> None:
        self._bodies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def take(self, frames: AsyncIterator[LiveFrame]) -> None:
        async for frame in frames:
            body: dict[str, Any] = json.loads(frame.data)
            assert frame.event == body["type"]
            self._bodies.put_nowait(body)

    async def next(self) -> dict[str, Any]:
        """The body of the next event the stream brought."""
        return await asyncio.wait_for(self._bodies.get(), timeout=WAIT_SECONDS)

    async def nothing_more(self) -> None:
        """The stream brought nothing beyond what the test has read."""
        for _ in range(QUIET_TURNS):
            await asyncio.sleep(0)
        assert self._bodies.empty(), self._bodies.get_nowait()


@asynccontextmanager
async def listening(
    hub: LiveHub, viewer: AuthenticatedUser, *topics: Topic, tab: str = "tab-chat"
) -> AsyncIterator[Heard]:
    """A stream of `viewer` on `hub` that follows `topics`, opened and past its
    `ready`."""
    async with hub.open(viewer, tab, topics) as frames:
        ready = await anext(frames)
        assert ready.event == "ready"
        heard = Heard()
        reader = asyncio.create_task(heard.take(frames))
        try:
            yield heard
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
