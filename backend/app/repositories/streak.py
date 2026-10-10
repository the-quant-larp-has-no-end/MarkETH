"""Queries on `streaks`."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.streak import Streak
from app.repositories.base import BaseRepository


class StreakRepository(BaseRepository[Streak]):
    """A streak is read and locked by the id of its user, which is its primary key."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Streak)
