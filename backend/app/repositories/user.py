"""Queries on `users` (SPEC §3, A.3)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import UserRole, UserStatus
from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, User)

    async def get(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        return await self._get(user_id, for_update=for_update)

    async def get_by_canonical_email(
        self, canonical_email: str, *, for_update: bool = False
    ) -> User | None:
        statement = select(User).where(User.canonical_email == canonical_email)
        return await self._session.scalar(self._locked(statement, for_update))

    async def insert_if_absent(
        self,
        *,
        canonical_email: str,
        email: str,
        role: UserRole,
        status: UserStatus,
        now: datetime,
    ) -> User:
        """The user with this canonical address, created if missing, with its row locked.

        Two sign-ins racing on a new address end with one row: the loser of the INSERT
        waits for the winner to commit, inserts nothing and then locks the winner's row.
        An existing row is returned unchanged; the caller updates `email` and `role`.
        """
        await self._session.execute(
            insert(User)
            .values(
                canonical_email=canonical_email,
                email=email,
                role=role,
                status=status,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.canonical_email])
        )
        statement = select(User).where(User.canonical_email == canonical_email)
        return (await self._session.scalars(self._locked(statement, True))).one()

    async def list_all(self) -> Sequence[User]:
        """Every user, oldest first."""
        return (await self._session.scalars(select(User).order_by(User.id))).all()
