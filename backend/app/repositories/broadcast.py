"""Queries on `broadcasts`."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.broadcast import Broadcast
from app.repositories.base import BaseRepository


class BroadcastRepository(BaseRepository[Broadcast]):
    """A broadcast is locked as `BaseRepository` locks every row, FOR NO KEY UPDATE: the
    foreign key of a chat message that is being inserted checks its broadcast with FOR
    KEY SHARE, and so waits for no lock the end of that broadcast holds."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Broadcast)
