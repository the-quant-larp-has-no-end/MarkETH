"""What the app role may do (SPEC §7, Appendix A.7, A.10).

The API and the relay connect as the app role; the owner role builds the schema and does
nothing else. The baseline revision (`migrations/versions/0001_baseline.py`) holds these
statements as they were first deployed, and `app.models.fingerprint` hashes them, so an
edit here asks for a revision that grants or revokes the same in a database.

Each statement is a template for Postgres' `format()`: %I takes an identifier and %L a
literal. A role name, a database name or a password is therefore never spliced into SQL
by hand.

The grants reach the tables and sequences that exist when they run. Nothing is granted
by default on an object created later: a revision that creates a table runs them again
(`app.models.revisions.grant_app_role`), and a table created by hand is out of the app
role's reach until it is granted.
"""

from typing import Final

TABLE_PRIVILEGES: Final[str] = "SELECT, INSERT, UPDATE, DELETE"
SEQUENCE_PRIVILEGES: Final[str] = "USAGE, SELECT"

ACCOUNT_COLUMNS_THE_APP_SETS: Final[str] = "id, kind, owner_id, market_id"
ENTRY_COLUMNS_THE_APP_SETS: Final[str] = "id, kind, ref_type, ref_id, idempotency_key, created_at"
"""Both lists are spelled out: a column added to either table is out of the app role's
reach, and its INSERT fails with "permission denied", until someone decides here whether
the app may write it."""

CREATE_APP_ROLE: Final[str] = "CREATE ROLE %I LOGIN PASSWORD %L"
"""Takes the role and its password. Run only where the role is missing: a role belongs
to the cluster, not to one database."""

GRANT_CONNECT: Final[str] = "GRANT CONNECT ON DATABASE %I TO %I"
"""Takes the database and the role."""

APP_ROLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "GRANT USAGE ON SCHEMA public TO %I",
    f"GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO %I",
    f"GRANT {SEQUENCE_PRIVILEGES} ON ALL SEQUENCES IN SCHEMA public TO %I",
    # SPEC §7: the ledger is append-only for the app.
    "REVOKE UPDATE, DELETE ON journal_entries, postings FROM %I",
    # "ALL TABLES" includes Alembic's own bookkeeping; the app may read it, never write it.
    "REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM %I",
    # A.10: `accounts.balance_micro` and `journal_entries.created_xid` are written by the
    # database alone. A column privilege cannot subtract from a table privilege, so the
    # table privileges go and the columns the app does set are granted back. UPDATE on
    # `accounts.id` moves nothing (the foreign key of `postings` pins the id of every
    # account that was ever posted to); Postgres asks for an UPDATE privilege on some
    # column before it lets a role lock a row, and the services lock accounts.
    "REVOKE INSERT, UPDATE, DELETE ON accounts FROM %I",
    f"GRANT INSERT ({ACCOUNT_COLUMNS_THE_APP_SETS}), UPDATE (id) ON accounts TO %I",
    "REVOKE INSERT ON journal_entries FROM %I",
    f"GRANT INSERT ({ENTRY_COLUMNS_THE_APP_SETS}) ON journal_entries TO %I",
    # A question is marked removed and never erased.
    "REVOKE DELETE ON questions FROM %I",
    # A chat message is never edited: its row exists exactly while it can be read.
    "REVOKE UPDATE ON chat_messages FROM %I",
    # A broadcast stays: what was asked, decided and ended is the record of it.
    "REVOKE DELETE ON broadcasts FROM %I",
)
"""Each takes the role. The order matters: what is granted on every table comes first,
then what is taken back table by table."""

REVOKE_APP_ROLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I",
    # Takes the column privileges with it.
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I",
    "REVOKE USAGE ON SCHEMA public FROM %I",
)
"""Each takes the role. Undoes `APP_ROLE_PRIVILEGES`; the role itself stays."""

REVOKE_CONNECT: Final[str] = "REVOKE CONNECT ON DATABASE %I FROM %I"
"""Takes the database and the role."""
