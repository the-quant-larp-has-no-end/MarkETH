"""Queries on `login_tokens` and `sessions` (SPEC §3, A.3)."""

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import LoginToken, UserSession
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository


class LoginTokenRepository(BaseRepository[LoginToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, LoginToken)

    async def get_by_hash(self, token_hash: str, *, for_update: bool = False) -> LoginToken | None:
        """Lock the token before consuming it, so one link opens exactly one session."""
        statement = select(LoginToken).where(LoginToken.token_hash == token_hash)
        return await self._session.scalar(self._locked(statement, for_update))


class SessionRepository(BaseRepository[UserSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, UserSession)

    async def get(self, session_id: str) -> UserSession | None:
        return await self._get(session_id, for_update=False)

    async def delete(self, session_id: str) -> None:
        await self._session.execute(delete(UserSession).where(UserSession.id == session_id))

    async def touch(self, session_id: str, last_seen_at: datetime) -> None:
        """Record activity. Never moves `last_seen_at` backwards, never revives a session."""
        await self._session.execute(
            update(UserSession)
            .where(UserSession.id == session_id, UserSession.last_seen_at < last_seen_at)
            .values(last_seen_at=last_seen_at)
            .execution_options(**MATCHED_BY_DATABASE)
        )
