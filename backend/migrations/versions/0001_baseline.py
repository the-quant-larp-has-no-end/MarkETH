"""Baseline: the whole schema as it was first deployed (SPEC §7, Appendix A.7, A.10).

Revision ID: 0001
Revises:

Every database starts here, and the revisions after this one take it to the schema of
the models. This file is therefore a record and is not edited: it holds the statements
the first deployed database was built with, as text, and reads nothing from
`app.models` that a later change to a model could alter. A change to a model is a new
revision (`make revision NAME=...`).

`upgrade` builds an empty database in five steps:

1. the tables, constraints and indexes;
2. the four trigger functions and their triggers;
3. the four VIS_* system accounts (SPEC §7);
4. the app role and what it may do: everything except what the ledger keeps to the
   database;
5. the fingerprint of all of the above, as the comment of `alembic_version`
   (`app.models.fingerprint`).
"""

from collections.abc import Sequence
from typing import Final
from uuid import uuid7

import sqlalchemy as sa
from alembic import op

from app.models.fingerprint import CLEAR_FINGERPRINT
from app.models.revisions import (
    app_role,
    current_database,
    execute_formatted,
    role_exists,
    store_fingerprint,
)

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FINGERPRINT: Final[str] = "c2e6fb70beaae04aa33addb36ed64ab0197aad751823402851f14312ea2ea7a0"
"""The fingerprint of the models this revision builds: `app.models.fingerprint` over the
statements below, the tables by name."""

