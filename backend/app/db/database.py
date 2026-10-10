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

_TRANSIENT_SQLSTATES: Final[frozenset[str]] = frozenset(
    {
        "55P03",  # lock_not_available: a lock was not granted within the lock timeout
        "40P01",  # deadlock_detected: Postgres ended this transaction to break a cycle
        "40001",  # serialization_failure: Postgres ended it for a concurrent update
    }
)
"""What Postgres reports when it is up and one transaction did not get through. Nothing
of that transaction is kept, and the same request can go through a moment later."""

_UNAVAILABLE_SQLSTATES: Final[frozenset[str]] = _TRANSIENT_SQLSTATES | {
    "53300",  # too_many_connections
    "57P01",  # admin_shutdown
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now: the server is starting up or shutting down
}
_CONNECTION_EXCEPTION_CLASS: Final[str] = "08"
"""SQLSTATE class 08: the connection failed or was lost."""


def is_unavailable(error: BaseException) -> bool:
    """Whether `error` means that Postgres could not be reached or would not serve.

    True for a connection that cannot be opened or was lost, a server that is starting
    up or shutting down, and everything `is_transient` recognises: a pool that had no
    free connection for `POSTGRES_POOL_TIMEOUT_SECONDS`, a lock that another
    transaction held for longer than `POSTGRES_LOCK_TIMEOUT_MS`, and a transaction
    Postgres ended as the victim of a deadlock or for a concurrent update. False for
    everything else a reachable database answers, such as a violated constraint, a
    denied permission or a cancelled statement: those are bugs, not outages.

    SPEC §9 answers an outage with 503; `app/api/errors.py` asks this function which
    errors are one. Without it each service would have to recognise them itself.

    The transient ones are counted among them although Postgres is up: the request
    failed before it changed anything, its transaction is rolled back, and the same
    request can succeed a moment later. 503 says exactly that, so a client keeps the
    Idempotency-Key of a trade and sends it again. A caller that has to tell those
    from a lost connection asks `is_transient`.

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


def is_transient(error: BaseException) -> bool:
    """Whether `error` means that Postgres is up and one attempt did not get through,
    and nothing worse:

    * a lock that another transaction held for longer than `POSTGRES_LOCK_TIMEOUT_MS`
      (SQLSTATE 55P03);
    * a transaction Postgres ended to break a deadlock (40P01) or for a concurrent
      update (40001);
    * a connection pool that had none free for `POSTGRES_POOL_TIMEOUT_SECONDS`: every
      connection of this process stayed in use, and the request never reached
      Postgres.

    Every such error is one `is_unavailable` recognises too. What sets it apart from
    the others there: the database serves and the connection, where there was one, is
    whole. Only the one transaction failed, having changed nothing. Work that can
    wait, such as a user's upkeep, skips its turn on this error and comes back later,
    where a lost connection has to leave as the API's 503.

    False for a connection that was lost while it waited for a lock: that is the lost
    connection.
    """
    if isinstance(error, PoolTimeoutError):
        return True
    if not isinstance(error, DBAPIError) or error.connection_invalidated:
        return False
    return _sqlstate(error) in _TRANSIENT_SQLSTATES


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
            # How long a request waits for a connection while all of them are in use.
            # After that the checkout fails, and the API answers 503 like for a lock
            # that was not granted in time.
            pool_timeout=settings.pool_timeout_seconds,
            # A statement that fails is logged with its text and without the values
            # it was sent with: those are addresses, names and codes that redeem.
            hide_parameters=True,
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
        """Whether Postgres answers a query right now. Never raises; used by `/health`.

        False also when the pool had no connection free for
        `POSTGRES_POOL_TIMEOUT_SECONDS`: the probe waits for one like any request."""
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
