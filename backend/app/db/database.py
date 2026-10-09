"""The async Postgres engine and its session factory (SPEC §7, §8).

One `Database` exists per process. It is built from a connection URL rather than from
the settings alone so the API and the relay can connect as the app role while
migrations and tests point the same class at another role or server.
"""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PostgresSettings

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str, settings: PostgresSettings) -> None:
        self._engine = create_async_engine(
            url,
            # SPEC §8: row locks, not SERIALIZABLE, order concurrent trades.
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
        )
        # Objects stay readable after COMMIT: services return values built from them.
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def ping(self) -> bool:
        """Whether Postgres answers a query right now. Never raises; used by `/health`."""
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            logger.warning("Postgres ping failed: %s", exc)
            return False
        return True

    async def dispose(self) -> None:
        """Close every pooled connection. Call once, on shutdown."""
        await self._engine.dispose()