TABLES: Final[dict[str, tuple[str, ...]]] = {
    "journal_entries": (
        """
CREATE TABLE journal_entries (
	id UUID NOT NULL,
	kind VARCHAR(32) NOT NULL,
	ref_type VARCHAR(32) NOT NULL,
	ref_id UUID NOT NULL,
	idempotency_key VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_xid BIGINT DEFAULT (pg_current_xact_id())::text::bigint NOT NULL,
	CONSTRAINT pk_journal_entries PRIMARY KEY (id),
	CONSTRAINT ck_journal_entries_kind CHECK (kind IN ('SIGNUP_GRANT', 'MONTHLY_GRANT', 'CODE_REDEMPTION', 'TRADE_BUY', 'TRADE_SELL', 'MARKET_RESOLUTION', 'MARKET_VOID', 'VOUCHER_ISSUE', 'VOUCHER_REDEEM', 'VOUCHER_REFUND', 'ADMIN_ADJUSTMENT', 'STREAK_REWARD', 'STREAK_BONUS')),
	CONSTRAINT uq_journal_entries_idempotency_key UNIQUE (idempotency_key)
)
        """,
    ),
    "outbox": (
        """
CREATE TABLE outbox (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	topic VARCHAR(32) NOT NULL,
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_outbox PRIMARY KEY (id),
	CONSTRAINT ck_outbox_topic CHECK (topic IN ('quote.updated'))
)
        """,
        """
CREATE INDEX ix_outbox_unpublished ON outbox (id) WHERE published_at IS NULL
        """,
    ),
    "users": (
        """
CREATE TABLE users (
	id UUID NOT NULL,
	email VARCHAR(320) NOT NULL,
	name VARCHAR(200) NOT NULL,
	username TEXT NOT NULL,
	degree_level VARCHAR(32) NOT NULL,
	degree_programme VARCHAR(100) NOT NULL,
	role VARCHAR(32) NOT NULL,
	status VARCHAR(32) NOT NULL,
	policy_accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	degree_changed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_users PRIMARY KEY (id),
	CONSTRAINT ck_users_username_format CHECK (username ~ '^[a-z0-9_]{1,32}$'),
	CONSTRAINT uq_users_email UNIQUE (email),
	CONSTRAINT uq_users_username UNIQUE (username),
	CONSTRAINT ck_users_degree_level CHECK (degree_level IN ('BACHELOR', 'MASTER', 'DOCTORATE', 'CONTINUING_EDUCATION', 'TEACHER_EDUCATION', 'OTHER')),
	CONSTRAINT ck_users_role CHECK (role IN ('USER', 'ADMIN', 'BARISTA')),
	CONSTRAINT ck_users_status CHECK (status IN ('ACTIVE', 'SUSPENDED'))
)
        """,
    ),
    "alerts": (
        """
CREATE TABLE alerts (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	severity VARCHAR(32) NOT NULL,
	message TEXT NOT NULL,
	context JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	acked_by UUID,
	acked_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_alerts PRIMARY KEY (id),
	CONSTRAINT ck_alerts_acked_by_iff_acked_at CHECK ((acked_by IS NULL) = (acked_at IS NULL)),
	CONSTRAINT ck_alerts_severity CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
	CONSTRAINT fk_alerts_acked_by_users FOREIGN KEY(acked_by) REFERENCES users (id)
)
        """,
    ),
    "audit_log": (
        """
CREATE TABLE audit_log (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	actor_id UUID,
	action VARCHAR(64) NOT NULL,
	target VARCHAR(128) NOT NULL,
	before JSONB,
	after JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_audit_log PRIMARY KEY (id),
	CONSTRAINT fk_audit_log_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id)
)
        """,
    ),
    "code_batches": (
        """
CREATE TABLE code_batches (
	id UUID NOT NULL,
	label TEXT NOT NULL,
	value_micro BIGINT NOT NULL,
	count BIGINT NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	one_per_user BOOLEAN NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_code_batches PRIMARY KEY (id),
	CONSTRAINT ck_code_batches_value_positive CHECK (value_micro > 0),
	CONSTRAINT ck_code_batches_count_positive CHECK (count > 0),
	CONSTRAINT fk_code_batches_created_by_users FOREIGN KEY(created_by) REFERENCES users (id)
)
        """,
    ),
    "grants": (
        """
CREATE TABLE grants (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	period VARCHAR(7) NOT NULL,
	entry_id UUID,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_grants PRIMARY KEY (id),
	CONSTRAINT uq_grants_user_id_period UNIQUE (user_id, period),
	CONSTRAINT ck_grants_period_format CHECK (period = 'SIGNUP' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
	CONSTRAINT fk_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT fk_grants_entry_id_journal_entries FOREIGN KEY(entry_id) REFERENCES journal_entries (id)
)
        """,
    ),
    "idempotency_keys": (
        """
CREATE TABLE idempotency_keys (
	user_id UUID NOT NULL,
	key VARCHAR(64) NOT NULL,
	request_hash VARCHAR(64) NOT NULL,
	response_json JSONB,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_idempotency_keys PRIMARY KEY (user_id, key),
	CONSTRAINT ck_idempotency_keys_request_hash_sha256_hex CHECK (request_hash ~ '^[0-9a-f]{64}$'),
	CONSTRAINT fk_idempotency_keys_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
)
        """,
    ),
    "markets": (
        """
CREATE TABLE markets (
	id UUID NOT NULL,
	question TEXT NOT NULL,
	yes_def TEXT NOT NULL,
	no_def TEXT NOT NULL,
	resolution_criteria TEXT NOT NULL,
	resolution_source TEXT NOT NULL,
	status VARCHAR(32) NOT NULL,
	tier VARCHAR(32),
	budget_micro BIGINT NOT NULL,
	b_micro BIGINT NOT NULL,
	q_yes_micro BIGINT NOT NULL,
	q_no_micro BIGINT NOT NULL,
	version BIGINT NOT NULL,
	volume_micro BIGINT NOT NULL,
	close_at TIMESTAMP WITH TIME ZONE NOT NULL,
	resolve_by TIMESTAMP WITH TIME ZONE NOT NULL,
	created_by UUID NOT NULL,
	audience_programme VARCHAR(100),
	approved_by UUID,
	rejection_reason TEXT,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	opened_at TIMESTAMP WITH TIME ZONE,
	closed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_markets PRIMARY KEY (id),
	CONSTRAINT ck_markets_budget_non_negative CHECK (budget_micro >= 0),
	CONSTRAINT ck_markets_b_non_negative CHECK (b_micro >= 0),
	CONSTRAINT ck_markets_b_positive_once_approved CHECK (status IN ('PROPOSED', 'REJECTED') OR b_micro > 0),
	CONSTRAINT ck_markets_loss_bound_within_budget CHECK (b_micro * 0.693147180559945309417232121458 <= budget_micro),
	CONSTRAINT ck_markets_q_yes_non_negative CHECK (q_yes_micro >= 0),
	CONSTRAINT ck_markets_q_no_non_negative CHECK (q_no_micro >= 0),
	CONSTRAINT ck_markets_version_non_negative CHECK (version >= 0),
	CONSTRAINT ck_markets_volume_non_negative CHECK (volume_micro >= 0),
	CONSTRAINT ck_markets_audience_programme_not_blank CHECK (audience_programme <> ''),
	CONSTRAINT ck_markets_status CHECK (status IN ('PROPOSED', 'REJECTED', 'OPEN', 'HALTED', 'CLOSED', 'RESOLVED', 'VOID')),
	CONSTRAINT ck_markets_tier CHECK (tier IN ('TIGHT', 'DEFAULT', 'MEDIUM', 'CUSTOM')),
	CONSTRAINT fk_markets_created_by_users FOREIGN KEY(created_by) REFERENCES users (id),
	CONSTRAINT fk_markets_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id)
)
        """,
        """
CREATE INDEX ix_markets_audience_programme_status_close_at ON markets (audience_programme, status, close_at)
        """,
        """
CREATE INDEX ix_markets_created_by_created_at ON markets (created_by, created_at)
        """,
        """
CREATE INDEX ix_markets_status_close_at ON markets (status, close_at)
        """,
    ),
    "streaks": (
        """
CREATE TABLE streaks (
	user_id UUID NOT NULL,
	current_days BIGINT NOT NULL,
	longest_days BIGINT NOT NULL,
	last_day DATE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_streaks PRIMARY KEY (user_id),
	CONSTRAINT ck_streaks_current_days_positive CHECK (current_days >= 1),
	CONSTRAINT ck_streaks_longest_not_below_current CHECK (longest_days >= current_days),
	CONSTRAINT fk_streaks_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
)
        """,
    ),
    "vouchers": (
        """
CREATE TABLE vouchers (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	product_id VARCHAR(64) NOT NULL,
	product_name VARCHAR(100) NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	value_micro BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	redeemed_at TIMESTAMP WITH TIME ZONE,
	redeemed_by UUID,
	CONSTRAINT pk_vouchers PRIMARY KEY (id),
	CONSTRAINT ck_vouchers_code_hash_sha256_hex CHECK (code_hash ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_vouchers_value_positive CHECK (value_micro > 0),
	CONSTRAINT ck_vouchers_redeemed_iff_redeemer CHECK ((status = 'REDEEMED') = (redeemed_by IS NOT NULL) AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)),
	CONSTRAINT fk_vouchers_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT uq_vouchers_code_hash UNIQUE (code_hash),
	CONSTRAINT ck_vouchers_status CHECK (status IN ('ACTIVE', 'REDEEMED', 'EXPIRED')),
	CONSTRAINT fk_vouchers_redeemed_by_users FOREIGN KEY(redeemed_by) REFERENCES users (id)
)
        """,
        """
CREATE INDEX ix_vouchers_user_id_status ON vouchers (user_id, status)
        """,
    ),
    "accounts": (
        """
CREATE TABLE accounts (
	id UUID NOT NULL,
	kind VARCHAR(32) NOT NULL,
	owner_id UUID,
	market_id UUID,
	balance_micro BIGINT DEFAULT 0 NOT NULL,
	CONSTRAINT pk_accounts PRIMARY KEY (id),
	CONSTRAINT ck_accounts_user_balance_non_negative CHECK (kind <> 'USER' OR balance_micro >= 0),
	CONSTRAINT ck_accounts_market_balance_non_negative CHECK (kind <> 'MARKET' OR balance_micro >= 0),
	CONSTRAINT ck_accounts_redemption_balance_non_negative CHECK (kind NOT IN ('VIS_REDEMPTION_PENDING', 'VIS_REDEEMED') OR balance_micro >= 0),
	CONSTRAINT ck_accounts_owner_iff_user CHECK ((kind = 'USER') = (owner_id IS NOT NULL)),
	CONSTRAINT ck_accounts_market_iff_market CHECK ((kind = 'MARKET') = (market_id IS NOT NULL)),
	CONSTRAINT ck_accounts_kind CHECK (kind IN ('USER', 'MARKET', 'VIS_ISSUANCE', 'VIS_MM_PNL', 'VIS_REDEMPTION_PENDING', 'VIS_REDEEMED')),
	CONSTRAINT fk_accounts_owner_id_users FOREIGN KEY(owner_id) REFERENCES users (id),
	CONSTRAINT fk_accounts_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id)
)
        """,
        """
CREATE UNIQUE INDEX uq_accounts_market_id ON accounts (market_id) WHERE kind = 'MARKET'
        """,
        """
CREATE UNIQUE INDEX uq_accounts_owner_id ON accounts (owner_id) WHERE kind = 'USER'
        """,
        """
CREATE UNIQUE INDEX uq_accounts_system_kind ON accounts (kind) WHERE kind NOT IN ('USER', 'MARKET')
        """,
    ),
    "broadcasts": (
        """
CREATE TABLE broadcasts (
	id UUID NOT NULL,
	market_id UUID NOT NULL,
	broadcaster_id UUID NOT NULL,
	status VARCHAR(32) NOT NULL,
	requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
	decided_by UUID,
	decided_at TIMESTAMP WITH TIME ZONE,
	refusal_reason TEXT,
	resumed_from UUID,
	started_at TIMESTAMP WITH TIME ZONE,
	on_air_at TIMESTAMP WITH TIME ZONE,
	last_chunk_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	ended_by UUID,
	end_reason VARCHAR(32),
	CONSTRAINT pk_broadcasts PRIMARY KEY (id),
	CONSTRAINT ck_broadcasts_decided_together CHECK ((decided_by IS NULL) = (decided_at IS NULL)),
	CONSTRAINT ck_broadcasts_requested_is_undecided CHECK ((status = 'REQUESTED') = (decided_at IS NULL)),
	CONSTRAINT ck_broadcasts_refusal_has_reason CHECK ((status = 'REFUSED') = (refusal_reason IS NOT NULL)),
	CONSTRAINT ck_broadcasts_live_has_started CHECK (status NOT IN ('LIVE', 'ENDED') OR (started_at IS NOT NULL AND last_chunk_at IS NOT NULL)),
	CONSTRAINT ck_broadcasts_ended_is_complete CHECK ((status = 'ENDED') = (ended_at IS NOT NULL) AND (status = 'ENDED') = (end_reason IS NOT NULL)),
	CONSTRAINT ck_broadcasts_resumed_has_started CHECK (resumed_from IS NULL OR status IN ('LIVE', 'ENDED')),
	CONSTRAINT fk_broadcasts_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT fk_broadcasts_broadcaster_id_users FOREIGN KEY(broadcaster_id) REFERENCES users (id),
	CONSTRAINT ck_broadcasts_status CHECK (status IN ('REQUESTED', 'APPROVED', 'REFUSED', 'LIVE', 'ENDED')),
	CONSTRAINT fk_broadcasts_decided_by_users FOREIGN KEY(decided_by) REFERENCES users (id),
	CONSTRAINT fk_broadcasts_resumed_from_broadcasts FOREIGN KEY(resumed_from) REFERENCES broadcasts (id),
	CONSTRAINT fk_broadcasts_ended_by_users FOREIGN KEY(ended_by) REFERENCES users (id),
	CONSTRAINT ck_broadcasts_end_reason CHECK (end_reason IN ('BROADCASTER', 'ADMIN', 'STALLED', 'TIME_LIMIT'))
)
        """,
        """
CREATE INDEX ix_broadcasts_broadcaster_id_requested_at ON broadcasts (broadcaster_id, requested_at)
        """,
        """
CREATE INDEX ix_broadcasts_market_id_requested_at ON broadcasts (market_id, requested_at)
        """,
        """
CREATE INDEX ix_broadcasts_open ON broadcasts (status, requested_at) WHERE status IN ('REQUESTED', 'APPROVED', 'LIVE')
        """,
        """
CREATE UNIQUE INDEX uq_broadcasts_market_open ON broadcasts (market_id) WHERE status IN ('REQUESTED', 'APPROVED', 'LIVE')
        """,
    ),
    "codes": (
        """
CREATE TABLE codes (
	id UUID NOT NULL,
	batch_id UUID NOT NULL,
	code_hash VARCHAR(64) NOT NULL,
	code VARCHAR(32),
	status VARCHAR(32) NOT NULL,
	redeemed_by UUID,
	redeemed_at TIMESTAMP WITH TIME ZONE,
	entry_id UUID,
	CONSTRAINT pk_codes PRIMARY KEY (id),
	CONSTRAINT ck_codes_code_hash_sha256_hex CHECK (code_hash ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_codes_redeemed_iff_redeemer CHECK ((status = 'REDEEMED') = (redeemed_by IS NOT NULL) AND (status = 'REDEEMED') = (redeemed_at IS NOT NULL)),
	CONSTRAINT ck_codes_code_iff_active CHECK ((status = 'ACTIVE') = (code IS NOT NULL)),
	CONSTRAINT fk_codes_batch_id_code_batches FOREIGN KEY(batch_id) REFERENCES code_batches (id),
	CONSTRAINT uq_codes_code_hash UNIQUE (code_hash),
	CONSTRAINT ck_codes_status CHECK (status IN ('ACTIVE', 'REDEEMED', 'REVOKED')),
	CONSTRAINT fk_codes_redeemed_by_users FOREIGN KEY(redeemed_by) REFERENCES users (id),
	CONSTRAINT fk_codes_entry_id_journal_entries FOREIGN KEY(entry_id) REFERENCES journal_entries (id)
)
        """,
        """
CREATE INDEX ix_codes_batch_id_status ON codes (batch_id, status)
        """,
    ),
    "notices": (
        """
CREATE TABLE notices (
	id UUID NOT NULL,
	user_id UUID NOT NULL,
	kind VARCHAR(32) NOT NULL,
	market_id UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	seen_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_notices PRIMARY KEY (id),
	CONSTRAINT uq_notices_user_id_kind_market_id UNIQUE (user_id, kind, market_id),
	CONSTRAINT fk_notices_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT ck_notices_kind CHECK (kind IN ('PROPOSAL_APPROVED')),
	CONSTRAINT fk_notices_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id)
)
        """,
        """
CREATE INDEX ix_notices_unseen ON notices (user_id, created_at) WHERE seen_at IS NULL
        """,
    ),
    "positions": (
        """
CREATE TABLE positions (
	user_id UUID NOT NULL,
	market_id UUID NOT NULL,
	outcome VARCHAR(32) NOT NULL,
	shares_micro BIGINT NOT NULL,
	cost_micro BIGINT NOT NULL,
	proceeds_micro BIGINT NOT NULL,
	CONSTRAINT pk_positions PRIMARY KEY (user_id, market_id, outcome),
	CONSTRAINT ck_positions_shares_non_negative CHECK (shares_micro >= 0),
	CONSTRAINT ck_positions_cost_non_negative CHECK (cost_micro >= 0),
	CONSTRAINT ck_positions_proceeds_non_negative CHECK (proceeds_micro >= 0),
	CONSTRAINT fk_positions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT fk_positions_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT ck_positions_outcome CHECK (outcome IN ('YES', 'NO'))
)
        """,
        """
CREATE INDEX ix_positions_market_id ON positions (market_id)
        """,
    ),
    "questions": (
        """
CREATE TABLE questions (
	id UUID NOT NULL,
	market_id UUID NOT NULL,
	asked_by UUID NOT NULL,
	text TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	answer_text TEXT,
	answered_by UUID,
	answered_at TIMESTAMP WITH TIME ZONE,
	removed_by UUID,
	removed_at TIMESTAMP WITH TIME ZONE,
	CONSTRAINT pk_questions PRIMARY KEY (id),
	CONSTRAINT ck_questions_text_length CHECK (char_length(text) BETWEEN 1 AND 500),
	CONSTRAINT ck_questions_answer_length CHECK (answer_text IS NULL OR char_length(answer_text) BETWEEN 1 AND 2000),
	CONSTRAINT ck_questions_answered_whole CHECK ((answer_text IS NULL) = (answered_by IS NULL) AND (answer_text IS NULL) = (answered_at IS NULL)),
	CONSTRAINT ck_questions_removed_whole CHECK ((removed_by IS NULL) = (removed_at IS NULL)),
	CONSTRAINT fk_questions_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT fk_questions_asked_by_users FOREIGN KEY(asked_by) REFERENCES users (id),
	CONSTRAINT fk_questions_answered_by_users FOREIGN KEY(answered_by) REFERENCES users (id),
	CONSTRAINT fk_questions_removed_by_users FOREIGN KEY(removed_by) REFERENCES users (id)
)
        """,
        """
CREATE INDEX ix_questions_market_id_id ON questions (market_id, id)
        """,
    ),
    "resolutions": (
        """
CREATE TABLE resolutions (
	id UUID NOT NULL,
	market_id UUID NOT NULL,
	outcome VARCHAR(32) NOT NULL,
	evidence TEXT NOT NULL,
	resolved_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_resolutions PRIMARY KEY (id),
	CONSTRAINT uq_resolutions_market_id UNIQUE (market_id),
	CONSTRAINT fk_resolutions_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT ck_resolutions_outcome CHECK (outcome IN ('YES', 'NO', 'VOID')),
	CONSTRAINT fk_resolutions_resolved_by_users FOREIGN KEY(resolved_by) REFERENCES users (id)
)
        """,
    ),
    "trades": (
        """
CREATE TABLE trades (
	id UUID NOT NULL,
	market_id UUID NOT NULL,
	user_id UUID NOT NULL,
	side VARCHAR(32) NOT NULL,
	outcome VARCHAR(32) NOT NULL,
	shares_micro BIGINT NOT NULL,
	amount_micro BIGINT NOT NULL,
	p_yes_before_micro BIGINT NOT NULL,
	p_yes_after_micro BIGINT NOT NULL,
	market_version BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_trades PRIMARY KEY (id),
	CONSTRAINT ck_trades_shares_positive CHECK (shares_micro > 0),
	CONSTRAINT ck_trades_amount_non_negative CHECK (amount_micro >= 0),
	CONSTRAINT uq_trades_market_id_market_version UNIQUE (market_id, market_version),
	CONSTRAINT fk_trades_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT fk_trades_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
	CONSTRAINT ck_trades_side CHECK (side IN ('BUY', 'SELL')),
	CONSTRAINT ck_trades_outcome CHECK (outcome IN ('YES', 'NO'))
)
        """,
        """
CREATE INDEX ix_trades_market_id_created_at ON trades (market_id, created_at)
        """,
        """
CREATE INDEX ix_trades_user_id_created_at ON trades (user_id, created_at)
        """,
        """
CREATE INDEX ix_trades_user_id_id ON trades (user_id, id)
        """,
    ),
    "chat_messages": (
        """
CREATE TABLE chat_messages (
	id UUID NOT NULL,
	market_id UUID NOT NULL,
	broadcast_id UUID NOT NULL,
	user_id UUID NOT NULL,
	text TEXT NOT NULL,
	from_vis BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_chat_messages PRIMARY KEY (id),
	CONSTRAINT ck_chat_messages_text_length CHECK (char_length(text) BETWEEN 1 AND 500),
	CONSTRAINT fk_chat_messages_market_id_markets FOREIGN KEY(market_id) REFERENCES markets (id),
	CONSTRAINT fk_chat_messages_broadcast_id_broadcasts FOREIGN KEY(broadcast_id) REFERENCES broadcasts (id),
	CONSTRAINT fk_chat_messages_user_id_users FOREIGN KEY(user_id) REFERENCES users (id)
)
        """,
        """
CREATE INDEX ix_chat_messages_broadcast_id_id ON chat_messages (broadcast_id, id)
        """,
    ),
    "postings": (
        """
CREATE TABLE postings (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	entry_id UUID NOT NULL,
	account_id UUID NOT NULL,
	amount_micro BIGINT NOT NULL,
	CONSTRAINT pk_postings PRIMARY KEY (id),
	CONSTRAINT ck_postings_amount_non_zero CHECK (amount_micro <> 0),
	CONSTRAINT fk_postings_entry_id_journal_entries FOREIGN KEY(entry_id) REFERENCES journal_entries (id),
	CONSTRAINT fk_postings_account_id_accounts FOREIGN KEY(account_id) REFERENCES accounts (id)
)
        """,
        """
CREATE INDEX ix_postings_account_id_id ON postings (account_id, id)
        """,
        """
CREATE INDEX ix_postings_entry_id_id ON postings (entry_id, id)
        """,
    ),
}
"""Table to its CREATE TABLE and the CREATE INDEX statements that follow it, in the order
they run: a table after every table it refers to."""

