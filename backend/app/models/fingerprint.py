"""A fingerprint of the schema this code builds (SPEC §7, Appendix A.10).

A database is built by the revisions of `migrations/versions/` and changed by nothing
else: a change to a model comes with a revision that takes a database there. Postgres
does not say which models a database matches, and `alembic check` cannot see a changed
CHECK constraint or a changed enum member, which is a CHECK as well. So every revision
carries the fingerprint of the models it leads to and writes it into the database, as
the comment of the `alembic_version` table, and `python -m scripts.check_schema`
compares that comment with what the code computes. A model that changed without a
revision is refused there, before the API starts.

This module lives in `app.models` so that importing it registers every table on
`Base.metadata` first.
"""

import hashlib
from collections.abc import Sequence
from typing import Final

from sqlalchemy import MetaData, make_url
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import Base
from app.models.privileges import APP_ROLE_PRIVILEGES, CREATE_APP_ROLE, GRANT_CONNECT
from app.models.triggers import TRIGGER_STATEMENTS

HAND_WRITTEN_STATEMENTS: Final[tuple[str, ...]] = (
    *TRIGGER_STATEMENTS,
    CREATE_APP_ROLE,
    GRANT_CONNECT,
    *APP_ROLE_PRIVILEGES,
)
"""What a database is besides its tables: the functions and triggers, then the app role
and its privileges (as templates, so the fingerprint does not depend on a role's name)."""

DIALECT: Final[str] = "postgresql+asyncpg://"
"""The dialect the DDL is compiled for: the one every connection of this code uses."""

STORE_FINGERPRINT: Final[str] = "COMMENT ON TABLE alembic_version IS %L"
"""A template for Postgres' `format()`: takes the fingerprint."""

CLEAR_FINGERPRINT: Final[str] = "COMMENT ON TABLE alembic_version IS NULL"

READ_FINGERPRINT: Final[str] = "SELECT obj_description(to_regclass('alembic_version'), 'pg_class')"
"""NULL where the table does not exist or carries no comment."""


def schema_fingerprint(
    metadata: MetaData = Base.metadata, statements: Sequence[str] = HAND_WRITTEN_STATEMENTS
) -> str:
    """SHA-256, as hex, over everything a database of these models is made of.

    That is the CREATE TABLE and CREATE INDEX statements of `metadata` as SQLAlchemy
    compiles them for PostgreSQL, and `statements`. Tables go in by name and the indexes
    of a table in the order of their text, so the result depends on the definitions
    alone, not on the order modules were imported in.

    Any edit to a model that reaches its DDL changes the result, a reordering of two
    constraints included. The answer is the same in every case: a revision that carries
    the new fingerprint.
    """
    dialect = make_url(DIALECT).get_dialect()()
    ddl: list[str] = []
    for table in sorted(metadata.tables.values(), key=lambda table: table.key):
        ddl.append(str(CreateTable(table).compile(dialect=dialect)))
        ddl.extend(
            sorted(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)
        )
    return fingerprint_of((*ddl, *statements))


def fingerprint_of(statements: Sequence[str]) -> str:
    """SHA-256, as hex, over `statements` in their order, each without its outer blanks."""
    digest = hashlib.sha256()
    for statement in statements:
        digest.update(statement.strip().encode())
        # A separator no statement contains, so two lists cannot run into one text.
        digest.update(b"\x00")
    return digest.hexdigest()
