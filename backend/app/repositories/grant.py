"""Queries on `grants` (SPEC §2)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.grant import Grant
from app.repositories.base import BaseRepository


class GrantRepository(BaseRepository[Grant]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Grant)

    async def insert_if_absent(self, user_id: UUID, period: str, now: datetime) -> Grant | None:
        """Record a grant for (user, period); None if that grant already exists.

        Only the caller that gets a row back may book the credit. A concurrent attempt
        waits in the INSERT for this transaction and then inserts nothing (SPEC §2).
        """
        statement = (
            insert(Grant)
            .values(user_id=user_id, period=period, created_at=now)
            .on_conflict_do_nothing(index_elements=[Grant.user_id, Grant.period])
            .returning(Grant)
        )
        return await self._session.scalar(statement)

    async def list_periods(self, user_id: UUID) -> Sequence[str]:
        """Periods already granted to a user: `SIGNUP` and `YYYY-MM` values, ascending."""
        statement = select(Grant.period).where(Grant.user_id == user_id).order_by(Grant.period)
        return (await self._session.scalars(statement)).all()

    async def set_entry(self, grant_id: UUID, entry_id: UUID) -> None:
        """Link a grant to the journal entry that booked it."""
        await self._session.execute(
            update(Grant).where(Grant.id == grant_id).values(entry_id=entry_id)
        )
