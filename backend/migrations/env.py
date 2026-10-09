"""Alembic environment: migrations run over asyncpg as the owner role (SPEC §7, A.10).

The connection comes from `Settings.postgres.owner_url`. A caller that drives Alembic
from Python (the test harness) may point it elsewhere by putting its own `Settings` into
`config.attributes["settings"]`; the command line uses the repository's `.env`.
Revision scripts read the same object from there, so a migration and its connection can
never disagree about which roles exist.
"""

import asyncio
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import CheckConstraint, Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.base import AwareDateTime, Base

SETTINGS_ATTRIBUTE = "settings"

config = context.config


def _settings() -> Settings:
    settings = config.attributes.get(SETTINGS_ATTRIBUTE)
    if settings is None:
        settings = get_settings()
        config.attributes[SETTINGS_ATTRIBUTE] = settings
    assert isinstance(settings, Settings)
    return settings


def _render_item(
    type_: str, item: object, autogen_context: AutogenContext
) -> str | Literal[False] | None:
    """How autogenerate writes two things it would otherwise get wrong.

    The CHECK of an enum column is left to the `sa.Enum(create_constraint=True)` it
    renders. Alembic means to skip such type-bound constraints itself, but does not
    recognise them under SQLAlchemy 2.1 and would write each one out twice more. None is
    its own "skip". A timestamp column is written as the plain SQL type.
    """
    if type_ == "check" and isinstance(item, CheckConstraint) and item._type_bound:
        return None
    if type_ == "type" and isinstance(item, AwareDateTime):
        # A revision records the column type, not the Python-side guard around it.
        return "sa.DateTime(timezone=True)"
    return False


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_item=_render_item,  # type: ignore[arg-type]
    )
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
        "Offline (--sql) mode is not supported: the baseline migration asks the server "
        "whether the app role exists."
    )

if config.cmd_opts is not None:
    # Started from the alembic command line: log like the API and the relay do. A caller
    # inside another process keeps its own logging setup.
    configure_logging(_settings().app.log_level)

asyncio.run(_run_online())
