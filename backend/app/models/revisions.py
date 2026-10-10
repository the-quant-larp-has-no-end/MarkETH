"""What a revision of `migrations/versions/` does that Alembic's `op` has no word for
(SPEC §7, Appendix A.10).

* `execute_formatted` runs a statement that names a role, a database or a password, with
  Postgres doing the quoting.
* `app_role` is the role the API and the relay connect as, from the Settings the
  migration runs with.
* `grant_app_role` gives that role what `app.models.privileges` says. The grants reach
  the tables that exist when they run, so a revision that creates a table ends with it.
* `store_fingerprint` writes the fingerprint a revision leads to into the database. It is
  the last step of every `upgrade`, and of every `downgrade` with the fingerprint of the
  revision before.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import Settings
from app.models.fingerprint import STORE_FINGERPRINT
from app.models.privileges import APP_ROLE_PRIVILEGES


def execute_formatted(template: str, *arguments: str) -> None:
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


def app_role() -> tuple[str, str]:
    """Name and password of the app role, from the Settings migrations/env.py ran with."""
    config = op.get_context().config
    assert config is not None
    settings = config.attributes["settings"]
    assert isinstance(settings, Settings)
    return settings.postgres.app_user, settings.postgres.app_password


def role_exists(role: str) -> bool:
    exists: bool = (
        op.get_bind()
        .execute(
            sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"),
            {"role": role},
        )
        .scalar_one()
    )
    return exists


def current_database() -> str:
    database: str = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    return database


def grant_app_role() -> None:
    """Give the app role what `APP_ROLE_PRIVILEGES` says, on every table that exists now.

    The statements grant on every table first and take back table by table, so running
    them again changes nothing for a table that had them and covers one that is new.
    """
    role, _ = app_role()
    for statement in APP_ROLE_PRIVILEGES:
        execute_formatted(statement, role)


def store_fingerprint(fingerprint: str) -> None:
    """Leave `fingerprint` in the database as the schema it now has."""
    execute_formatted(STORE_FINGERPRINT, fingerprint)