TRIGGERS: Final[tuple[str, ...]] = (
    """
CREATE FUNCTION assert_journal_entry_balanced() RETURNS trigger
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
    """,
    """
CREATE CONSTRAINT TRIGGER postings_entry_balanced
AFTER INSERT OR UPDATE OR DELETE ON postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balanced()
    """,
    """
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
        IF FOUND AND creator <> (pg_current_xact_id())::text::bigint THEN
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
    """,
    """
CREATE TRIGGER postings_apply
AFTER INSERT OR UPDATE OR DELETE ON postings
FOR EACH ROW EXECUTE FUNCTION apply_posting()
    """,
    """
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
    """,
    """
CREATE TRIGGER markets_liquidity_fixed_while_open
BEFORE UPDATE OF b_micro, budget_micro ON markets
FOR EACH ROW EXECUTE FUNCTION keep_open_market_liquidity()
    """,
    """
CREATE FUNCTION keep_market_audience() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.audience_programme IS DISTINCT FROM OLD.audience_programme THEN
        RAISE EXCEPTION 'market %: the audience of a market cannot change', OLD.id
            USING ERRCODE = 'check_violation',
                  TABLE = 'markets',
                  CONSTRAINT = 'markets_audience_fixed';
    END IF;
    RETURN NEW;
END;
$$
    """,
    """
CREATE TRIGGER markets_audience_fixed
BEFORE UPDATE OF audience_programme ON markets
FOR EACH ROW EXECUTE FUNCTION keep_market_audience()
    """,
)
"""Each function before the trigger that calls it."""

