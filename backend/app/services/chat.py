"""The live chat beside a market's live stream (SPEC §6).

The chat of a market is open exactly while the market has a broadcast that is LIVE
(`BroadcastLookup.live_broadcast_id`). Whoever is inside the market reads it and writes
in it (`MarketAccess.get_inside`); a market the caller is not inside of is answered like
an id no market has. A message belongs to one broadcast: a broadcast that starts later
on the same market starts with an empty chat.

Nothing of a chat is kept after its broadcast. A row of `chat_messages` exists exactly
while its message can be read: every transaction that stores the end of a broadcast
calls `clear`, and a removed message is deleted on the spot. What stays of a removal is
its audit row, which holds the text.

A message is one line of plain text. It is cleaned before it is measured and stored
(`app.core.text.clean_line`) and comes back character for character as cleaned.

Locks. An insert, the clearing of a chat and the deletion of what one user wrote run one
at a time per broadcast, under the advisory lock `chat:{broadcast_id}`: a message sent
in the instant its broadcast ends is deleted with the others or refused, and none
outlasts its broadcast. A transaction that takes the lock of more than one broadcast
takes them in ascending order of the ids. The removal of one message locks its row by
deleting it and nothing else. No operation here locks a market row, a broadcast row, a
user or an account: the foreign keys of an insert ask for FOR KEY SHARE, which waits for
no lock a trade, the end of a broadcast or a suspension holds. So a busy chat never
makes a trade wait; and no credit moves.

What changes is announced inside the transaction that writes it (`uow.announce`):
`chat.message` carries the message, `chat.removed` its id, both on `market:{id}` for
whoever is inside the market.
"""

from collections import defaultdict
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import UserRole
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.live import Topic, TopicKind
from app.core.text import clean_line
from app.integrations.redis.client import RedisUnavailableError
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.models.chat import ChatMessage
from app.repositories.chat import DeletedMessage
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.chat import (
    ChatMessageItem,
    ChatResponse,
    PostChatMessageRequest,
    RemovedResponse,
)
from app.schemas.live import BroadcastChangedEvent, ChatMessageEvent, ChatRemovedEvent
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.broadcast_view import BroadcastLookup
from app.services.email_policy import EmailPolicy
from app.services.live_events import LiveEvents
from app.services.market_access import MarketAccess

CHAT_CLOSED_MESSAGE: Final[str] = "The live chat is closed."
"""Message of the `ConflictError` that refuses a message while the market has no
broadcast that is LIVE."""

CHAT_BUSY_MESSAGE: Final[str] = "The chat is busy. Try again in a moment."
"""Message of the `RateLimitedError` of `CHAT_ROOM_MESSAGE_LIMIT`."""

EMPTY_TEXT_MESSAGE: Final[str] = "This cannot be empty."
"""Message of the `ValidationFailedError` for a text that holds nothing once it is
cleaned. Worded as `app/api/validation.py` words an empty field."""

TEXT_FIELD: Final[str] = "text"
"""`details.field` of a refused text."""

USER_RATE: Final[str] = "chat"
"""Name of the rate rule of `CHAT_MESSAGE_LIMIT_PER_USER`. Its subject is the user's
id: the key is `ratelimit:chat:{user_id}`."""

ROOM_RATE: Final[str] = "chat-room"
"""Name of the rate rule of `CHAT_ROOM_MESSAGE_LIMIT`. Its subject is the broadcast's
id: the key is `ratelimit:chat-room:{broadcast_id}`."""


def too_long_message(limit: int) -> str:
    """Message of the `ValidationFailedError` for a text longer than
    `CHAT_MESSAGE_MAX_LENGTH`, which is `limit`. Worded as `app/api/validation.py`
    words a field that is too long."""
    return f"Use at most {limit:,} {'character' if limit == 1 else 'characters'}."


