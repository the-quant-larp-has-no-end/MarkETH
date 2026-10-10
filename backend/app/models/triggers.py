"""Rules a table definition cannot state: three trigger functions and their triggers
(SPEC §1, §5, §7, Appendix A.7, A.8).

* `postings_entry_balanced`: a journal entry nets to zero at commit, or the transaction
  fails.
* `postings_apply`: every posting moves the balance of its account, and only the
  transaction that created an entry may add postings to it.
* `markets_liquidity_fixed_while_open`: the budget and b of an OPEN market cannot change.

The baseline revision (`migrations/versions/0001_baseline.py`) runs `TRIGGER_STATEMENTS`
right after it has created the tables from the models. `app.models.fingerprint` hashes
the same statements, so an edit here asks for a rebuilt database, as an edit to a table
does.
"""

from typing import Final

from app.models.ledger import CURRENT_TRANSACTION_ID

# Deferred to commit: an entry is unbalanced while its postings are being inserted. The
# check fires once per posting, but an inserted posting that is not the last of its
# entry returns after one index probe on (entry_id, id), so an entry is summed once.
# That is sound only because `apply_posting` seals an entry: all its postings are
# inserted by one transaction, so the last one sees them all. Measured on Postgres 18
# with one entry of 20,001 postings: the commit takes 0.2 s, against 27 s when every
# posting sums the entry.
CREATE_BALANCE_FUNCTION: Final[str] = """
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
"""

CREATE_BALANCE_TRIGGER: Final[str] = """
CREATE CONSTRAINT TRIGGER postings_entry_balanced
AFTER INSERT OR UPDATE OR DELETE ON postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_journal_entry_balanced()
"""

# SECURITY DEFINER: it runs as the owner role, which is how a posting inserted by the
# app role reaches a column the app role cannot write. Tables are schema-qualified and
# the search path is pinned, as for every function that runs with its owner's rights.
#
# The balance follows INSERT, UPDATE and DELETE alike, so it equals the sum of the
# account's postings whoever changes them; the app role can only INSERT.
#
# An entry that does not exist is left to the foreign key to report.
CREATE_APPLY_FUNCTION: Final[str] = f"""
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
"""  # noqa: E501  (Postgres stores the body as written; a wrapped line is another schema)

CREATE_APPLY_TRIGGER: Final[str] = """
CREATE TRIGGER postings_apply
AFTER INSERT OR UPDATE OR DELETE ON postings
FOR EACH ROW EXECUTE FUNCTION apply_posting()
"""

# SPEC §5: budget and b are immutable while a market is OPEN. Approval sets both in the
# UPDATE that opens the market; there OLD.status is still PROPOSED.
CREATE_LIQUIDITY_FUNCTION: Final[str] = """
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

CREATE_LIQUIDITY_TRIGGER: Final[str] = """
CREATE TRIGGER markets_liquidity_fixed_while_open
BEFORE UPDATE OF b_micro, budget_micro ON markets
FOR EACH ROW EXECUTE FUNCTION keep_open_market_liquidity()
"""

TRIGGER_STATEMENTS: Final[tuple[str, ...]] = (
    CREATE_BALANCE_FUNCTION,
    CREATE_BALANCE_TRIGGER,
    CREATE_APPLY_FUNCTION,
    CREATE_APPLY_TRIGGER,
    CREATE_LIQUIDITY_FUNCTION,
    CREATE_LIQUIDITY_TRIGGER,
)
"""In the order the baseline runs them: each function before the trigger that calls it."""

TRIGGER_FUNCTIONS: Final[tuple[str, ...]] = (
    "assert_journal_entry_balanced",
    "apply_posting",
    "keep_open_market_liquidity",
)
"""What remains once the tables are dropped: a trigger goes with its table, the function
it calls does not."""
