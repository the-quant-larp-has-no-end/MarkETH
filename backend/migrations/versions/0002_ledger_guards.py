"""Ledger guards: the database keeps balances, seals entries and backs up more rules.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 22:05:00.000000

Written by hand; autogenerate offers none of it but the column, the index and the
unique constraint (SPEC §1, §5, §7, §8, Appendix A.7, A.10).

* `accounts.balance_micro` is kept by the database. A trigger adds every posting to its
  account, and the app role may no longer write the column, open an account with a
  balance or delete one. A balance therefore cannot move without a posting, nor a
  posting without its balance.
* A journal entry is sealed by the transaction that created it
  (`journal_entries.created_xid`): a later transaction cannot add postings to it.
* The balance check sums an entry once, from its last posting, instead of once per
  posting. That is sound only because of the seal: every posting of an entry is
  inserted by one transaction, so its last one sees them all.
* More CHECKs: a market account and the two coffee ticket accounts never go negative,
  b · ln 2 fits the budget, `idempotency_keys.request_hash` is a SHA-256.
* A market's budget and b cannot change while it is OPEN.
* Each trade of a market has its own `market_version`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.config import Settings

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TRANSACTION_ID = "(pg_current_xact_id())::text::bigint"

# ln 2 cut off after 30 places, so a budget of exactly ceil(b · ln 2) always passes.
LOSS_BOUND_WITHIN_BUDGET = "b_micro * 0.693147180559945309417232121458 <= budget_micro"

ACCOUNT_COLUMNS_THE_APP_SETS = "id, kind, owner_id, market_id"
ENTRY_COLUMNS_THE_APP_SETS = "id, kind, ref_type, ref_id, idempotency_key, created_at"

# SECURITY DEFINER: it runs as the owner role, which is how a posting inserted by the
# app role reaches a column the app role cannot write. Tables are schema-qualified and
# the search path is pinned, as for every function that runs with its owner's rights.
#
# The balance follows INSERT, UPDATE and DELETE alike, so it equals the sum of the
# account's postings whoever changes them; the app role can only INSERT.
#
# An entry that does not exist is left to the foreign key to report.
CREATE_APPLY_FUNCTION = f"""
CREATE FUNCTION apply_posting() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    creator bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT created_xid INTO creator FROM public.journal_entries WHERE id = NEW.entry_id;
        IF FOUND AND creator <> {CURRENT_TRANSACTION_ID} THEN
            RAISE EXCEPTION 'journal entry % is sealed: only the transaction that created it may add postings',
                NEW.entry_id
                USING ERRCODE = 'check_violation',
                      TABLE = 'postings',
                      CONSTRAINT = 'postings_entry_sealed';
        END IF;
    END IF;

    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        UPDATE public.accounts
        SET balance_micro = balance_micro - OLD.amount_micro
        WHERE id = OLD.account_id;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        UPDATE public.accounts
        SET balance_micro = balance_micro + NEW.amount_micro
        WHERE id = NEW.account_id;
    END IF;
    RETURN NULL;
END;
$$
"""

CREATE_APPLY_TRIGGER = """
CREATE TRIGGER postings_apply
AFTER INSERT OR UPDATE OR DELETE ON postings
FOR EACH ROW EXECUTE FUNCTION apply_posting()
"""

# The deferred check still fires once per posting at commit, but an inserted posting
# that is not the last of its entry returns after one index probe on (entry_id, id).
# Measured on Postgres 18 with one entry of 20,001 postings: commit in 0.2 s, where
# re-summing the entry for every posting took 27 s.
BALANCE_FUNCTION = """
CREATE OR REPLACE FUNCTION assert_journal_entry_balanced() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    affected uuid[];
    unbalanced record;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM postings WHERE entry_id = NEW.entry_id AND id > NEW.id) THEN
            RETURN NULL;
        END IF;
        affected := ARRAY[NEW.entry_id];
    ELSIF TG_OP = 'DELETE' THEN
        affected := ARRAY[OLD.entry_id];
    ELSE
        affected := ARRAY[OLD.entry_id, NEW.entry_id];
    END IF;

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

# The body of revision 0001, restored on downgrade: every posting re-sums its entry.
BALANCE_FUNCTION_0001 = """
CREATE OR REPLACE FUNCTION assert_journal_entry_balanced() RETURNS trigger
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

# SPEC §5: budget and b are immutable while a market is OPEN. Approval sets both in the
# UPDATE that opens the market; there the old status is still PROPOSED.
CREATE_LIQUIDITY_FUNCTION = """
CREATE FUNCTION keep_open_market_liquidity() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF OLD.status = 'OPEN'
       AND (NEW.b_micro <> OLD.b_micro OR NEW.budget_micro <> OLD.budget_micro) THEN
        RAISE EXCEPTION 'market % is OPEN: its budget and b cannot change; halt it first', OLD.id
            USING ERRCODE = 'check_violation',
                  TABLE = 'markets',
                  CONSTRAINT = 'markets_liquidity_fixed_while_open';
    END IF;
    RETURN NEW;
