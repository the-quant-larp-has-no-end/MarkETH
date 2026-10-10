"""Queries on `users` (SPEC §3, A.3)."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccountKind, UserRole, UserStatus
from app.models.ledger import Account
from app.models.user import User
from app.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class UserWithBalance:
    """A user and what their account holds, in µCR."""

    user: User
    balance_micro: int


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

    async def search(
        self,
        *,
        text: str | None,
        role: UserRole | None,
        status: UserStatus | None,
        always_admin: Collection[str],
        before_id: UUID | None,
        limit: int,
    ) -> Sequence[UserWithBalance]:
        """Users with their balance, newest first, at most `limit` of them.

        Newest first is by `id`: a UUIDv7, so in the order the profiles were created.
        `before_id` is the id of the last user seen (the cursor). Every user has an
        account, so the join leaves nobody out.

        Each filter that is not None narrows the list:

        * `text`: the username, the name or the address contains it, whatever the case
          of its letters. It is taken literally: `%` and `_` stand for themselves.
        * `role`: the role in effect. `always_admin` are the addresses that are ADMIN
          whatever their row says: ADMIN lists them beside the rows that say ADMIN,
          and no other role lists one of them.
        * `status`: the stored status.
        """
        statement = (
            select(User, Account.balance_micro)
            .join(Account, (Account.owner_id == User.id) & (Account.kind == AccountKind.USER))
            .order_by(User.id.desc())
            .limit(limit)
        )
        if text is not None:
            statement = statement.where(
                or_(
                    User.username.icontains(text, autoescape=True),
                    User.name.icontains(text, autoescape=True),
                    User.email.icontains(text, autoescape=True),
                )
            )
        if role is UserRole.ADMIN:
            statement = statement.where(or_(User.role == role, User.email.in_(always_admin)))
        elif role is not None:
            statement = statement.where(User.role == role, User.email.not_in(always_admin))
        if status is not None:
            statement = statement.where(User.status == status)
        if before_id is not None:
            statement = statement.where(User.id < before_id)
        rows = await self._session.execute(statement)
        return [UserWithBalance(user=user, balance_micro=balance) for user, balance in rows]
