"""Queries on `chat_messages`."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage
from app.repositories.base import BaseRepository


class ChatMessageRepository(BaseRepository[ChatMessage]):
    """The messages of a broadcast are read in the order of their ids, which is the
    order they were stored in (`ix_chat_messages_broadcast_id_id`). A row is inserted
    and deleted, never updated and never locked: the app role has no UPDATE on the
    table, and Postgres asks for it before it locks a row."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ChatMessage)
