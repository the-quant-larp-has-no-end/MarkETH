"""Refuse a database that was built from other models than the ones in this code.

    uv run python -m scripts.check_schema

The schema has one revision and no upgrade path, so `alembic upgrade head` does nothing
to a database that is already built, whatever the models have become since. The
baseline revision leaves a fingerprint of what it built in the database
(`app.models.fingerprint`); this command computes the fingerprint of the code and
compares the two. Where they differ it prints why and exits with status 1, and the
answer is a wipe and a rebuild.

It runs after `alembic upgrade head`, as the owner role, and changes nothing.
"""

import asyncio
import sys
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.models.fingerprint import READ_FINGERPRINT, schema_fingerprint

SCHEMA_CHANGED: Final[str] = (
    "The schema changed since this database was built: wipe and rebuild it (make reset)."
)


async def stored_fingerprint(settings: Settings) -> str | None:
    """The fingerprint the baseline revision left in the database of `settings`.

    None where it left none: the database is empty, or something else built it.
    """
    engine = create_async_engine(
        settings.postgres.owner_url, poolclass=NullPool, hide_parameters=True
    )
    try:
        async with engine.connect() as connection:
            stored: str | None = await connection.scalar(text(READ_FINGERPRINT))
    finally:
        await engine.dispose()
    return stored


def main() -> None:
    stored = asyncio.run(stored_fingerprint(get_settings()))
    if stored != schema_fingerprint():
        sys.exit(SCHEMA_CHANGED)
    print("The database was built from the schema of this code.")


if __name__ == "__main__":
    main()
