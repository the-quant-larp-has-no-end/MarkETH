"""Bring a test Postgres to a known state: migrate it, check it, empty it.

The Alembic helpers are synchronous and safe to call from anywhere, including from
inside a running event loop: `migrations/env.py` starts its own loop with `asyncio.run`,
so each command runs in a worker thread and the caller blocks until it is done.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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


def upgrade_database(settings: Settings, revision: str = "head") -> None:
    """`alembic upgrade`, as the owner role of `settings`."""
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.upgrade(config, revision))


def downgrade_database(settings: Settings, revision: str = "base") -> None:
    """`alembic downgrade`, as the owner role of `settings`."""
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.downgrade(config, revision))


def check_database_matches_models(settings: Settings) -> None:
    """`alembic check`: raises `CommandError` if autogenerate would still change the schema."""
    config = alembic_config(settings)
    _in_worker_thread(lambda: command.check(config))


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
