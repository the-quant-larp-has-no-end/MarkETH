"""Baseline: the whole schema, built from the models (SPEC §7, Appendix A.7, A.10).

Revision ID: 0001
Revises:

The schema has this one revision. A change to a model is followed by a wipe and a
rebuild (`make reset`), not by a second revision: `scripts.check_schema` refuses a
database that was built from other models.

`upgrade` builds an empty database in five steps:

1. the tables, constraints and indexes, straight from `app.models`;
2. the four trigger functions and their triggers (`app.models.triggers`);
3. the four VIS_* system accounts (SPEC §7);
4. the app role and what it may do: everything except what the ledger keeps to the
   database (`app.models.privileges`);
5. the fingerprint of all of the above, as the comment of `alembic_version`
   (`app.models.fingerprint`).
"""

from collections.abc import Sequence
from uuid import uuid7

import sqlalchemy as sa
from alembic import op

from app.core.config import Settings
from app.core.enums import SYSTEM_ACCOUNT_KINDS
from app.db.base import Base
from app.models import Account
from app.models.fingerprint import CLEAR_FINGERPRINT, STORE_FINGERPRINT, schema_fingerprint
from app.models.privileges import (
    APP_ROLE_PRIVILEGES,
    CREATE_APP_ROLE,
    GRANT_CONNECT,
    REVOKE_APP_ROLE_PRIVILEGES,
    REVOKE_CONNECT,
)
from app.models.triggers import TRIGGER_FUNCTIONS, TRIGGER_STATEMENTS

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # checkfirst=False: a table that is already there fails the build, instead of being
    # kept as it is and passed off as the one the models describe.
    Base.metadata.create_all(bind, checkfirst=False)
    for statement in TRIGGER_STATEMENTS:
        op.execute(statement)
    bind.execute(
        sa.insert(Account), [{"id": uuid7(), "kind": kind} for kind in SYSTEM_ACCOUNT_KINDS]
    )
    _grant_app_role()
    _execute_formatted(STORE_FINGERPRINT, schema_fingerprint())


def downgrade() -> None:
    """Undo everything except the app role itself: it is cluster-wide and may be in use."""
    _revoke_app_role()
    op.execute(CLEAR_FINGERPRINT)
    # The triggers go with their tables, the functions they call do not.
    Base.metadata.drop_all(op.get_bind())
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")


def _grant_app_role() -> None:
    role, password = _app_role()
    if not _role_exists(role):
        _execute_formatted(CREATE_APP_ROLE, role, password)
    _execute_formatted(GRANT_CONNECT, _current_database(), role)
    for statement in APP_ROLE_PRIVILEGES:
        _execute_formatted(statement, role)


def _revoke_app_role() -> None:
    role, _ = _app_role()
    if not _role_exists(role):
        return
    for statement in REVOKE_APP_ROLE_PRIVILEGES:
        _execute_formatted(statement, role)
    _execute_formatted(REVOKE_CONNECT, _current_database(), role)


def _app_role() -> tuple[str, str]:
    """Name and password of the app role, from the Settings migrations/env.py ran with."""
    config = op.get_context().config
    assert config is not None
    settings = config.attributes["settings"]
    assert isinstance(settings, Settings)
    return settings.postgres.app_user, settings.postgres.app_password


def _role_exists(role: str) -> bool:
    exists: bool = (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role},
        )
        .scalar_one()
    )
    return exists


def _current_database() -> str:
    database: str = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    return database


def _execute_formatted(template: str, *arguments: str) -> None:
    """Run a statement that cannot take bind parameters, letting Postgres do the quoting.

    `template` is a format() string: %I takes an identifier, %L a literal. Role names,
    the database name and the password are therefore never spliced into SQL by hand.
    """
    bind = op.get_bind()
    parameters = {f"argument_{index}": argument for index, argument in enumerate(arguments)}
    placeholders = "".join(f", CAST(:{name} AS text)" for name in parameters)
    statement: str = bind.execute(
        sa.text(f"SELECT format(CAST(:template AS text){placeholders})"),
        {"template": template, **parameters},
    ).scalar_one()
    bind.exec_driver_sql(statement)
