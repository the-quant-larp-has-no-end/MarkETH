"""Which errors of a database call mean "Postgres could not serve this" (SPEC §9).

The API answers those with 503 and everything else with 500. `is_unavailable` draws the
line; these are the cases on each side of it, without a database.

One of them is no outage at all: a lock that another transaction held for too long.
`is_lock_timeout` tells it from a lost connection, for work that can wait where a
request cannot (a user's upkeep, A.3).
"""

import socket

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.db.database import ERROR_FAMILIES, is_lock_timeout, is_unavailable


class DriverError(Exception):
    """Stands in for the driver's error, which carries Postgres' SQLSTATE."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


def reported(sqlstate: str | None, error: type[DBAPIError] = DBAPIError) -> DBAPIError:
    return error("SELECT 1", {}, DriverError(sqlstate))


def lost_connection(sqlstate: str | None = None) -> DBAPIError:
    return InterfaceError("SELECT 1", {}, DriverError(sqlstate), connection_invalidated=True)


LOCK_TIMEOUTS = [
    pytest.param(reported("55P03"), id="lock-not-granted-in-time"),
    pytest.param(reported("55P03", OperationalError), id="lock-not-granted-in-time-operational"),
]

LOST_CONNECTIONS = [
    pytest.param(ConnectionRefusedError("connect call failed"), id="refused"),
    pytest.param(ConnectionResetError("reset by peer"), id="reset"),
    pytest.param(BrokenPipeError(), id="broken-pipe"),
    pytest.param(socket.gaierror(8, "nodename nor servname provided"), id="unresolvable-host"),
    pytest.param(TimeoutError("connect timed out"), id="connect-timeout"),
    pytest.param(PoolTimeoutError("QueuePool limit reached"), id="pool-exhausted"),
    pytest.param(lost_connection(), id="connection-lost"),
    pytest.param(lost_connection("55P03"), id="connection-lost-while-waiting-for-a-lock"),
    pytest.param(reported("08006"), id="connection-failure"),
    pytest.param(reported("08P01"), id="protocol-violation"),
    pytest.param(reported("57P01"), id="admin-shutdown"),
    pytest.param(reported("57P02"), id="crash-shutdown"),
    pytest.param(reported("57P03"), id="starting-up"),
    pytest.param(reported("53300", OperationalError), id="too-many-connections"),
]
"""Postgres cannot be reached or does not serve: every outage that is not a lock timeout."""

OUTAGES = [*LOST_CONNECTIONS, *LOCK_TIMEOUTS]

BUGS = [
    pytest.param(reported("23505", IntegrityError), id="unique-violation"),
    pytest.param(reported("23514", IntegrityError), id="check-violation"),
    pytest.param(reported("42501"), id="permission-denied"),
    pytest.param(reported("57014"), id="statement-cancelled"),
    pytest.param(reported("40P01"), id="deadlock"),
    pytest.param(reported("55006"), id="object-in-use"),
    pytest.param(reported("55000"), id="object-not-in-prerequisite-state"),
    pytest.param(reported("28P01"), id="wrong-password"),
    pytest.param(reported("3D000"), id="no-such-database"),
    pytest.param(reported(None), id="no-sqlstate"),
    pytest.param(FileNotFoundError("no such file"), id="file-not-found"),
    pytest.param(PermissionError("not allowed"), id="os-permission"),
    pytest.param(ValueError("a timestamp needs a timezone"), id="value-error"),
    pytest.param(RuntimeError("a bug"), id="runtime-error"),
]


@pytest.mark.parametrize("error", OUTAGES)
def test_an_outage_is_recognised(error: Exception) -> None:
    assert is_unavailable(error)
    assert isinstance(error, ERROR_FAMILIES), "the API would never ask about this error"


@pytest.mark.parametrize("error", BUGS)
def test_an_answer_from_a_reachable_database_is_not_an_outage(error: Exception) -> None:
    assert not is_unavailable(error)


@pytest.mark.parametrize("error", LOCK_TIMEOUTS)
def test_a_lock_that_was_not_granted_in_time_is_told_apart(error: Exception) -> None:
    """Postgres is up and the connection is whole: only the one statement failed."""
    assert is_lock_timeout(error)
    assert is_unavailable(error), "to a request it is still an answer of 503"


@pytest.mark.parametrize("error", LOST_CONNECTIONS)
def test_a_lost_connection_is_no_lock_timeout(error: Exception) -> None:
    """Whoever swallows a lock timeout must not swallow this."""
    assert not is_lock_timeout(error)
    assert is_unavailable(error)


@pytest.mark.parametrize("error", BUGS)
def test_a_bug_is_no_lock_timeout(error: Exception) -> None:
    """A deadlock or a cancelled statement in particular: neither is the lock timeout."""
    assert not is_lock_timeout(error)