END;
$$
"""

CREATE_LIQUIDITY_TRIGGER = """
CREATE TRIGGER markets_liquidity_fixed_while_open
BEFORE UPDATE OF b_micro, budget_micro ON markets
FOR EACH ROW EXECUTE FUNCTION keep_open_market_liquidity()
"""


def upgrade() -> None:
    op.add_column(
        "journal_entries",
        sa.Column(
            "created_xid",
            sa.BigInteger(),
            server_default=sa.text(CURRENT_TRANSACTION_ID),
            nullable=False,
        ),
    )
    op.alter_column("accounts", "balance_micro", server_default=sa.text("0"))
    op.create_check_constraint(
        op.f("ck_accounts_market_balance_non_negative"),
        "accounts",
        "kind <> 'MARKET' OR balance_micro >= 0",
    )
    op.create_check_constraint(
        op.f("ck_accounts_redemption_balance_non_negative"),
        "accounts",
        "kind NOT IN ('VIS_REDEMPTION_PENDING', 'VIS_REDEEMED') OR balance_micro >= 0",
    )
    op.create_check_constraint(
        op.f("ck_markets_loss_bound_within_budget"), "markets", LOSS_BOUND_WITHIN_BUDGET
    )
    op.create_check_constraint(
        op.f("ck_idempotency_keys_request_hash_sha256_hex"),
        "idempotency_keys",
        "request_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_unique_constraint(
        op.f("uq_trades_market_id_market_version"), "trades", ["market_id", "market_version"]
    )
    op.drop_index(op.f("ix_postings_entry_id"), table_name="postings")
    op.create_index(op.f("ix_postings_entry_id_id"), "postings", ["entry_id", "id"], unique=False)

    op.execute(BALANCE_FUNCTION)
    op.execute(CREATE_APPLY_FUNCTION)
    op.execute(CREATE_APPLY_TRIGGER)
    op.execute(CREATE_LIQUIDITY_FUNCTION)
    op.execute(CREATE_LIQUIDITY_TRIGGER)
    _restrict_app_role()


def downgrade() -> None:
    _unrestrict_app_role()
    op.execute("DROP TRIGGER markets_liquidity_fixed_while_open ON markets")
    op.execute("DROP FUNCTION keep_open_market_liquidity()")
    op.execute("DROP TRIGGER postings_apply ON postings")
    op.execute("DROP FUNCTION apply_posting()")
    op.execute(BALANCE_FUNCTION_0001)

    op.drop_index(op.f("ix_postings_entry_id_id"), table_name="postings")
    op.create_index(op.f("ix_postings_entry_id"), "postings", ["entry_id"], unique=False)
    op.drop_constraint(op.f("uq_trades_market_id_market_version"), "trades", type_="unique")
    op.drop_constraint(
        op.f("ck_idempotency_keys_request_hash_sha256_hex"), "idempotency_keys", type_="check"
    )
    op.drop_constraint(op.f("ck_markets_loss_bound_within_budget"), "markets", type_="check")
    op.drop_constraint(
        op.f("ck_accounts_redemption_balance_non_negative"), "accounts", type_="check"
    )
    op.drop_constraint(op.f("ck_accounts_market_balance_non_negative"), "accounts", type_="check")
    op.alter_column("accounts", "balance_micro", server_default=None)
    op.drop_column("journal_entries", "created_xid")


def _restrict_app_role() -> None:
    """Take from the app role what only the database may write (A.10).

    Revision 0001 granted INSERT, UPDATE and DELETE on every table. Column privileges
    cannot subtract from a table privilege, so those go first and the columns the app
    does set are granted back. UPDATE on `accounts.id` moves nothing (the foreign key
    of `postings` pins the id of every account that was ever posted to); Postgres asks
    for an UPDATE privilege on some column before it lets a role lock a row, and the
    services lock accounts.
    """
    role = _app_role()
    if not _role_exists(role):
        return
    _execute_formatted("REVOKE INSERT, UPDATE, DELETE ON accounts FROM %I", role)
    _execute_formatted(
        f"GRANT INSERT ({ACCOUNT_COLUMNS_THE_APP_SETS}), UPDATE (id) ON accounts TO %I", role
    )
    _execute_formatted("REVOKE INSERT ON journal_entries FROM %I", role)
    _execute_formatted(
        f"GRANT INSERT ({ENTRY_COLUMNS_THE_APP_SETS}) ON journal_entries TO %I", role
    )


def _unrestrict_app_role() -> None:
    role = _app_role()
    if not _role_exists(role):
        return
    _execute_formatted(
        f"REVOKE INSERT ({ENTRY_COLUMNS_THE_APP_SETS}) ON journal_entries FROM %I", role
    )
    _execute_formatted("GRANT INSERT ON journal_entries TO %I", role)
    _execute_formatted(
        f"REVOKE INSERT ({ACCOUNT_COLUMNS_THE_APP_SETS}), UPDATE (id) ON accounts FROM %I", role
    )
    _execute_formatted("GRANT INSERT, UPDATE, DELETE ON accounts TO %I", role)


def _app_role() -> str:
    """Name of the app role, from the Settings migrations/env.py ran with."""
    config = op.get_context().config
    assert config is not None
    settings = config.attributes["settings"]
    assert isinstance(settings, Settings)
    return settings.postgres.app_user


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


def _execute_formatted(template: str, *arguments: str) -> None:
    """Run a statement that cannot take bind parameters, letting Postgres do the quoting.

    `template` is a format() string in which %I takes an identifier.
    """
    bind = op.get_bind()
    parameters = {f"argument_{index}": argument for index, argument in enumerate(arguments)}
    placeholders = "".join(f", CAST(:{name} AS text)" for name in parameters)
    statement: str = bind.execute(
        sa.text(f"SELECT format(CAST(:template AS text){placeholders})"),
        {"template": template, **parameters},
    ).scalar_one()
    bind.exec_driver_sql(statement)
