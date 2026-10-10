"""The engine the application talks to Postgres with: what a failed statement shows, and
how long a request waits for a connection (SPEC §7, §9).

Nothing here connects: an engine is built, looked at and thrown away.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from app.core.config import Settings
from app.db.database import Database, is_transient, is_unavailable
from scripts import check_schema
from tests.support.database import upgrade_database

ADDRESS = "zoe.zuercher@ethz.ch"
CODE = "7KQ2M9XAW3RT"
STATEMENT = "INSERT INTO users (email, name) VALUES ($1::VARCHAR, $2::VARCHAR)"


class EngineAskedFor(Exception):
    """Raised by the stand-in below in place of building an engine."""


def recording(seen: list[tuple[str, dict[str, Any]]]) -> Callable[..., AsyncEngine]:
    """Stands in for `create_async_engine`: remembers what it was asked for and stops
    there, before anything could connect."""

    def create(url: str, **options: Any) -> AsyncEngine:
        seen.append((url, options))
        raise EngineAskedFor

    return create


async def test_the_engine_hides_the_values_of_a_failed_statement(base_settings: Settings) -> None:
    """A statement error carries its SQL and, unless told otherwise, the values it was
    sent with: the address of whoever called, a name, a code that redeems. The API logs
    such an error with its traceback, so the engine is told to leave the values out."""
    database = Database(base_settings.postgres.app_url, base_settings.postgres)
    try:
        engine = database.engine.sync_engine
        assert engine.hide_parameters is True

        failed = DBAPIError.instance(
            STATEMENT,
            (ADDRESS, "Zo\u00eb Z\u00fcrcher", CODE),
            RuntimeError("duplicate key value violates unique constraint"),
            Exception,
            hide_parameters=engine.hide_parameters,
        )
    finally:
        await database.dispose()

    text = str(failed)
    assert STATEMENT in text, "the statement is what a reader of the log needs"
    assert "[SQL parameters hidden due to hide_parameters=True]" in text
    for value in (ADDRESS, "Z\u00fcrcher", CODE):
        assert value not in text
        assert value not in repr(failed)


def test_an_error_made_without_the_flag_would_show_the_values() -> None:
    """What the flag is for: without it the line in the log holds the values."""
    shown = DBAPIError.instance(
        STATEMENT, (ADDRESS, CODE), RuntimeError("boom"), Exception, hide_parameters=False
    )

    assert ADDRESS in str(shown)
    assert CODE in str(shown)


def test_the_migration_environment_hides_the_values_of_a_failed_statement(
    base_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`alembic upgrade head` connects as the owner role with an engine of its own. The
    environment is run up to the point where it asks for that engine."""
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr("sqlalchemy.ext.asyncio.create_async_engine", recording(seen))

    with pytest.raises(EngineAskedFor):
        upgrade_database(base_settings)

    ((url, options),) = seen
    assert url == base_settings.postgres.owner_url
    assert options["hide_parameters"] is True


async def test_the_schema_check_hides_the_values_of_a_failed_statement(
    base_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(check_schema, "create_async_engine", recording(seen))

    with pytest.raises(EngineAskedFor):
        await check_schema.stored_fingerprint(base_settings)

    ((url, options),) = seen
    assert url == base_settings.postgres.owner_url
    assert options["hide_parameters"] is True


@pytest.mark.parametrize("seconds", [10, 3, 1])
async def test_a_request_waits_for_a_connection_as_long_as_the_settings_say(
    base_settings: Settings, seconds: int
) -> None:
    """POSTGRES_POOL_TIMEOUT_SECONDS is the checkout timeout of the pool, beside its
    size and its overflow. Without it a request would wait SQLAlchemy's own 30 s."""
    assert base_settings.postgres.pool_timeout_seconds == 10, "the value of .example.env"
    postgres = base_settings.postgres.model_copy(update={"pool_timeout_seconds": seconds})
    database = Database(postgres.app_url, postgres)
    try:
        pool = database.engine.sync_engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.timeout() == seconds
        assert pool.size() == postgres.pool_size
    finally:
        await database.dispose()


def test_a_checkout_that_times_out_is_unavailable_and_transient() -> None:
    """The error SQLAlchemy's own pool raises when no connection came free in time, made
    by that pool: one connection allowed, taken by a stand-in, and no time to wait."""
    pool = QueuePool(Mock, pool_size=1, max_overflow=0, timeout=0.01)
    held = pool.connect()
    try:
        with pytest.raises(PoolTimeoutError) as refused:
            pool.connect()
    finally:
        held.close()
        pool.dispose()

    assert is_unavailable(refused.value)
    assert is_transient(refused.value)
