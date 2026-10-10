"""Refuse a database whose schema is not the one of the models in this code.

    uv run python -m scripts.check_schema

`alembic upgrade head` takes a database through the revisions of `migrations/versions/`
and no further: where a model changed and no revision came with it, the database stays
as it was, whatever the code expects. Every revision leaves the fingerprint of the
models it leads to in the database (`app.models.fingerprint`); this command computes the
fingerprint of the code and compares the two. Where they differ it prints why and exits
with status 1, and the answer is a revision (`make revision NAME=...`).

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
    "The models of this code are not the schema of this database, and no revision leads "
    "there: write one (make revision NAME=...)."
)


async def stored_fingerprint(settings: Settings) -> str | None:
    """The fingerprint the last revision left in the database of `settings`.

    None where none left one: the database is empty, or something else built it.
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
    print("The database has the schema of this code.")


if __name__ == "__main__":
    main()