def chat_lock(broadcast_id: UUID) -> str:
    """Name of the advisory lock under which the messages of one broadcast are
    inserted and deleted."""
    return f"chat:{broadcast_id}"


class ChatService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        access: MarketAccess,
        broadcasts: BroadcastLookup,
        audit: AuditService,
        rate_limiter: RateLimiter,
        fallback_rate_limiter: RateLimiter,
        events: LiveEvents,
        email_policy: EmailPolicy,
    ) -> None:
        self._uow_factory = uow_factory
        self._limits = settings.chat
        self._clock = clock
        self._access = access
        self._broadcasts = broadcasts
        self._audit = audit
        self._rate_limiter = rate_limiter
        self._fallback_rate_limiter = fallback_rate_limiter
        self._events = events
        self._email_policy = email_policy
        self._user_rate = RateLimit(
            USER_RATE,
            self._limits.message_limit_per_user,
            self._limits.message_limit_per_user_window_seconds,
        )
        self._room_rate = RateLimit(
            ROOM_RATE,
            self._limits.room_message_limit,
            self._limits.room_message_limit_window_seconds,
        )

    async def read(self, viewer: AuthenticatedUser, market_id: UUID) -> ChatResponse:
        """The chat of a market as it stands (`GET /markets/{id}/chat`).

        One read transaction. A market `viewer` is not inside of: `NotFoundError`
        (404). With no broadcast that is LIVE the chat is closed and empty. Otherwise
        the latest `CHAT_BACKLOG_SIZE` messages of that broadcast, earliest first:
        the chat has no pages and no way back beyond them. `can_remove` says whether
        the viewer is an admin.
        """
        async with self._uow_factory() as uow:
            await self._access.get_inside(uow, viewer, market_id)
            broadcast_id = await self._broadcasts.live_broadcast_id(uow, market_id)
            if broadcast_id is None:
                return ChatResponse(
                    open=False, broadcast_id=None, can_remove=viewer.is_admin, items=[]
                )
            latest = await uow.chat_messages.list_latest(broadcast_id, self._limits.backlog_size)
            return ChatResponse(
                open=True,
                broadcast_id=broadcast_id,
                can_remove=viewer.is_admin,
                items=[
                    _item(
                        message,
                        author.username,
                        self._email_policy.effective_role(author.email, author.role),
                    )
                    for message, author in latest
                ],
            )

    async def post(
        self, user: AuthenticatedUser, market_id: UUID, request: PostChatMessageRequest
    ) -> ChatMessageItem:
        """Store one message and announce it (`POST /markets/{id}/chat`).

        The checks run in this order, and an answer names the first that fails:

        1. The text is cleaned to one line. Nothing left: `ValidationFailedError`
           (422, `EMPTY_TEXT_MESSAGE`); longer than `CHAT_MESSAGE_MAX_LENGTH`: 422 with
           `too_long_message`. Both name `details.field` = `text`.
        2. The user's rate, `CHAT_MESSAGE_LIMIT_PER_USER` per window:
           `RateLimitedError` (429). A request refused in step 1 does not count.
        3. A read transaction. A market the user is not inside of: `NotFoundError`
           (404). No broadcast that is LIVE: `ConflictError(CHAT_CLOSED_MESSAGE)`
           (409).
        4. The chat's rate, `CHAT_ROOM_MESSAGE_LIMIT` per window for everybody
           together: `RateLimitedError(CHAT_BUSY_MESSAGE)` (429). It stands after
           step 3, so a caller outside the market never spends the chat's allowance.
        5. The write transaction: the advisory lock of the chat, then the lookup
           again. Anything but the broadcast of step 3 is 409 as there: the
           broadcast ended while the message waited. Then the market again, for the
           row the event is built from, the insert, and the announcement of
           `chat.message` with the item this returns.

        Steps 3 and 5 are two short transactions so that no Redis call runs inside
        one. Both rates are counted in Redis, and in this process while Redis cannot
        be reached. The id of a message is made once the lock is granted, so the ids
        of a chat grow in the order of its commits. `from_vis` is the author's role
        in effect at this moment and is stored with the row.
        """
        text = clean_line(request.text)
        if not text:
            raise ValidationFailedError(EMPTY_TEXT_MESSAGE, details={"field": TEXT_FIELD})
        if len(text) > self._limits.message_max_length:
            raise ValidationFailedError(
                too_long_message(self._limits.message_max_length),
                details={"field": TEXT_FIELD},
            )
        await self._count(self._user_rate, str(user.id))

        async with self._uow_factory() as uow:
            await self._access.get_inside(uow, user, market_id)
            broadcast_id = await self._broadcasts.live_broadcast_id(uow, market_id)
        if broadcast_id is None:
            raise ConflictError(CHAT_CLOSED_MESSAGE)
        await self._count(self._room_rate, str(broadcast_id), refusal=CHAT_BUSY_MESSAGE)

        async with self._uow_factory() as uow:
            await uow.advisory_lock(chat_lock(broadcast_id))
            if await self._broadcasts.live_broadcast_id(uow, market_id) != broadcast_id:
                raise ConflictError(CHAT_CLOSED_MESSAGE)
            market = await self._access.get_inside(uow, user, market_id)
            now = self._clock.now()
            message = await uow.chat_messages.add(
                ChatMessage(
                    market_id=market.id,
                    broadcast_id=broadcast_id,
                    user_id=user.id,
                    text=text,
                    from_vis=user.is_admin,
                    created_at=now,
                )
            )
            item = _item(message, user.username, user.role)
            uow.announce(
                self._events.about(
                    market,
                    (Topic(TopicKind.MARKET, market.id),),
                    ChatMessageEvent(
                        type="chat.message",
                        market_id=market.id,
                        broadcast_id=broadcast_id,
                        message=item,
                        ts=now,
                    ),
                )
            )
            return item

    async def remove(
        self, actor: AuthenticatedUser, market_id: UUID, message_id: UUID
    ) -> RemovedResponse:
        """Delete one message as an admin, audit-logged
        (`POST /admin/markets/{id}/chat/{message_id}/remove`).

        Anybody but an admin: `ForbiddenError` (403), before anything is read. Then
        one transaction: a market the actor is not inside of, and a message the
        market does not have (never had, removed before, or deleted with its
        broadcast): `NotFoundError` (404). The row is deleted; the audit row
        `chat_message.remove` holds every column of it, the text and the author
        among them; `chat.removed` is announced with the id and nothing of the
        message.
        """
        if not actor.is_admin:
            raise ForbiddenError
        async with self._uow_factory() as uow:
            market = await self._access.get_inside(uow, actor, market_id)
            deleted = await uow.chat_messages.delete_one(message_id, market.id)
            if deleted is None:
                raise NotFoundError
            now = self._clock.now()
            await self._audit.record(
                uow,
                actor_id=actor.id,
                action=AuditAction.CHAT_MESSAGE_REMOVE,
                target=AuditService.target(AuditTarget.CHAT_MESSAGE, deleted.id),
                before={
                    "market_id": str(deleted.market_id),
                    "broadcast_id": str(deleted.broadcast_id),
                    "user_id": str(deleted.user_id),
                    "text": deleted.text,
                    "from_vis": deleted.from_vis,
                    "created_at": deleted.created_at.isoformat(),
                },
                after=None,
            )
            uow.announce(
                self._events.about(
                    market,
                    (Topic(TopicKind.MARKET, market.id),),
                    ChatRemovedEvent(
                        type="chat.removed",
                        market_id=market.id,
                        broadcast_id=deleted.broadcast_id,
                        message_id=deleted.id,
                        ts=now,
                    ),
                )
            )
            return RemovedResponse(id=deleted.id, removed_at=now)

    async def clear(self, uow: UnitOfWork, broadcast_id: UUID) -> int:
        """Delete every message of a broadcast, in the caller's transaction: what every
        transaction that stores the end of a broadcast calls, once it has written the
        status. Returns how many there were.

        It takes the advisory lock of the chat first. An insert that holds the lock
        commits before this and is deleted here; one that waits for the lock gets it
        when the caller has committed, finds no broadcast that is LIVE and is
        refused. It announces nothing and writes no audit row: the end of the
        broadcast is the caller's to announce.
        """
        await uow.advisory_lock(chat_lock(broadcast_id))
        return await uow.chat_messages.delete_of_broadcast(broadcast_id)

    async def remove_of_user(self, uow: UnitOfWork, user_id: UUID, actor_id: UUID) -> None:
        """Delete every message of `user_id` in the chats that run, in the caller's
        transaction, audit-logged as `actor_id`: what a suspension calls.

        The chats in which the user has a message are read, their advisory locks are
        taken in ascending order of the broadcasts' ids, and the user's messages in
        those chats are deleted. Per broadcast one audit row
        `chat_message.remove_of_user` on `user:{user_id}`, whose `before` holds
        `market_id`, `broadcast_id` and `messages`: the deleted rows in the order
        they were stored in, each with `id`, `text` and `created_at`. Per market one
        `broadcast.changed` on `broadcast:{market_id}` for whoever is inside the
        market, so an open chat is read again. A user without a message leaves no
        audit row and no event.

        A message the account sends in the same instant, to a chat it has no message
        in yet or behind the lock, can outlast this; an admin removes it.
        """
        broadcast_ids = await uow.chat_messages.broadcast_ids_of_user(user_id)
        for broadcast_id in broadcast_ids:
            await uow.advisory_lock(chat_lock(broadcast_id))
        deleted = await uow.chat_messages.delete_of_user(user_id, broadcast_ids)
        if not deleted:
            return
        now = self._clock.now()
        by_broadcast: defaultdict[UUID, list[DeletedMessage]] = defaultdict(list)
        for message in deleted:
            by_broadcast[message.broadcast_id].append(message)
        for broadcast_id in sorted(by_broadcast):
            messages = by_broadcast[broadcast_id]
            await self._audit.record(
                uow,
                actor_id=actor_id,
                action=AuditAction.CHAT_MESSAGE_REMOVE_OF_USER,
                target=AuditService.target(AuditTarget.USER, user_id),
                before={
                    "market_id": str(messages[0].market_id),
                    "broadcast_id": str(broadcast_id),
                    "messages": [
                        {
                            "id": str(message.id),
                            "text": message.text,
                            "created_at": message.created_at.isoformat(),
                        }
                        for message in messages
                    ],
                },
                after=None,
            )
        markets = await uow.markets.get_many({message.market_id for message in deleted})
        for market_id in sorted(markets):
            market = markets[market_id]
            uow.announce(
                self._events.about(
                    market,
                    (Topic(TopicKind.BROADCAST, market.id),),
                    BroadcastChangedEvent(type="broadcast.changed", market_id=market.id, ts=now),
                )
            )

    async def _count(self, rule: RateLimit, subject: str, *, refusal: str | None = None) -> None:
        """Count one message against `rule`, in Redis, or in this process while Redis
        cannot be reached. Beyond the limit: `RateLimitedError` with `refusal`, or
        with the sentence every rate limit of the API has."""
        try:
            decision = await self._rate_limiter.hit(rule, subject)
        except RedisUnavailableError:
            decision = await self._fallback_rate_limiter.hit(rule, subject)
        if not decision.allowed:
            raise RateLimitedError(refusal, retry_after_seconds=decision.retry_after_seconds)


def _item(message: ChatMessage, username: str, role: UserRole) -> ChatMessageItem:
    return ChatMessageItem(
        id=message.id, username=username, role=role, text=message.text, from_vis=message.from_vis
    )
