"""Queries on `notices` (SPEC §6, A.5)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market import Market
from app.models.notice import Notice
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository


class NoticeRepository(BaseRepository[Notice]):
    """A notice is read together with the question of the market it is about: that is
    what its user is shown, and one statement reads both."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Notice)

    async def list_unseen(self, user_id: UUID) -> Sequence[tuple[Notice, str]]:
        """A user's notices that are not marked seen, oldest first, each with its
        market's question."""
        statement = (
            select(Notice, Market.question)
            .join(Market, Market.id == Notice.market_id)
            .where(Notice.user_id == user_id, Notice.seen_at.is_(None))
            .order_by(Notice.created_at, Notice.id)
        )
        return [(notice, question) for notice, question in await self._session.execute(statement)]

    async def get_for_user(self, notice_id: UUID, user_id: UUID) -> tuple[Notice, str] | None:
        """One notice of `user_id` with its market's question, seen or not. None when
        there is no such notice or it is somebody else's."""
        statement = (
            select(Notice, Market.question)
            .join(Market, Market.id == Notice.market_id)
            .where(Notice.id == notice_id, Notice.user_id == user_id)
        )
        row = (await self._session.execute(statement)).first()
        return None if row is None else (row[0], row[1])

    async def mark_seen(self, notice_id: UUID, user_id: UUID, now: datetime) -> bool:
        """Mark a notice of `user_id` seen as of `now`. False if there is no such
        notice, it is somebody else's, or it was marked before: then nothing changes."""
        statement = (
            update(Notice)
            .where(Notice.id == notice_id, Notice.user_id == user_id, Notice.seen_at.is_(None))
            .values(seen_at=now)
            .returning(Notice.id)
            .execution_options(**MATCHED_BY_DATABASE)
        )
        return (await self._session.scalars(statement)).first() is not None
