"""Alembic environment: the schema is built over asyncpg as the owner role (SPEC §7, A.10).

The connection comes from `Settings.postgres.owner_url`. A caller that drives Alembic
from Python (the test harness) may point it elsewhere by putting its own `Settings` into
`config.attributes["settings"]`; the command line uses the repository's `.env`.
The baseline revision reads the same object from there, so the revision and its
connection can never disagree about which roles exist.

`Base.metadata` is what the baseline builds the tables from and what `alembic check`
compares a database with.
"""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.base import Base

SETTINGS_ATTRIBUTE = "settings"

config = context.config


def _settings() -> Settings:
    settings = config.attributes.get(SETTINGS_ATTRIBUTE)
    if settings is None:
        settings = get_settings()
        config.attributes[SETTINGS_ATTRIBUTE] = settings
    assert isinstance(settings, Settings)
    return settings


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_settings().postgres.owner_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError(
        "Offline (--sql) mode is not supported: the baseline revision asks the server "
        "whether the app role exists."
    )

if config.cmd_opts is not None:
    # Started from the alembic command line: log like the API and the relay do. A caller
    # inside another process keeps its own logging setup.
    configure_logging(_settings().app.log_level)

asyncio.run(_run_online())