TRIGGER_FUNCTIONS: Final[tuple[str, ...]] = (
    "assert_journal_entry_balanced",
    "apply_posting",
    "keep_open_market_liquidity",
    "keep_market_audience",
)

SYSTEM_ACCOUNT_KINDS: Final[tuple[str, ...]] = (
    "VIS_ISSUANCE",
    "VIS_MM_PNL",
    "VIS_REDEMPTION_PENDING",
    "VIS_REDEEMED",
)

CREATE_APP_ROLE: Final[str] = "CREATE ROLE %I LOGIN PASSWORD %L"
GRANT_CONNECT: Final[str] = "GRANT CONNECT ON DATABASE %I TO %I"
REVOKE_CONNECT: Final[str] = "REVOKE CONNECT ON DATABASE %I FROM %I"

APP_ROLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "GRANT USAGE ON SCHEMA public TO %I",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I",
    "REVOKE UPDATE, DELETE ON journal_entries, postings FROM %I",
    "REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM %I",
    "REVOKE INSERT, UPDATE, DELETE ON accounts FROM %I",
    "GRANT INSERT (id, kind, owner_id, market_id), UPDATE (id) ON accounts TO %I",
    "REVOKE INSERT ON journal_entries FROM %I",
    "GRANT INSERT (id, kind, ref_type, ref_id, idempotency_key, created_at) ON journal_entries TO %I",
    "REVOKE DELETE ON questions FROM %I",
    "REVOKE UPDATE ON chat_messages FROM %I",
    "REVOKE DELETE ON broadcasts FROM %I",
)
"""Each takes the role. What is granted on every table comes first, then what is taken
back table by table."""

