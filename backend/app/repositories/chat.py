"""Queries on `chat_messages`."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Delete, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage
from app.models.user import User
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository


@dataclass(frozen=True, slots=True)
class DeletedMessage:
    """A message a DELETE took away: every column of the row that is gone."""

    id: UUID
    market_id: UUID
    broadcast_id: UUID
    user_id: UUID
    text: str
    from_vis: bool
    created_at: datetime


_ROW: Final = (
    ChatMessage.id,
    ChatMessage.market_id,
    ChatMessage.broadcast_id,
    ChatMessage.user_id,
    ChatMessage.text,
    ChatMessage.from_vis,
    ChatMessage.created_at,
)
"""The columns a DELETE returns, in the order of the fields of `DeletedMessage`."""


class ChatMessageRepository(BaseRepository[ChatMessage]):
    """The messages of a broadcast are read in the order of their ids, which is the
    order they were stored in (`ix_chat_messages_broadcast_id_id`). A row is inserted
    and deleted, never updated and never locked: the app role has no UPDATE on the
    table, and Postgres asks for it before it locks a row.

    A DELETE says what it took away: a message exists nowhere else, so whoever keeps
    a record of one that is removed gets the row from the statement that deletes it.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChatMessage)

    async def list_latest(
        self, broadcast_id: UUID, limit: int
    ) -> Sequence[tuple[ChatMessage, str]]:
        """The latest `limit` messages of a broadcast, earliest first, each with its
        author's username as it reads now."""
        statement = (
            select(ChatMessage, User.username)
            .join(User, User.id == ChatMessage.user_id)
            .where(ChatMessage.broadcast_id == broadcast_id)
            .order_by(ChatMessage.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [(message, username) for message, username in reversed(rows)]

    async def broadcast_ids_of_user(self, user_id: UUID) -> Sequence[UUID]:
        """The broadcasts in whose chats a user has a message, in ascending order."""
        statement = (
            select(ChatMessage.broadcast_id)
            .where(ChatMessage.user_id == user_id)
            .distinct()
            .order_by(ChatMessage.broadcast_id)
        )
        return (await self._session.scalars(statement)).all()

    async def delete_one(self, message_id: UUID, market_id: UUID) -> DeletedMessage | None:
        """Delete one message of a market. None when the market has no such message:
        it never had, or the message is gone already."""
        deleted = await self._deleted(
            delete(ChatMessage).where(
                ChatMessage.id == message_id, ChatMessage.market_id == market_id
            )
        )
        return deleted[0] if deleted else None

    async def delete_of_broadcast(self, broadcast_id: UUID) -> int:
        """Delete every message of a broadcast. Returns how many there were."""
        statement = delete(ChatMessage).where(ChatMessage.broadcast_id == broadcast_id)
        return len(await self._deleted(statement))

    async def delete_of_user(
        self, user_id: UUID, broadcast_ids: Collection[UUID]
    ) -> Sequence[DeletedMessage]:
        """Delete every message a user has in the chats of `broadcast_ids`. Returns the
        deleted rows in the order they were stored in."""
        if not broadcast_ids:
            return []
        return await self._deleted(
            delete(ChatMessage).where(
                ChatMessage.user_id == user_id, ChatMessage.broadcast_id.in_(broadcast_ids)
            )
        )

    async def _deleted(self, statement: Delete) -> list[DeletedMessage]:
        """Run a DELETE and give the rows it took away, in the order of their ids. A
        copy of a deleted row that this session holds leaves the session with it
        (`MATCHED_BY_DATABASE`)."""
        rows = await self._session.execute(
            statement.returning(*_ROW).execution_options(**MATCHED_BY_DATABASE)
        )
        return sorted((DeletedMessage(*row) for row in rows), key=lambda message: message.id)
