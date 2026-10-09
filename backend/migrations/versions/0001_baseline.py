"""Baseline: the whole schema of SPEC §7 with the additions of Appendix A.7.

Revision ID: 0001
Revises:
Create Date: 2026-10-09 19:19:13.929353

`_create_tables` and `_drop_tables` are Alembic's autogenerate output for `app.models`.
Three things autogenerate cannot express are written by hand around them:

* the deferred constraint trigger that rejects a journal entry whose postings do not
  sum to zero (SPEC §7),
* the four VIS_* system accounts (SPEC §7),
* the app role and its privileges: everything except UPDATE and DELETE on the ledger
  tables (A.10).
"""

from collections.abc import Sequence
from uuid import uuid7

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import Settings

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Values are spelled out, not imported from app.core.enums: a migration describes the
# schema as it was at this revision, whatever the code looks like later.
SYSTEM_ACCOUNT_KINDS = ("VIS_ISSUANCE", "VIS_MM_PNL", "VIS_REDEMPTION_PENDING", "VIS_REDEEMED")

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
SEQUENCE_PRIVILEGES = "USAGE, SELECT"

CREATE_BALANCE_FUNCTION = """
CREATE FUNCTION assert_journal_entry_balanced() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    affected uuid[];
    unbalanced record;
BEGIN
    affected := CASE TG_OP
        WHEN 'INSERT' THEN ARRAY[NEW.entry_id]
        WHEN 'DELETE' THEN ARRAY[OLD.entry_id]
        ELSE ARRAY[OLD.entry_id, NEW.entry_id]
    END;

    SELECT entry_id, sum(amount_micro) AS total
    INTO unbalanced
    FROM postings
    WHERE entry_id = ANY (affected)
    GROUP BY entry_id
    HAVING sum(amount_micro) <> 0
    LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION 'journal entry % is unbalanced: its postings sum to % micro-units',
            unbalanced.entry_id, unbalanced.total
            USING ERRCODE = 'check_violation',
                  TABLE = 'postings',
                  CONSTRAINT = 'postings_entry_balanced';
    END IF;
    RETURN NULL;
END;
$$
"""

# Deferred to commit: an entry is unbalanced while its postings are being inserted. It
# fires per posting and re-sums the entry each time, which is quadratic in the legs of
# one entry; that is the price of a check no insert order can slip past.
CREATE_BALANCE_TRIGGER = """
CREATE CONSTRAINT TRIGGER postings_entry_balanced
AFTER INSERT OR UPDATE OR DELETE ON postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balanced()
"""


def upgrade() -> None:
    _create_tables()
    op.execute(CREATE_BALANCE_FUNCTION)
    op.execute(CREATE_BALANCE_TRIGGER)
    _seed_system_accounts()
    _grant_app_role()


def downgrade() -> None:
    """Undo everything except the app role itself: it is cluster-wide and may be in use."""
    _revoke_app_role()
    op.execute("DROP TRIGGER postings_entry_balanced ON postings")
    op.execute("DROP FUNCTION assert_journal_entry_balanced()")
    _drop_tables()


def _seed_system_accounts() -> None:
    accounts = sa.table(
        "accounts",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.String()),
        sa.column("balance_micro", sa.BigInteger()),
    )
    op.bulk_insert(
        accounts,
        [{"id": uuid7(), "kind": kind, "balance_micro": 0} for kind in SYSTEM_ACCOUNT_KINDS],
    )


def _grant_app_role() -> None:
    role, password = _app_role()
    if not _role_exists(role):
        _execute_formatted("CREATE ROLE %I LOGIN PASSWORD %L", role, password)
    _execute_formatted("GRANT CONNECT ON DATABASE %I TO %I", _current_database(), role)
    _execute_formatted("GRANT USAGE ON SCHEMA public TO %I", role)
    _execute_formatted(f"GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO %I", role)
    _execute_formatted(f"GRANT {SEQUENCE_PRIVILEGES} ON ALL SEQUENCES IN SCHEMA public TO %I", role)
    # Tables and sequences of later revisions, which the owner role will create.
    _execute_formatted(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {TABLE_PRIVILEGES} ON TABLES TO %I",
        role,
    )
    _execute_formatted(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {SEQUENCE_PRIVILEGES} ON SEQUENCES TO %I",
        role,
    )
    # SPEC §7: the ledger is append-only for the app.
    _execute_formatted("REVOKE UPDATE, DELETE ON journal_entries, postings FROM %I", role)
    # "ALL TABLES" included Alembic's own bookkeeping; the app may read it, never write it.
    _execute_formatted("REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM %I", role)