REVOKE_APP_ROLE_PRIVILEGES: Final[tuple[str, ...]] = (
    "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I",
    "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I",
    "REVOKE USAGE ON SCHEMA public FROM %I",
)
"""Each takes the role. Undoes `APP_ROLE_PRIVILEGES`; the role itself stays."""


def upgrade() -> None:
    bind = op.get_bind()
    # A table that is already there fails the build, instead of being kept as it is and
    # passed off as the one this revision describes.
    for statements in TABLES.values():
        for statement in statements:
            bind.exec_driver_sql(statement)
    for statement in TRIGGERS:
        bind.exec_driver_sql(statement)
    bind.execute(
        sa.text("INSERT INTO accounts (id, kind) VALUES (:id, :kind)"),
        [{"id": uuid7(), "kind": kind} for kind in SYSTEM_ACCOUNT_KINDS],
    )
    _grant_app_role()
    store_fingerprint(FINGERPRINT)


def downgrade() -> None:
    """Undo everything except the app role itself: it is cluster-wide and may be in use."""
    _revoke_app_role()
    op.execute(CLEAR_FINGERPRINT)
    # The triggers go with their tables, the functions they call do not.
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    for function in TRIGGER_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}()")


def _grant_app_role() -> None:
    role, password = app_role()
    if not role_exists(role):
        execute_formatted(CREATE_APP_ROLE, role, password)
    execute_formatted(GRANT_CONNECT, current_database(), role)
    for statement in APP_ROLE_PRIVILEGES:
        execute_formatted(statement, role)


def _revoke_app_role() -> None:
    role, _ = app_role()
    if not role_exists(role):
        return
    for statement in REVOKE_APP_ROLE_PRIVILEGES:
        execute_formatted(statement, role)
    execute_formatted(REVOKE_CONNECT, current_database(), role)
