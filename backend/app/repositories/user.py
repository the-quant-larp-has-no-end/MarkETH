"""Queries on `users` (SPEC §3, A.3)."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        return await self._get(user_id, for_update=for_update)

    async def get_by_email(self, email: str, *, for_update: bool = False) -> User | None:
        """The user whose id this is: `email` as `EmailPolicy.identify` returned it."""
        statement = select(User).where(User.email == email)
        return await self._session.scalar(self._locked(statement, for_update))

    async def get_by_username(self, username: str) -> User | None:
        """Whoever holds exactly this username. A username has one spelling."""
        statement = select(User).where(User.username == username)
        return await self._session.scalar(statement)

    async def list_all(self) -> Sequence[User]:
        """Every user, oldest first."""
        return (await self._session.scalars(select(User).order_by(User.id))).all()