def _revoke_app_role() -> None:
    role, _ = _app_role()
    if not _role_exists(role):
        return
    _execute_formatted(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE {SEQUENCE_PRIVILEGES} ON SEQUENCES FROM %I",
        role,
    )
    _execute_formatted(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE {TABLE_PRIVILEGES} ON TABLES FROM %I",
        role,
    )
    _execute_formatted("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I", role)
    _execute_formatted("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I", role)
    _execute_formatted("REVOKE USAGE ON SCHEMA public FROM %I", role)
    _execute_formatted("REVOKE CONNECT ON DATABASE %I FROM %I", _current_database(), role)


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


def _create_tables() -> None:
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "SIGNUP_GRANT",
                "MONTHLY_GRANT",
                "CODE_REDEMPTION",
                "TRADE_BUY",
                "TRADE_SELL",
                "MARKET_RESOLUTION",
                "MARKET_VOID",
                "COFFEE_TICKET_ISSUE",
                "COFFEE_TICKET_REDEEM",
                "COFFEE_TICKET_REFUND",
                name="kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("ref_type", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entries")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_journal_entries_idempotency_key")),
    )
    op.create_table(
        "login_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_login_tokens_token_hash_sha256_hex")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_tokens_token_hash")),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "topic",
            sa.Enum(
                "quote.updated", name="topic", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "payload", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_email", sa.String(length=320), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "USER",
                "ADMIN",
                "BARISTA",
                name="role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "SUSPENDED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("canonical_email", name=op.f("uq_users_canonical_email")),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "INFO",
                "WARNING",
                "CRITICAL",
                name="severity",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "context", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_by", sa.Uuid(), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(acked_by IS NULL) = (acked_at IS NULL)", name=op.f("ck_alerts_acked_by_iff_acked_at")
        ),
        sa.ForeignKeyConstraint(["acked_by"], ["users.id"], name=op.f("fk_alerts_acked_by_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column(
            "before", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "after", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_log_actor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_table(
        "coffee_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "REDEEMED",
                "EXPIRED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("value_micro", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'REDEEMED') = (redeemed_by IS NOT NULL) AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)",
            name=op.f("ck_coffee_tickets_redeemed_iff_redeemer"),
        ),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_coffee_tickets_code_hash_sha256_hex")
        ),
        sa.CheckConstraint("value_micro > 0", name=op.f("ck_coffee_tickets_value_positive")),
        sa.ForeignKeyConstraint(
            ["redeemed_by"], ["users.id"], name=op.f("fk_coffee_tickets_redeemed_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_coffee_tickets_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coffee_tickets")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_coffee_tickets_code_hash")),
    )
    op.create_index(
        op.f("ix_coffee_tickets_user_id_status"),
        "coffee_tickets",
        ["user_id", "status"],
        unique=False,
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "response_json",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_idempotency_keys_user_id_users")
        ),
        sa.PrimaryKeyConstraint("user_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_table(
        "markets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("yes_def", sa.Text(), nullable=False),
        sa.Column("no_def", sa.Text(), nullable=False),
        sa.Column("resolution_criteria", sa.Text(), nullable=False),
        sa.Column("resolution_source", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PROPOSED",
                "REJECTED",
                "OPEN",
                "HALTED",
                "CLOSED",
                "RESOLVED",
                "VOID",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "requested_tier",
            sa.Enum(
                "TIGHT",
                "DEFAULT",
                "MEDIUM",
                "CUSTOM",
                name="requested_tier",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "tier",
            sa.Enum(
                "TIGHT",
                "DEFAULT",
                "MEDIUM",
                "CUSTOM",
                name="tier",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("budget_micro", sa.BigInteger(), nullable=False),
        sa.Column("b_micro", sa.BigInteger(), nullable=False),
        sa.Column("q_yes_micro", sa.BigInteger(), nullable=False),
        sa.Column("q_no_micro", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("volume_micro", sa.BigInteger(), nullable=False),
        sa.Column("close_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolve_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PROPOSED', 'REJECTED') OR b_micro > 0",
            name=op.f("ck_markets_b_positive_once_approved"),
        ),
        sa.CheckConstraint("b_micro >= 0", name=op.f("ck_markets_b_non_negative")),
        sa.CheckConstraint("budget_micro >= 0", name=op.f("ck_markets_budget_non_negative")),
        sa.CheckConstraint("q_no_micro >= 0", name=op.f("ck_markets_q_no_non_negative")),
        sa.CheckConstraint("q_yes_micro >= 0", name=op.f("ck_markets_q_yes_non_negative")),
        sa.CheckConstraint("version >= 0", name=op.f("ck_markets_version_non_negative")),
        sa.CheckConstraint("volume_micro >= 0", name=op.f("ck_markets_volume_non_negative")),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name=op.f("fk_markets_approved_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_markets_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_markets")),
    )
    op.create_index(
        op.f("ix_markets_created_by_created_at"),
        "markets",
        ["created_by", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_markets_status_close_at"), "markets", ["status", "close_at"], unique=False
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id ~ '^[0-9a-f]{64}$'", name=op.f("ck_sessions_id_sha256_hex")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_table(
        "voucher_code_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("value_micro", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("one_per_user", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("count > 0", name=op.f("ck_voucher_code_batches_count_positive")),
        sa.CheckConstraint("value_micro > 0", name=op.f("ck_voucher_code_batches_value_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_voucher_code_batches_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voucher_code_batches")),
    )
    op.create_table(
        "voucher_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period = 'SIGNUP' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name=op.f("ck_voucher_grants_period_format"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_voucher_grants_entry_id_journal_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_voucher_grants_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voucher_grants")),
        sa.UniqueConstraint("user_id", "period", name=op.f("uq_voucher_grants_user_id_period")),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "USER",
                "MARKET",
                "VIS_ISSUANCE",
                "VIS_MM_PNL",
                "VIS_REDEMPTION_PENDING",
                "VIS_REDEEMED",
                name="kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("market_id", sa.Uuid(), nullable=True),
        sa.Column("balance_micro", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'MARKET') = (market_id IS NOT NULL)",
            name=op.f("ck_accounts_market_iff_market"),
        ),
        sa.CheckConstraint(
            "(kind = 'USER') = (owner_id IS NOT NULL)", name=op.f("ck_accounts_owner_iff_user")
        ),
        sa.CheckConstraint(
            "kind <> 'USER' OR balance_micro >= 0",
            name=op.f("ck_accounts_user_balance_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name=op.f("fk_accounts_market_id_markets")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_accounts_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
    )
    op.create_index(
        "uq_accounts_market_id",
        "accounts",
        ["market_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'MARKET'"),
    )
    op.create_index(
        "uq_accounts_owner_id",
        "accounts",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'USER'"),
    )
    op.create_index(
        "uq_accounts_system_kind",
        "accounts",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind NOT IN ('USER', 'MARKET')"),
    )
    op.create_table(
        "positions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "YES", "NO", name="outcome", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("shares_micro", sa.BigInteger(), nullable=False),
        sa.Column("cost_micro", sa.BigInteger(), nullable=False),
        sa.Column("proceeds_micro", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("cost_micro >= 0", name=op.f("ck_positions_cost_non_negative")),
        sa.CheckConstraint("proceeds_micro >= 0", name=op.f("ck_positions_proceeds_non_negative")),
        sa.CheckConstraint("shares_micro >= 0", name=op.f("ck_positions_shares_non_negative")),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name=op.f("fk_positions_market_id_markets")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_positions_user_id_users")),
        sa.PrimaryKeyConstraint("user_id", "market_id", "outcome", name=op.f("pk_positions")),
    )
    op.create_index(op.f("ix_positions_market_id"), "positions", ["market_id"], unique=False)
    op.create_table(
        "resolutions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "YES",
                "NO",
                "VOID",
                name="outcome",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name=op.f("fk_resolutions_market_id_markets")
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], name=op.f("fk_resolutions_resolved_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolutions")),
        sa.UniqueConstraint("market_id", name=op.f("uq_resolutions_market_id")),
    )
    op.create_table(
        "trades",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("market_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "side",
            sa.Enum(
                "BUY", "SELL", name="side", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "YES", "NO", name="outcome", native_enum=False, create_constraint=True, length=32
            ),
            nullable=False,
        ),
        sa.Column("shares_micro", sa.BigInteger(), nullable=False),
        sa.Column("amount_micro", sa.BigInteger(), nullable=False),
        sa.Column("p_yes_before_micro", sa.BigInteger(), nullable=False),
        sa.Column("p_yes_after_micro", sa.BigInteger(), nullable=False),
        sa.Column("market_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_micro >= 0", name=op.f("ck_trades_amount_non_negative")),
        sa.CheckConstraint("shares_micro > 0", name=op.f("ck_trades_shares_positive")),
        sa.ForeignKeyConstraint(
            ["market_id"], ["markets.id"], name=op.f("fk_trades_market_id_markets")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_trades_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trades")),
    )
    op.create_index(
        op.f("ix_trades_market_id_created_at"), "trades", ["market_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_trades_user_id_id"), "trades", ["user_id", "id"], unique=False)
    op.create_table(
        "voucher_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "REDEEMED",
                "REVOKED",
                name="status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("redeemed_by", sa.Uuid(), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(status = 'REDEEMED') = (redeemed_by IS NOT NULL) AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)",
            name=op.f("ck_voucher_codes_redeemed_iff_redeemer"),
        ),
        sa.CheckConstraint(
            "code_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_voucher_codes_code_hash_sha256_hex")
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["voucher_code_batches.id"],
            name=op.f("fk_voucher_codes_batch_id_voucher_code_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_voucher_codes_entry_id_journal_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_by"], ["users.id"], name=op.f("fk_voucher_codes_redeemed_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voucher_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_voucher_codes_code_hash")),
    )
    op.create_index(
        op.f("ix_voucher_codes_batch_id_status"),
        "voucher_codes",
        ["batch_id", "status"],
        unique=False,
    )
    op.create_table(
        "postings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount_micro", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("amount_micro <> 0", name=op.f("ck_postings_amount_non_zero")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_postings_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["journal_entries.id"], name=op.f("fk_postings_entry_id_journal_entries")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_postings")),
    )
    op.create_index(
        op.f("ix_postings_account_id_id"), "postings", ["account_id", "id"], unique=False
    )
    op.create_index(op.f("ix_postings_entry_id"), "postings", ["entry_id"], unique=False)


def _drop_tables() -> None:
    op.drop_index(op.f("ix_postings_entry_id"), table_name="postings")
    op.drop_index(op.f("ix_postings_account_id_id"), table_name="postings")
    op.drop_table("postings")
    op.drop_index(op.f("ix_voucher_codes_batch_id_status"), table_name="voucher_codes")
    op.drop_table("voucher_codes")
    op.drop_index(op.f("ix_trades_user_id_id"), table_name="trades")
    op.drop_index(op.f("ix_trades_market_id_created_at"), table_name="trades")
    op.drop_table("trades")
    op.drop_table("resolutions")
    op.drop_index(op.f("ix_positions_market_id"), table_name="positions")
    op.drop_table("positions")
    op.drop_index(
        "uq_accounts_system_kind",
        table_name="accounts",
        postgresql_where=sa.text("kind NOT IN ('USER', 'MARKET')"),
    )
    op.drop_index(
        "uq_accounts_owner_id", table_name="accounts", postgresql_where=sa.text("kind = 'USER'")
    )
    op.drop_index(
        "uq_accounts_market_id", table_name="accounts", postgresql_where=sa.text("kind = 'MARKET'")
    )
    op.drop_table("accounts")
    op.drop_table("voucher_grants")
    op.drop_table("voucher_code_batches")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(op.f("ix_markets_status_close_at"), table_name="markets")
    op.drop_index(op.f("ix_markets_created_by_created_at"), table_name="markets")
    op.drop_table("markets")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_coffee_tickets_user_id_status"), table_name="coffee_tickets")
    op.drop_table("coffee_tickets")
    op.drop_table("audit_log")
    op.drop_table("alerts")
    op.drop_table("users")
    op.drop_index(
        "ix_outbox_unpublished",
        table_name="outbox",
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.drop_table("outbox")
    op.drop_table("login_tokens")
    op.drop_table("journal_entries")
