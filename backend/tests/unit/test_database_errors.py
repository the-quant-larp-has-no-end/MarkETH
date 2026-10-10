"""Which errors of a database call mean "Postgres could not serve this" (SPEC §9).

The API answers those with 503 and everything else with 500. `is_unavailable` draws the
line; these are the cases on each side of it, without a database.

Some of them are no outage at all: Postgres is up and one attempt did not get through.
A lock that another transaction held for too long, a transaction ended as the victim of
a deadlock or for a concurrent update, a pool that had no connection free in time.
`is_transient` tells those from a lost connection, for work that can wait where a
request cannot (a user's upkeep, A.3).
"""

import socket

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.db.database import ERROR_FAMILIES, is_transient, is_unavailable


class DriverError(Exception):
    """Stands in for the driver's error, which carries Postgres' SQLSTATE."""

    def __init__(self, sqlstate: str | None) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


def reported(sqlstate: str | None, error: type[DBAPIError] = DBAPIError) -> DBAPIError:
    return error("SELECT 1", {}, DriverError(sqlstate))


def lost_connection(sqlstate: str | None = None) -> DBAPIError:
    return InterfaceError("SELECT 1", {}, DriverError(sqlstate), connection_invalidated=True)


def pool_timeout() -> PoolTimeoutError:
    """What a checkout raises once it waited POSTGRES_POOL_TIMEOUT_SECONDS in vain."""
    return PoolTimeoutError(
        "QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 10.00"
    )


TRANSIENT = [
    pytest.param(reported("55P03"), id="lock-not-granted-in-time"),
    pytest.param(reported("55P03", OperationalError), id="lock-not-granted-in-time-operational"),
    pytest.param(reported("40P01"), id="deadlock-victim"),
    pytest.param(reported("40P01", OperationalError), id="deadlock-victim-operational"),
    pytest.param(reported("40001"), id="serialization-failure"),
    pytest.param(reported("40001", OperationalError), id="serialization-failure-operational"),
    pytest.param(pool_timeout(), id="pool-exhausted"),
]
"""Postgres is up and one attempt did not get through, having changed nothing."""

LOST_CONNECTIONS = [
    pytest.param(ConnectionRefusedError("connect call failed"), id="refused"),
    pytest.param(ConnectionResetError("reset by peer"), id="reset"),
    pytest.param(BrokenPipeError(), id="broken-pipe"),
    pytest.param(socket.gaierror(8, "nodename nor servname provided"), id="unresolvable-host"),
    pytest.param(TimeoutError("connect timed out"), id="connect-timeout"),
    pytest.param(lost_connection(), id="connection-lost"),
    pytest.param(lost_connection("55P03"), id="connection-lost-while-waiting-for-a-lock"),
    pytest.param(lost_connection("40P01"), id="connection-lost-in-a-deadlock"),
    pytest.param(reported("08006"), id="connection-failure"),
    pytest.param(reported("08P01"), id="protocol-violation"),
    pytest.param(reported("57P01"), id="admin-shutdown"),
    pytest.param(reported("57P02"), id="crash-shutdown"),
    pytest.param(reported("57P03"), id="starting-up"),
    pytest.param(reported("53300", OperationalError), id="too-many-connections"),
]
"""Postgres cannot be reached or does not serve: every outage that is not transient."""

OUTAGES = [*LOST_CONNECTIONS, *TRANSIENT]

BUGS = [
    pytest.param(reported("23505", IntegrityError), id="unique-violation"),
    pytest.param(reported("23514", IntegrityError), id="check-violation"),
    pytest.param(reported("42501"), id="permission-denied"),
    pytest.param(reported("57014"), id="statement-cancelled"),
    pytest.param(reported("40002"), id="integrity-violation-at-commit"),
    pytest.param(reported("40003"), id="statement-completion-unknown"),
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


@pytest.mark.parametrize("error", TRANSIENT)
def test_an_attempt_that_did_not_get_through_while_postgres_is_up_is_told_apart(
    error: Exception,
) -> None:
    """A lock not granted in time, a deadlock victim, a concurrent update and a pool
    with no free connection: the database serves, and only the one attempt failed."""
    assert is_transient(error)
    assert is_unavailable(error), "to a request it is still an answer of 503"


@pytest.mark.parametrize("error", LOST_CONNECTIONS)
def test_a_lost_connection_is_not_transient(error: Exception) -> None:
    """Whoever swallows a transient error must not swallow this."""
    assert not is_transient(error)
    assert is_unavailable(error)


@pytest.mark.parametrize("error", BUGS)
def test_a_bug_is_not_transient(error: Exception) -> None:
    """A cancelled statement or a violated constraint in particular."""
    assert not is_transient(error)


def test_the_two_ways_postgres_ends_a_transaction_for_another_are_503_not_500() -> None:
    """SQLSTATE 40P01 and 40001. No pair of requests is known to deadlock; should one,
    its victim is told to send the request again instead of that something broke."""
    for sqlstate in ("40P01", "40001"):
        assert is_unavailable(reported(sqlstate)), sqlstate
        assert is_transient(reported(sqlstate)), sqlstate
    # The rest of class 40 is not: a statement whose completion is unknown may have
    # committed, so it is not safe to send again.
    assert not is_unavailable(reported("40003"))
