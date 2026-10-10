"""Bring a test Postgres to a known state: build its schema, check it, empty it.

The Alembic helpers are synchronous and safe to call from anywhere, including from
inside a running event loop: `migrations/env.py` starts its own loop with `asyncio.run`,
so each command runs in a worker thread and the caller blocks until it is done.
"""

from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final
from uuid import uuid7

from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.enums import SYSTEM_ACCOUNT_KINDS
from app.db.base import Base
from app.models import Account

BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

LOCK_TIMEOUT: Final[str] = "10s"
"""How long a reset waits for a table lock before it fails.

A test that leaked an open transaction would otherwise hang the whole run here.
"""


def alembic_config(settings: Settings) -> Config:
    """The repository's Alembic configuration, connecting with `settings` instead of `.env`."""
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.attributes["settings"] = settings
    return config


def upgrade_database(settings: Settings) -> None:
    """`alembic upgrade head`, as the owner role of `settings`: builds the whole schema."""
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.upgrade(config, "head"))


def downgrade_database(settings: Settings) -> None:
    """`alembic downgrade base`, as the owner role of `settings`: removes the whole schema."""
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.downgrade(config, "base"))


def check_database_matches_models(settings: Settings) -> None:
    """`alembic check`: raises `CommandError` where the models and the schema differ.

    Alembic does not compare CHECK constraints, and an enum column is a VARCHAR with
    one. A change there is seen by the fingerprint instead (`app.models.fingerprint`).
    """
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.check(config))


@asynccontextmanager
async def scratch_database(
    owner_engine: AsyncEngine, settings: Settings, name: str
) -> AsyncIterator[Settings]:
    """An empty database beside the one the tests share, dropped on the way out.

    Yields `settings` pointing at it. For a test that builds or removes a whole schema,
    which must not happen to the database every other test runs on.
    """
    async with owner_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"CREATE DATABASE {name}"))
    try:
        yield settings.model_copy(
            update={"postgres": settings.postgres.model_copy(update={"database": name})}
        )
    finally:
        async with owner_engine.connect() as connection:
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await autocommit.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


async def reset_database(owner_engine: AsyncEngine) -> None:
    """Empty every table and restart every identity; keep the system accounts at balance 0.

    The four VIS_* accounts keep their ids, so anything that remembers one stays valid
    from test to test. One that a test removed is put back.
    """
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    system_accounts = select(Account.kind, Account.id).where(Account.kind.in_(SYSTEM_ACCOUNT_KINDS))
    async with owner_engine.begin() as connection:
        await connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        ids = {row.kind: row.id for row in await connection.execute(system_accounts)}
        await connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY"))
        await connection.execute(
            insert(Account),
            [
                {"id": ids.get(kind) or uuid7(), "kind": kind, "balance_micro": 0}
                for kind in SYSTEM_ACCOUNT_KINDS
            ],
        )


def _in_worker_thread(function: Callable[[], None]) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(function).result()
