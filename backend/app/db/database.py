"""The async Postgres engine and its session factory (SPEC §7, §8).

One `Database` exists per process. It is built from a connection URL rather than from
the settings alone so the API and the relay can connect as the app role while
migrations and tests point the same class at another role or server.
"""

import logging
import socket
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PostgresSettings

logger = logging.getLogger(__name__)

ERROR_FAMILIES: Final[tuple[type[Exception], ...]] = (SQLAlchemyError, OSError)
"""Base classes of everything a database call can raise. `is_unavailable` tells which
of those errors mean that Postgres could not serve the request."""

_UNREACHABLE: Final = (ConnectionError, TimeoutError, socket.gaierror)
"""What the driver raises, unwrapped, when no connection can be opened: refused or
reset, timed out, or a host name that does not resolve (a stopped compose service)."""

_LOCK_NOT_AVAILABLE: Final[str] = "55P03"
"""SQLSTATE lock_not_available: a lock was not granted within the lock timeout."""

_UNAVAILABLE_SQLSTATES: Final[frozenset[str]] = frozenset(
    {
        "53300",  # too_many_connections
        _LOCK_NOT_AVAILABLE,
        "57P01",  # admin_shutdown
        "57P02",  # crash_shutdown
        "57P03",  # cannot_connect_now: the server is starting up or shutting down
    }
)
_CONNECTION_EXCEPTION_CLASS: Final[str] = "08"
"""SQLSTATE class 08: the connection failed or was lost."""


def is_unavailable(error: BaseException) -> bool:
    """Whether `error` means that Postgres could not be reached or would not serve.

    True for a connection that cannot be opened or was lost, a server that is starting
    up or shutting down, a connection pool that stayed exhausted, and a lock that
    another transaction held for longer than `POSTGRES_LOCK_TIMEOUT_MS`. False for
    everything else a reachable database answers, such as a violated constraint, a
    denied permission or a cancelled statement: those are bugs, not outages.

    SPEC §9 answers an outage with 503; `app/api/errors.py` asks this function which
    errors are one. Without it each service would have to recognise them itself.

    A lock that was not granted in time is counted among them although Postgres is up:
    the statement failed before it changed anything, its transaction is rolled back,
    and the same request can succeed a moment later. 503 says exactly that, so a client
    keeps the Idempotency-Key of a trade and sends it again. A caller that has to tell
    that case from a lost connection asks `is_lock_timeout`.

    A bare socket error carries no sign of whose socket it was. It is taken to be the
    database's, because the Redis and ScyllaDB adapters wrap their own failures in
    errors of their own and nothing else in a request opens a connection.
    """
    if isinstance(error, (*_UNREACHABLE, PoolTimeoutError)):
        return True
    if not isinstance(error, DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    sqlstate = _sqlstate(error)
    return sqlstate is not None and (
        sqlstate in _UNAVAILABLE_SQLSTATES or sqlstate.startswith(_CONNECTION_EXCEPTION_CLASS)
    )


def is_lock_timeout(error: BaseException) -> bool:
    """Whether `error` is a lock that another transaction held for longer than
    `POSTGRES_LOCK_TIMEOUT_MS`, and nothing worse.

    Every such error is one `is_unavailable` recognises too. What sets it apart from
    the others there: Postgres is up and the connection is whole. Only the one
    statement failed, having changed nothing, and its transaction is rolled back. Work
    that can wait, such as a user's upkeep, skips its turn on this error and comes back
    later, where a lost connection has to leave as the API's 503.

    False for a connection that was lost while it waited for the lock: that is the
    lost connection.
    """
    if not isinstance(error, DBAPIError) or error.connection_invalidated:
        return False
    return _sqlstate(error) == _LOCK_NOT_AVAILABLE


def _sqlstate(error: DBAPIError) -> str | None:
    """The SQLSTATE Postgres reported, if the driver's error carries one."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


class Database:
    def __init__(self, url: str, settings: PostgresSettings) -> None:
        self._engine = create_async_engine(
            url,
            # SPEC §8: row locks, not SERIALIZABLE, order concurrent trades.
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            # Sent when a connection opens, so every session of the pool has it: a
            # statement waits this long for a lock and then fails with SQLSTATE 55P03.
            # Without it a request could queue on a market row for longer than the proxy
            # waits for the API, and commit after its client was told it had failed.
            connect_args={"server_settings": {"lock_timeout": f"{settings.lock_timeout_ms}ms"}},
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
        except ERROR_FAMILIES as exc:
            logger.warning("Postgres ping failed: %s", exc)
            return False
        return True

    async def dispose(self) -> None:
        """Close every pooled connection. Call once, on shutdown."""
        await self._engine.dispose()
