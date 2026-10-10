"""The data layer against a real Postgres (SPEC §5, §7, §8, A.7, A.10).

What the database itself must guarantee, whatever the services above it do: the schema
matches the models, journal entries balance, the app role cannot rewrite the ledger, and
duplicates the spec forbids are rejected. Then the queries of each repository, and the
test harness the other integration tests stand on.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Final
from uuid import UUID, uuid7

import pytest
from sqlalchemy import CheckConstraint, func, literal_column, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import PRODUCT_ID_MAX_LENGTH, PRODUCT_NAME_MAX_LENGTH, Settings
from app.core.degrees import CATALOGUE
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    STORED_MARKET_STATUSES,
    SYSTEM_ACCOUNT_KINDS,
    AccountKind,
    AlertSeverity,
    BroadcastEndReason,
    BroadcastStatus,
    CodeStatus,
    DegreeLevel,
    JournalKind,
    MarketStatus,
    MarketTier,
    NoticeKind,
    OutboxTopic,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
    VoucherStatus,
)
from app.core.security import sha256_hex
from app.core.text import (
    ANSWER_STORED_MAX_LENGTH,
    CHAT_MESSAGE_STORED_MAX_LENGTH,
    QUESTION_STORED_MAX_LENGTH,
)
from app.db.base import Base
from app.db.database import Database
from app.models import (
    Account,
    Alert,
    AuditLogEntry,
    Broadcast,
    ChatMessage,
    Code,
    CodeBatch,
    Grant,
    IdempotencyKey,
    JournalEntry,
    Market,
    Notice,
    Position,
    Posting,
    Question,
    Resolution,
    Streak,
    Trade,
    User,
    Voucher,
)
from app.models.fingerprint import schema_fingerprint
from app.models.triggers import TRIGGER_FUNCTIONS, TRIGGER_STATEMENTS
from app.repositories.unit_of_work import UnitOfWork
from scripts import check_schema
from scripts.check_schema import stored_fingerprint
from tests.support.database import (
    check_database_matches_models,
    downgrade_database,
    reset_database,
    scratch_database,
    upgrade_database,
)
from tests.support.schema import check_constraint, enum_check_expression, with_one_more_member

pytestmark = pytest.mark.integration

type UowFactory = Callable[[], UnitOfWork]

NOW: Final[datetime] = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
DAY: Final[timedelta] = timedelta(days=1)
CR: Final[int] = 1_000_000
IDEMPOTENCY_KEY: Final[str] = "0199c1d4-7a55-7f0e-9a53-3f1f0c7f7a10"

BLOCKED_FOR: Final[float] = 0.3
"""Seconds a waiter must stay blocked before a test believes that it is waiting on a lock."""
UNBLOCKED_WITHIN: Final[float] = 5.0


@dataclass(frozen=True, slots=True)
class World:
    """The rows most tests start from, committed before the test body runs."""

    user: User
    admin: User
    market: Market
    user_account: Account
    market_account: Account
    batch: CodeBatch


def _user(name: str, **overrides: Any) -> User:
    values: dict[str, Any] = {
        "email": f"{name}@ethz.ch",
        "name": name.capitalize(),
        "username": name,
        "degree_level": DegreeLevel.BACHELOR,
        "degree_programme": "BSc Computer Science",
        "role": UserRole.USER,
        "status": UserStatus.ACTIVE,
        "policy_accepted_at": NOW,
        "created_at": NOW,
    }
    return User(**(values | overrides))


def _market(created_by: UUID, **overrides: Any) -> Market:
    values: dict[str, Any] = {
        "question": "Will the Polymensa serve Älplermagronen on Friday?",
        "yes_def": "It is on the menu.",
        "no_def": "It is not on the menu.",
        "resolution_criteria": "The published menu of that day.",
        "resolution_source": "ethz.ch/menu",
        "status": MarketStatus.OPEN,
        "tier": MarketTier.DEFAULT,
        "budget_micro": 6_931_472,
        "b_micro": 10 * CR,
        "close_at": NOW + 7 * DAY,
        "resolve_by": NOW + 14 * DAY,
        "created_by": created_by,
        "created_at": NOW,
    }
    return Market(**(values | overrides))


def _entry(idempotency_key: str, kind: JournalKind = JournalKind.SIGNUP_GRANT) -> JournalEntry:
    return JournalEntry(
        id=uuid7(),
        kind=kind,
        ref_type="test",
        ref_id=uuid7(),
        idempotency_key=idempotency_key,
        created_at=NOW,
    )


def _trade(world: World, **overrides: Any) -> Trade:
    values: dict[str, Any] = {
        "market_id": world.market.id,
        "user_id": world.user.id,
        "side": TradeSide.BUY,
        "outcome": Outcome.YES,
        "shares_micro": 1_900_000,
        "amount_micro": CR,
        "p_yes_before_micro": 500_000,
        "p_yes_after_micro": 547_000,
        "market_version": 1,
        "created_at": NOW,
    }
    return Trade(**(values | overrides))


def _voucher(world: World, code: str, **overrides: Any) -> Voucher:
    values: dict[str, Any] = {
        "user_id": world.user.id,
        "product_id": "mate",
        "product_name": "Mate",
        "code_hash": sha256_hex(code),
        "status": VoucherStatus.ACTIVE,
        "value_micro": CR,
        "created_at": NOW,
        "expires_at": NOW + 30 * DAY,
    }
    return Voucher(**(values | overrides))


def _code(world: World, plain: str, /, **overrides: Any) -> Code:
    """An ACTIVE code of the world's batch, which keeps `plain` itself beside its hash.
    The ids are made here, so codes sort by id in the order they were built."""
    values: dict[str, Any] = {
        "id": uuid7(),
        "batch_id": world.batch.id,
        "code_hash": sha256_hex(plain),
        "code": plain,
        "status": CodeStatus.ACTIVE,
    }
    return Code(**(values | overrides))


def _entry_with_legs(world: World, *amounts_micro: int) -> list[Base]:
    """An entry and one posting per amount, all on the user's account."""
    entry = _entry("legs")
    postings = [
        Posting(entry_id=entry.id, account_id=world.user_account.id, amount_micro=amount_micro)
        for amount_micro in amounts_micro
    ]
    return [entry, *postings]


async def _insert(uow_factory: UowFactory, rows: Sequence[Base]) -> None:
    """Insert rows one by one, in order, and commit."""
    async with uow_factory() as uow:
        for row in rows:
            uow.session.add(row)
            await uow.flush()


async def _post(uow: UnitOfWork, idempotency_key: str, legs: Mapping[UUID, int]) -> JournalEntry:
    """Book an entry the way `LedgerService` does: the entry, then its postings. The
    database moves the balances."""
    entry = await uow.journal.add_entry(_entry(idempotency_key))
    await uow.journal.add_postings(
        [
            Posting(entry_id=entry.id, account_id=account_id, amount_micro=amount_micro)
            for account_id, amount_micro in legs.items()
        ]
    )
    return entry


async def _grant(uow_factory: UowFactory, world: World, amount_micro: int, key: str) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        await _post(uow, key, {issuance.id: -amount_micro, world.user_account.id: amount_micro})


@pytest.fixture
async def world(uow_factory: UowFactory) -> World:
    async with uow_factory() as uow:
        user = await uow.users.add(_user("alice"))
        admin = await uow.users.add(_user("admin"))
        market = await uow.markets.add(_market(user.id))
        user_account = await uow.accounts.create_user_account(user.id)
        market_account = await uow.accounts.create_market_account(market.id)
        batch = await uow.code_batches.add(
            CodeBatch(
                label="Erstsemestrigen-Apéro",
                value_micro=2 * CR,
                count=3,
                expires_at=NOW + 30 * DAY,
                one_per_user=True,
                created_by=admin.id,
                created_at=NOW,
            )
        )
    return World(user, admin, market, user_account, market_account, batch)


# --- The schema and the revision that builds it -------------------------------------


def test_models_match_the_migrated_schema(settings: Settings, owner_engine: AsyncEngine) -> None:
    check_database_matches_models(settings)


async def test_every_check_constraint_of_the_models_exists_exactly_once(
    owner_engine: AsyncEngine,
) -> None:
    # `alembic check` does not compare CHECK constraints, so compare them by name here.
    expected = sorted(
        str(constraint.name)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    )
    async with owner_engine.connect() as connection:
        actual = await connection.scalars(
            text(
                "SELECT conname FROM pg_constraint"
                " WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
                " ORDER BY conname"
            )
        )
        assert list(actual) == expected


async def test_exactly_four_system_accounts_exist(uow_factory: UowFactory) -> None:
    async with uow_factory() as uow:
        accounts = (await uow.session.scalars(select(Account))).all()
        assert sorted(account.kind for account in accounts) == sorted(SYSTEM_ACCOUNT_KINDS)
        for account in accounts:
            assert (account.balance_micro, account.owner_id, account.market_id) == (0, None, None)
            assert await uow.accounts.get_system_account(account.kind) is account


async def test_downgrade_removes_everything_but_the_app_role(
    settings: Settings, owner_engine: AsyncEngine
) -> None:
    app_role = settings.postgres.app_user
    leftovers = text(
        "SELECT"
        " (SELECT count(*) FROM pg_tables"
        "   WHERE schemaname = 'public' AND tablename <> 'alembic_version'),"
        " (SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace),"
        " (SELECT count(*) FROM pg_default_acl),"
        " (SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = :role),"
        " (SELECT count(*) FROM alembic_version),"
        " (SELECT count(*) FROM pg_description WHERE objoid = 'alembic_version'::regclass),"
        " (SELECT count(*) FROM pg_roles WHERE rolname = :role)"
    )
    async with scratch_database(owner_engine, settings, "marketh_downgrade_check") as scratch:
        scratch_engine = create_async_engine(scratch.postgres.owner_url, poolclass=NullPool)

        await asyncio.to_thread(upgrade_database, scratch)
        await asyncio.to_thread(downgrade_database, scratch)
        async with scratch_engine.connect() as connection:
            row = (await connection.execute(leftovers, {"role": app_role})).one()
        assert tuple(row) == (0, 0, 0, 0, 0, 0, 1)

        await asyncio.to_thread(upgrade_database, scratch)
        async with scratch_engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(Account)) == 4
        assert await stored_fingerprint(scratch) == schema_fingerprint()


# --- The fingerprint of the schema (A.10) ---------------------------------------------
#
# The schema has one revision, so `alembic upgrade head` leaves a database alone that
# was built from other models. `scripts.check_schema` is what notices.


async def test_a_fresh_database_carries_the_fingerprint_of_the_models(
    settings: Settings, owner_engine: AsyncEngine
) -> None:
    assert await stored_fingerprint(settings) == schema_fingerprint()


async def _run_check_schema(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m scripts.check_schema` against the database of `settings`.

    `main` starts an event loop of its own, as it does from the command line, so it runs
    in a worker thread.
    """
    monkeypatch.setattr(check_schema, "get_settings", lambda: settings)
    await asyncio.to_thread(check_schema.main)


async def test_check_schema_accepts_a_database_built_from_these_models(
    settings: Settings,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run_check_schema(settings, monkeypatch)

    assert capsys.readouterr().out == "The database was built from the schema of this code.\n"


def _change_a_check(monkeypatch: pytest.MonkeyPatch) -> None:
    check = check_constraint("postings", "ck_postings_amount_non_zero")
    monkeypatch.setattr(check, "sqltext", literal_column("amount_micro > 0"))


def _add_an_enum_member(monkeypatch: pytest.MonkeyPatch) -> None:
    check = check_constraint("journal_entries", "ck_journal_entries_kind")
    more_kinds = with_one_more_member(JournalKind, "TIP")
    expression = enum_check_expression("journal_entries", "kind", more_kinds)
    monkeypatch.setattr(check, "sqltext", expression)


@pytest.mark.parametrize(
    "edit_a_model",
    [_change_a_check, _add_an_enum_member],
    ids=["changed-check", "added-enum-member"],
)
async def test_check_schema_refuses_a_database_once_a_model_has_changed(
    settings: Settings,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    edit_a_model: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """`alembic check` stays green for both edits: it does not compare CHECK
    constraints, and an enum column is a VARCHAR with one. Left alone, the database
    would refuse rows the code writes, or take rows the code forbids."""
    await _run_check_schema(settings, monkeypatch)

    edit_a_model(monkeypatch)

    await asyncio.to_thread(check_database_matches_models, settings)
    with pytest.raises(SystemExit) as refusal:
        await _run_check_schema(settings, monkeypatch)
    assert refusal.value.code == (
        "The schema changed since this database was built: wipe and rebuild it (make reset)."
    )


async def test_check_schema_refuses_a_database_that_carries_no_fingerprint(
    settings: Settings, owner_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing says what such a database was built from, so nothing vouches for it."""
    async with scratch_database(owner_engine, settings, "marketh_unbuilt_check") as scratch:
        assert await stored_fingerprint(scratch) is None
        with pytest.raises(SystemExit, match="The schema changed since this database was built"):
            await _run_check_schema(scratch, monkeypatch)


# --- The ledger ---------------------------------------------------------------------


async def test_balanced_entry_commits(uow_factory: UowFactory, world: World) -> None:
    await _grant(uow_factory, world, 5 * CR, "grant:alice:SIGNUP")

    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        account = await uow.accounts.get(world.user_account.id)
        assert account is not None
        assert (account.balance_micro, issuance.balance_micro) == (5 * CR, -5 * CR)
        assert await uow.accounts.total_balance() == 0
        assert await uow.journal.get_entry_by_idempotency_key("grant:alice:SIGNUP") is not None


async def test_unbalanced_entry_fails_at_commit(uow_factory: UowFactory, world: World) -> None:
    inserted = False

    async def book_unbalanced() -> None:
        nonlocal inserted
        async with uow_factory() as uow:
            issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
            entry = await uow.journal.add_entry(_entry("grant:unbalanced"))
            await uow.journal.add_postings(
                [
                    Posting(entry_id=entry.id, account_id=issuance.id, amount_micro=-5 * CR),
                    Posting(
                        entry_id=entry.id, account_id=world.user_account.id, amount_micro=4 * CR
                    ),
                ]
            )
            # Both INSERTs went through. Only COMMIT runs the deferred check.
            inserted = True

    with pytest.raises(IntegrityError, match="is unbalanced"):
        await book_unbalanced()

    assert inserted
    async with uow_factory() as uow:
        assert await uow.journal.get_entry_by_idempotency_key("grant:unbalanced") is None


async def test_each_entry_must_balance_on_its_own(uow_factory: UowFactory, world: World) -> None:
    async def book_two_halves() -> None:
        async with uow_factory() as uow:
            issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
            debit = await uow.journal.add_entry(_entry("half:debit"))
            credit = await uow.journal.add_entry(_entry("half:credit"))
            await uow.journal.add_postings(
                [
                    Posting(entry_id=debit.id, account_id=issuance.id, amount_micro=-CR),
                    Posting(entry_id=credit.id, account_id=world.user_account.id, amount_micro=CR),
                ]
            )

    with pytest.raises(IntegrityError, match="is unbalanced"):
        await book_two_halves()


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE postings SET amount_micro = amount_micro + 1 WHERE amount_micro > 0",
        "DELETE FROM postings WHERE amount_micro > 0",
    ],
    ids=["update-one-leg", "delete-one-leg"],
)
async def test_owner_role_cannot_unbalance_a_committed_entry(
    uow_factory: UowFactory, owner_engine: AsyncEngine, world: World, tamper: str
) -> None:
    await _grant(uow_factory, world, 5 * CR, "grant:alice:SIGNUP")

    async def run_tamper() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(text(tamper))

    with pytest.raises(IntegrityError, match="is unbalanced"):
        await run_tamper()


async def _move(uow_factory: UowFactory, source: UUID, target: UUID, amount_micro: int) -> None:
    """One balanced entry that takes `amount_micro` from `source` and gives it to `target`."""
    async with uow_factory() as uow:
        await _post(uow, f"move:{uuid7()}", {source: -amount_micro, target: amount_micro})


async def _system_account_id(uow_factory: UowFactory, kind: AccountKind) -> UUID:
    async with uow_factory() as uow:
        return (await uow.accounts.get_system_account(kind)).id


async def test_negative_user_balance_is_rejected(uow_factory: UowFactory, world: World) -> None:
    issuance = await _system_account_id(uow_factory, AccountKind.VIS_ISSUANCE)

    with pytest.raises(IntegrityError, match="ck_accounts_user_balance_non_negative"):
        await _move(uow_factory, world.user_account.id, issuance, 1)

    async with uow_factory() as uow:
        assert not await uow.accounts.has_negative_user_balance()
        assert await uow.accounts.total_balance() == 0


@pytest.mark.parametrize("kind", [AccountKind.VIS_ISSUANCE, AccountKind.VIS_MM_PNL])
async def test_issuance_and_market_maker_pnl_may_go_negative(
    uow_factory: UowFactory, world: World, kind: AccountKind
) -> None:
    """SPEC §7: `vis:issuance` is the source of every credit; `vis:mm_pnl` may lose."""
    source = await _system_account_id(uow_factory, kind)
    await _move(uow_factory, source, world.user_account.id, CR)

    async with uow_factory() as uow:
        assert (await uow.accounts.get_system_account(kind)).balance_micro == -CR


@pytest.mark.parametrize(
    ("kind", "constraint"),
    [
        (AccountKind.MARKET, "ck_accounts_market_balance_non_negative"),
        (AccountKind.VIS_REDEMPTION_PENDING, "ck_accounts_redemption_balance_non_negative"),
        (AccountKind.VIS_REDEEMED, "ck_accounts_redemption_balance_non_negative"),
    ],
)
async def test_market_and_voucher_accounts_cannot_go_negative(
    uow_factory: UowFactory, world: World, kind: AccountKind, constraint: str
) -> None:
    """A.8: a market account pays out only what it took in, and a voucher leaves
    `vis:redemption_pending` once. A balanced entry that overdraws one is a bug."""
    source = (
        world.market_account.id
        if kind is AccountKind.MARKET
        else await _system_account_id(uow_factory, kind)
    )

    with pytest.raises(IntegrityError, match=constraint):
        await _move(uow_factory, source, world.user_account.id, 1)

    async with uow_factory() as uow:
        assert await uow.accounts.total_balance() == 0
        assert await uow.accounts.sum_balance_by_kind(AccountKind.USER) == 0


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE journal_entries SET ref_type = 'rewritten'",
        "DELETE FROM journal_entries",
        "TRUNCATE journal_entries CASCADE",
        "UPDATE postings SET amount_micro = 1",
        "DELETE FROM postings",
        "TRUNCATE postings",
        "UPDATE alembic_version SET version_num = 'rewritten'",
    ],
)
async def test_app_role_cannot_rewrite_the_ledger(
    uow_factory: UowFactory, database: Database, world: World, statement: str
) -> None:
    await _grant(uow_factory, world, 5 * CR, "grant:alice:SIGNUP")

    async def run_statement() -> None:
        async with database.engine.begin() as connection:
            await connection.execute(text(statement))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await run_statement()


async def test_app_role_can_insert_and_read_ledger_rows(
    uow_factory: UowFactory, database: Database, settings: Settings, world: World
) -> None:
    # `uow_factory` runs on the app role: this INSERT is the one thing it may do here.
    await _grant(uow_factory, world, 5 * CR, "grant:alice:SIGNUP")

    async with database.engine.connect() as connection:
        role = await connection.scalar(text("SELECT current_user"))
        entries = await connection.scalar(select(func.count()).select_from(JournalEntry))
        postings = await connection.scalar(select(func.count()).select_from(Posting))
    assert (role, entries, postings) == (settings.postgres.app_user, 1, 2)


async def test_app_role_holds_exactly_these_privileges(
    settings: Settings, owner_engine: AsyncEngine
) -> None:
    """Everything on every table, less what the ledger keeps to the database (A.10).

    The privileges are granted once, on the tables that exist when the baseline grants
    them, and nothing is granted by default: a table that missed them would be out of
    the app's reach.
    """
    everything = {"SELECT", "INSERT", "UPDATE", "DELETE"}
    on_tables = dict.fromkeys(Base.metadata.tables, everything) | {
        "accounts": {"SELECT"},
        "journal_entries": {"SELECT"},
        "postings": {"SELECT", "INSERT"},
        "alembic_version": {"SELECT"},
        # A question is never erased, a chat message never edited, a broadcast never
        # erased.
        "questions": {"SELECT", "INSERT", "UPDATE"},
        "chat_messages": {"SELECT", "INSERT", "DELETE"},
        "broadcasts": {"SELECT", "INSERT", "UPDATE"},
    }
    on_columns = {
        ("accounts", "INSERT"): {"id", "kind", "owner_id", "market_id"},
        ("accounts", "UPDATE"): {"id"},
        ("journal_entries", "INSERT"): {
            "id",
            "kind",
            "ref_type",
            "ref_id",
            "idempotency_key",
            "created_at",
        },
    }
    granted_to_role = (
        " JOIN pg_roles r ON r.oid = acl.grantee"
        " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r' AND r.rolname = :role"
    )
    role = {"role": settings.postgres.app_user}

    async with owner_engine.connect() as connection:
        table_grants = await connection.execute(
            text(
                "SELECT c.relname, acl.privilege_type FROM pg_class c"
                " CROSS JOIN LATERAL aclexplode(c.relacl) AS acl" + granted_to_role
            ),
            role,
        )
        column_grants = await connection.execute(
            text(
                "SELECT c.relname, acl.privilege_type, a.attname FROM pg_attribute a"
                " JOIN pg_class c ON c.oid = a.attrelid"
                " CROSS JOIN LATERAL aclexplode(a.attacl) AS acl" + granted_to_role
            ),
            role,
        )
        defaults = await connection.scalar(text("SELECT count(*) FROM pg_default_acl"))

    actual_on_tables: dict[str, set[str]] = {}
    for table, privilege in table_grants:
        actual_on_tables.setdefault(table, set()).add(privilege)
    actual_on_columns: dict[tuple[str, str], set[str]] = {}
    for table, privilege, column in column_grants:
        actual_on_columns.setdefault((table, privilege), set()).add(column)
    assert actual_on_tables == on_tables
    assert actual_on_columns == on_columns
    assert defaults == 0


# --- Rows the database must refuse ----------------------------------------------------

REJECTED_ROWS: Final = [
    pytest.param(
        lambda w: [
            Resolution(
                market_id=w.market.id,
                outcome=outcome,
                evidence="Menu of the day.",
                resolved_by=w.admin.id,
                created_at=NOW,
            )
            for outcome in (ResolutionOutcome.YES, ResolutionOutcome.NO)
        ],
        "uq_resolutions_market_id",
        id="second-resolution-for-a-market",
    ),
    pytest.param(
        lambda w: [
            Grant(user_id=w.user.id, period=SIGNUP_GRANT_PERIOD, created_at=NOW) for _ in range(2)
        ],
        "uq_grants_user_id_period",
        id="duplicate-grant",
    ),
    pytest.param(
        lambda w: [Grant(user_id=w.user.id, period="2026-13", created_at=NOW)],
        "ck_grants_period_format",
        id="grant-period-that-is-no-month",
    ),
    pytest.param(
        lambda w: [_code(w, "SAME-CODE") for _ in range(2)],
        "uq_codes_code_hash",
        id="duplicate-code-hash",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", status=CodeStatus.REDEEMED, code=None)],
        "ck_codes_redeemed_iff_redeemer",
        id="redeemed-code-without-redeemer",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", code_hash="XXXX-XXXX-XXXX")],
        "ck_codes_code_hash_sha256_hex",
        id="plain-code-where-its-hash-belongs",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", code=None)],
        "ck_codes_code_iff_active",
        id="active-code-that-does-not-keep-its-code",
    ),
    pytest.param(
        lambda w: [
            _code(w, "CODE", status=CodeStatus.REDEEMED, redeemed_by=w.user.id, redeemed_at=NOW)
        ],
        "ck_codes_code_iff_active",
        id="redeemed-code-that-kept-its-code",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", status=CodeStatus.REVOKED)],
        "ck_codes_code_iff_active",
        id="revoked-code-that-kept-its-code",
    ),
    pytest.param(
        lambda w: [_voucher(w, "SAME-CODE") for _ in range(2)],
        "uq_vouchers_code_hash",
        id="duplicate-voucher-hash",
    ),
    pytest.param(
        lambda w: [_voucher(w, "CODE", status=VoucherStatus.REDEEMED)],
        "ck_vouchers_redeemed_iff_redeemer",
        id="redeemed-voucher-without-barista",
    ),
    pytest.param(
        # Another username, so that only the index on the address can fire.
        lambda w: [_user("alice", username="alice_again")],
        "uq_users_email",
        id="duplicate-email",
    ),
    pytest.param(
        lambda w: [_user("alicia", username="alice")],
        "uq_users_username",
        id="username-of-someone-else",
    ),
    *(
        pytest.param(
            lambda w, username=username: [_user("bob", username=username)],
            "ck_users_username_format",
            id=f"username-{reason}",
        )
        for reason, username in (
            ("with-a-capital-letter", "Bob"),
            ("in-upper-case", "BOB"),
            ("of-someone-else-in-upper-case", "ALICE"),
            ("with-a-space", "bob smith"),
            ("with-a-dash", "bob-smith"),
            ("with-a-letter-outside-ascii", "zo" + chr(0xEB)),
            ("of-33-characters", "b" * 33),
            ("that-is-empty", ""),
            ("that-ends-in-a-line-break", "bob" + chr(0x0A)),
        )
    ),
    pytest.param(
        lambda w: [_entry("same-key"), _entry("same-key")],
        "uq_journal_entries_idempotency_key",
        id="duplicate-journal-idempotency-key",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.USER, owner_id=w.user.id)],
        "uq_accounts_owner_id",
        id="second-account-for-a-user",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.MARKET, market_id=w.market.id)],
        "uq_accounts_market_id",
        id="second-account-for-a-market",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.VIS_ISSUANCE)],
        "uq_accounts_system_kind",
        id="second-issuance-account",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.USER)],
        "ck_accounts_owner_iff_user",
        id="user-account-without-owner",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.MARKET, owner_id=w.admin.id, market_id=w.market.id)],
        "ck_accounts_owner_iff_user",
        id="market-account-with-owner",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.MARKET)],
        "ck_accounts_market_iff_market",
        id="market-account-without-market",
    ),
    pytest.param(
        lambda w: [Account(kind=AccountKind.USER, owner_id=w.admin.id, market_id=w.market.id)],
        "ck_accounts_market_iff_market",
        id="user-account-with-market",
    ),
    *(
        pytest.param(
            lambda w, column=column: [
                Position(
                    **{
                        "user_id": w.user.id,
                        "market_id": w.market.id,
                        "outcome": Outcome.YES,
                        column: -1,
                    }
                )
            ],
            constraint,
            id=f"negative-position-{column}",
        )
        for column, constraint in (
            ("shares_micro", "ck_positions_shares_non_negative"),
            ("cost_micro", "ck_positions_cost_non_negative"),
            ("proceeds_micro", "ck_positions_proceeds_non_negative"),
        )
    ),
    *(
        pytest.param(
            lambda w, column=column: [
                _market(w.user.id, status=MarketStatus.PROPOSED, **{column: -1})
            ],
            constraint,
            id=f"negative-market-{column}",
        )
        for column, constraint in (
            ("q_yes_micro", "ck_markets_q_yes_non_negative"),
            ("q_no_micro", "ck_markets_q_no_non_negative"),
            ("version", "ck_markets_version_non_negative"),
            ("volume_micro", "ck_markets_volume_non_negative"),
            ("budget_micro", "ck_markets_budget_non_negative"),
            ("b_micro", "ck_markets_b_non_negative"),
        )
    ),
    *(
        pytest.param(
            lambda w, status=status: [_market(w.user.id, status=status, b_micro=0)],
            "ck_markets_b_positive_once_approved",
            id=f"{status.lower()}-market-without-liquidity",
        )
        for status in STORED_MARKET_STATUSES
        if status not in (MarketStatus.PROPOSED, MarketStatus.REJECTED)
    ),
    pytest.param(
        lambda w: _entry_with_legs(w, 0),
        "ck_postings_amount_non_zero",
        id="zero-amount-posting",
    ),
    pytest.param(
        lambda w: [_trade(w, shares_micro=0)],
        "ck_trades_shares_positive",
        id="trade-of-zero-shares",
    ),
    pytest.param(
        lambda w: [_trade(w, market_version=1), _trade(w, market_version=1)],
        "uq_trades_market_id_market_version",
        id="two-trades-with-one-market-version",
    ),
    pytest.param(
        lambda w: [_market(w.user.id, b_micro=10 * CR, budget_micro=6_931_471)],
        "ck_markets_loss_bound_within_budget",
        id="market-whose-budget-does-not-cover-b-ln-2",
    ),
    pytest.param(
        lambda w: [
            IdempotencyKey(
                user_id=w.user.id, key=IDEMPOTENCY_KEY, request_hash="buy 1 CR", created_at=NOW
            )
        ],
        "ck_idempotency_keys_request_hash_sha256_hex",
        id="request-hash-that-is-no-hash",
    ),
    pytest.param(
        lambda w: [
            Notice(
                user_id=w.user.id,
                kind=NoticeKind.PROPOSAL_APPROVED,
                market_id=w.market.id,
                created_at=NOW + late,
            )
            for late in (timedelta(0), DAY)
        ],
        "uq_notices_user_id_kind_market_id",
        id="second-notice-of-one-kind-about-one-market",
    ),
    pytest.param(
        lambda w: [
            Notice(
                user_id=uuid7(),
                kind=NoticeKind.PROPOSAL_APPROVED,
                market_id=w.market.id,
                created_at=NOW,
            )
        ],
        "fk_notices_user_id_users",
        id="notice-for-nobody",
    ),
    pytest.param(
        lambda w: [
            Notice(
                user_id=w.user.id,
                kind=NoticeKind.PROPOSAL_APPROVED,
                market_id=uuid7(),
                created_at=NOW,
            )
        ],
        "fk_notices_market_id_markets",
        id="notice-about-no-market",
    ),
]


@pytest.mark.parametrize(("rows", "constraint"), REJECTED_ROWS)
async def test_database_rejects(
    uow_factory: UowFactory,
    world: World,
    rows: Callable[[World], Sequence[Base]],
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await _insert(uow_factory, rows(world))


# --- EXPIRED is reported, never stored (A.4) -------------------------------------------


async def test_the_application_cannot_store_a_market_as_expired(
    uow_factory: UowFactory, world: World
) -> None:
    """Neither as a row of its own nor by changing one: the statement fails when it is
    bound, the transaction is rolled back, and the market is what it was."""
    with pytest.raises(StatementError, match="EXPIRED"):
        await _insert(uow_factory, [_market(world.user.id, status=MarketStatus.EXPIRED)])

    async def expire() -> None:
        async with uow_factory() as uow:
            market = await uow.markets.get_for_update(world.market.id)
            assert market is not None
            market.status = MarketStatus.EXPIRED

    with pytest.raises(StatementError, match="EXPIRED"):
        await expire()

    async with uow_factory() as uow:
        stored = (await uow.session.scalars(select(Market))).all()
    assert [(market.id, market.status) for market in stored] == [
        (world.market.id, MarketStatus.OPEN)
    ]


async def test_the_application_cannot_ask_the_database_for_expired_markets(
    uow_factory: UowFactory, world: World
) -> None:
    """A query for a status no row holds would answer with nothing and be taken for
    "there are none". It fails instead: an expired proposal is read as PROPOSED and
    judged by the clock (`MarketView.stored_statuses`)."""
    questions: list[Callable[[UnitOfWork], Awaitable[object]]] = [
        lambda uow: uow.markets.list_by_statuses([MarketStatus.EXPIRED]),
        lambda uow: uow.markets.list_by_statuses([MarketStatus.OPEN, MarketStatus.EXPIRED]),
        lambda uow: uow.markets.count_by_statuses([MarketStatus.EXPIRED]),
        lambda uow: uow.markets.list_held_outside([MarketStatus.EXPIRED]),
    ]
    for asking in questions:
        with pytest.raises(StatementError, match="EXPIRED"):
            async with uow_factory() as uow:
                await asking(uow)

    async with uow_factory() as uow:
        assert [
            market.id for market in await uow.markets.list_by_statuses(STORED_MARKET_STATUSES)
        ] == [world.market.id]


@pytest.mark.parametrize("role", ["owner", "app"])
async def test_the_database_itself_refuses_a_market_stored_as_expired(
    owner_engine: AsyncEngine, database: Database, world: World, role: str
) -> None:
    """Whoever goes around the application meets the CHECK, which lists the seven
    statuses a row keeps."""
    engine = owner_engine if role == "owner" else database.engine

    async def write(statement: str) -> None:
        async with engine.begin() as connection:
            await connection.execute(text(statement), {"id": world.market.id})

    with pytest.raises(IntegrityError, match="ck_markets_status"):
        await write("UPDATE markets SET status = 'EXPIRED' WHERE id = :id")
    with pytest.raises(IntegrityError, match="ck_markets_status"):
        await write(
            "INSERT INTO markets (id, question, yes_def, no_def, resolution_criteria,"
            " resolution_source, status, budget_micro, b_micro, q_yes_micro, q_no_micro,"
            " version, volume_micro, close_at, resolve_by, created_by, created_at)"
            " SELECT gen_random_uuid(), question, yes_def, no_def, resolution_criteria,"
            " resolution_source, 'EXPIRED', budget_micro, b_micro, 0, 0, 0, 0, close_at,"
            " resolve_by, created_by, created_at FROM markets WHERE id = :id"
        )

    async with owner_engine.connect() as connection:
        statuses = (await connection.scalars(text("SELECT status FROM markets"))).all()
        listed = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_markets_status'"
            )
        )
    assert statuses == ["OPEN"]
    assert listed is not None
    assert "EXPIRED" not in listed
    assert all(f"'{status.value}'" in listed for status in STORED_MARKET_STATUSES)


async def test_a_notice_of_a_kind_the_enum_does_not_know_is_refused(
    owner_engine: AsyncEngine, world: World
) -> None:
    async def write() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO notices (id, user_id, kind, market_id, created_at)"
                    " VALUES (gen_random_uuid(), :user, 'PROPOSAL_REJECTED', :market, now())"
                ),
                {"user": world.user.id, "market": world.market.id},
            )

    with pytest.raises(IntegrityError, match="ck_notices_kind"):
        await write()


# --- Notices (A.5) ---------------------------------------------------------------------


async def test_the_unseen_notices_of_a_user_are_listed_oldest_first_with_their_questions(
    uow_factory: UowFactory, world: World
) -> None:
    """The repository itself: by `created_at`, then by id, never somebody else's and
    never one that is marked."""
    async with uow_factory() as uow:
        later = await uow.markets.add(_market(world.user.id, question="Asked later?"))
        twin = await uow.markets.add(_market(world.user.id, question="Asked at the same time?"))
        seen = await uow.markets.add(_market(world.user.id, question="Seen already?"))

    def notice(market: Market, user: User, at: datetime, **more: Any) -> Notice:
        return Notice(
            id=uuid7(),
            user_id=user.id,
            kind=NoticeKind.PROPOSAL_APPROVED,
            market_id=market.id,
            created_at=at,
            **more,
        )

    first_twin = notice(later, world.user, NOW + DAY)
    second_twin = notice(twin, world.user, NOW + DAY)
    oldest = notice(world.market, world.user, NOW)
    await _insert(
        uow_factory,
        [
            second_twin,
            first_twin,
            oldest,
            notice(seen, world.user, NOW - DAY, seen_at=NOW),
            notice(world.market, world.admin, NOW - DAY),
        ],
    )

    async with uow_factory() as uow:
        unseen = await uow.notices.list_unseen(world.user.id)
        others = await uow.notices.list_unseen(world.admin.id)
        nobodys = await uow.notices.list_unseen(uuid7())

    assert first_twin.id < second_twin.id
    assert [(row.id, question) for row, question in unseen] == [
        (oldest.id, world.market.question),
        (first_twin.id, "Asked later?"),
        (second_twin.id, "Asked at the same time?"),
    ]
    assert [question for _, question in others] == [world.market.question]
    assert nobodys == []


async def test_a_notice_is_marked_seen_once_and_only_by_its_user(
    uow_factory: UowFactory, world: World
) -> None:
    notice = Notice(
        user_id=world.user.id,
        kind=NoticeKind.PROPOSAL_APPROVED,
        market_id=world.market.id,
        created_at=NOW,
    )
    await _insert(uow_factory, [notice])

    async with uow_factory() as uow:
        assert not await uow.notices.mark_seen(notice.id, world.admin.id, NOW + DAY)
        assert not await uow.notices.mark_seen(uuid7(), world.user.id, NOW + DAY)
        assert await uow.notices.get_for_user(notice.id, world.admin.id) is None
        assert await uow.notices.mark_seen(notice.id, world.user.id, NOW + DAY)
        assert not await uow.notices.mark_seen(notice.id, world.user.id, NOW + 2 * DAY)

    async with uow_factory() as uow:
        found = await uow.notices.get_for_user(notice.id, world.user.id)
        assert found is not None
        stored, question = found
        assert (stored.seen_at, question) == (NOW + DAY, world.market.question)
        assert await uow.notices.list_unseen(world.user.id) == []


async def test_the_app_role_reads_writes_and_marks_notices(
    uow_factory: UowFactory, database: Database, settings: Settings, world: World
) -> None:
    """`uow_factory` runs on the app role: the baseline granted it the table like every
    other one that was there when it ran."""
    async with uow_factory() as uow:
        await uow.notices.add(
            Notice(
                user_id=world.user.id,
                kind=NoticeKind.PROPOSAL_APPROVED,
                market_id=world.market.id,
                created_at=NOW,
            )
        )
        ((written, _),) = await uow.notices.list_unseen(world.user.id)
        assert await uow.notices.mark_seen(written.id, world.user.id, NOW)

    async with database.engine.connect() as connection:
        role = await connection.scalar(text("SELECT current_user"))
        marked = await connection.scalar(
            select(func.count()).select_from(Notice).where(Notice.seen_at.is_not(None))
        )
    assert (role, marked) == (settings.postgres.app_user, 1)


@pytest.mark.parametrize("status", [CodeStatus.REDEEMED, CodeStatus.REVOKED])
async def test_a_code_cannot_be_redeemed_or_revoked_by_a_statement_that_keeps_it(
    uow_factory: UowFactory, world: World, status: CodeStatus
) -> None:
    """Only an ACTIVE row holds its code. An UPDATE that takes a code out of ACTIVE and
    leaves the code in the row is refused, so the two can only change in one statement."""
    await _insert(uow_factory, [_code(world, "KEPT")])

    async def change(*, clearing_the_code: bool) -> None:
        async with uow_factory() as uow:
            code = await uow.codes.get_by_hash(sha256_hex("KEPT"), for_update=True)
            assert code is not None
            code.status = status
            if status is CodeStatus.REDEEMED:
                code.redeemed_by, code.redeemed_at = world.user.id, NOW
            if clearing_the_code:
                code.code = None

    with pytest.raises(IntegrityError, match="ck_codes_code_iff_active"):
        await change(clearing_the_code=False)
    async with uow_factory() as uow:
        assert await uow.codes.list_active_codes(world.batch.id) == ["KEPT"]

    await change(clearing_the_code=True)
    async with uow_factory() as uow:
        stored = await uow.codes.get_by_hash(sha256_hex("KEPT"))
        assert stored is not None
        assert (stored.status, stored.code) == (status, None)


@pytest.mark.parametrize("status", [MarketStatus.PROPOSED, MarketStatus.REJECTED])
async def test_market_needs_no_liquidity_before_approval(
    uow_factory: UowFactory, world: World, status: MarketStatus
) -> None:
    proposal = _market(world.user.id, status=status, tier=None, budget_micro=0, b_micro=0)
    await _insert(uow_factory, [proposal])
    assert (proposal.q_yes_micro, proposal.q_no_micro, proposal.version) == (0, 0, 0)


async def test_a_sell_may_bring_in_nothing(uow_factory: UowFactory, world: World) -> None:
    """A.4: the proceeds of a sell can round down to 0; the trade is still recorded."""
    await _insert(uow_factory, [_trade(world, side=TradeSide.SELL, amount_micro=0)])


@pytest.mark.parametrize("column", ["b_micro", "budget_micro"])
async def test_budget_and_b_cannot_change_while_a_market_is_open(
    uow_factory: UowFactory, world: World, column: str
) -> None:
    """SPEC §5: both are immutable while the market is OPEN."""

    async def change() -> None:
        async with uow_factory() as uow:
            market = await uow.markets.get_for_update(world.market.id)
            assert market is not None
            setattr(market, column, getattr(market, column) * 2)

    with pytest.raises(IntegrityError, match="is OPEN: its budget and b cannot change"):
        await change()

    async with uow_factory() as uow:
        market = await uow.markets.get(world.market.id)
        assert market is not None
        assert (market.budget_micro, market.b_micro) == (6_931_472, 10 * CR)


async def test_budget_and_b_can_change_once_a_market_is_halted(
    uow_factory: UowFactory, world: World
) -> None:
    """SPEC §5: "Changing them requires HALT"; trading it does not touch them at all."""
    async with uow_factory() as uow:
        market = await uow.markets.get_for_update(world.market.id)
        assert market is not None
        market.q_yes_micro += CR
        market.version += 1
        market.status = MarketStatus.HALTED
    async with uow_factory() as uow:
        market = await uow.markets.get_for_update(world.market.id)
        assert market is not None
        market.budget_micro, market.b_micro = 13_862_944, 20 * CR

    async with uow_factory() as uow:
        market = await uow.markets.get(world.market.id)
        assert market is not None
        assert (market.budget_micro, market.b_micro) == (13_862_944, 20 * CR)


async def test_approval_sets_budget_and_b_as_it_opens_the_market(
    uow_factory: UowFactory, world: World
) -> None:
    proposal = _market(
        world.user.id, status=MarketStatus.PROPOSED, tier=None, budget_micro=0, b_micro=0
    )
    await _insert(uow_factory, [proposal])

    async with uow_factory() as uow:
        market = await uow.markets.get_for_update(proposal.id)
        assert market is not None
        market.status = MarketStatus.OPEN
        market.tier = MarketTier.TIGHT
        market.budget_micro, market.b_micro = 3_465_736, 5 * CR

    async with uow_factory() as uow:
        opened = await uow.markets.get(proposal.id)
        assert opened is not None
        assert (opened.status, opened.b_micro) == (MarketStatus.OPEN, 5 * CR)


@pytest.mark.parametrize(
    ("column", "value"),
    [("status", "BANNED"), ("degree_level", "KINDERGARTEN"), ("role", "MODERATOR")],
)
async def test_enum_columns_reject_unknown_values(
    owner_engine: AsyncEngine, world: World, column: str, value: str
) -> None:
    async def write() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(text(f"UPDATE users SET {column} = :value"), {"value": value})

    with pytest.raises(IntegrityError, match=f"ck_users_{column}"):
        await write()


@pytest.mark.parametrize("column", ["role", "policy_accepted_at"])
async def test_no_user_is_stored_without_a_role_or_without_having_accepted_the_policy(
    uow_factory: UowFactory, column: str
) -> None:
    """Every profile starts with a role, and there is no profile of somebody who did not
    accept the usage policy: both columns are NOT NULL."""
    with pytest.raises(IntegrityError, match=f'null value in column "{column}"'):
        await _insert(uow_factory, [_user("bob", **{column: None})])

    async with uow_factory() as uow:
        assert await uow.users.get_by_username("bob") is None


async def test_a_user_keeps_the_role_and_the_instant_of_the_policy_it_was_stored_with(
    uow_factory: UowFactory,
) -> None:
    accepted_at = NOW - DAY
    await _insert(
        uow_factory,
        [
            _user("bea", role=UserRole.BARISTA, policy_accepted_at=accepted_at),
            _user("ada", role=UserRole.ADMIN),
        ],
    )

    async with uow_factory() as uow:
        bea, ada = await uow.users.get_by_username("bea"), await uow.users.get_by_username("ada")
    assert bea is not None
    assert ada is not None
    assert (bea.role, bea.policy_accepted_at, bea.created_at) == (
        UserRole.BARISTA,
        accepted_at,
        NOW,
    )
    assert (ada.role, ada.policy_accepted_at) == (UserRole.ADMIN, NOW)


# --- Unit of work and locking ---------------------------------------------------------


async def test_unit_of_work_commits_on_exit_and_rolls_back_on_error(
    uow_factory: UowFactory,
) -> None:
    async def fail_after_insert() -> None:
        async with uow_factory() as uow:
            await uow.users.add(_user("bob"))
            raise RuntimeError("the service gave up")

    async with uow_factory() as uow:
        await uow.users.add(_user("alice"))
    with pytest.raises(RuntimeError, match="gave up"):
        await fail_after_insert()

    async with uow_factory() as uow:
        assert [user.email for user in await uow.users.list_all()] == ["alice@ethz.ch"]


async def test_market_lock_makes_a_second_transaction_wait(
    uow_factory: UowFactory, world: World
) -> None:
    async def trade_later() -> int:
        async with uow_factory() as uow:
            market = await uow.markets.get_for_update(world.market.id)
            assert market is not None
            market.version += 1
            return market.version

    async with uow_factory() as first:
        market = await first.markets.get_for_update(world.market.id)
        assert market is not None
        second = asyncio.create_task(trade_later())
        await asyncio.sleep(BLOCKED_FOR)
        assert not second.done()
        market.version += 1

    # The waiter computed on the committed version 1, not on the 0 it would have read
    # without the lock.
    assert await asyncio.wait_for(second, UNBLOCKED_WITHIN) == 2


async def test_locking_read_replaces_a_stale_copy(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        stale = await uow.markets.get(world.market.id)
        assert stale is not None
        assert stale.q_yes_micro == 0

        async with uow_factory() as other:
            theirs = await other.markets.get_for_update(world.market.id)
            assert theirs is not None
            theirs.q_yes_micro = 1_900_000

        locked = await uow.markets.get_for_update(world.market.id)
        assert locked is stale
        assert locked.q_yes_micro == 1_900_000


async def test_row_lock_does_not_block_rows_that_reference_it(
    uow_factory: UowFactory, world: World
) -> None:
    grant = Grant(user_id=world.user.id, period="2026-11", created_at=NOW)
    async with uow_factory() as uow:
        assert await uow.users.get(world.user.id, for_update=True) is not None
        # The foreign-key check on grants.user_id must not wait for the lock above.
        await asyncio.wait_for(_insert(uow_factory, [grant]), UNBLOCKED_WITHIN)


async def test_lock_many_returns_accounts_in_ascending_id_order(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        pnl = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
        wanted = [world.market_account.id, pnl.id, world.user_account.id]
        locked = await uow.accounts.lock_many(wanted)
        assert [account.id for account in locked] == sorted(wanted)


async def test_advisory_lock_serialises_transactions(uow_factory: UowFactory) -> None:
    async def take(name: str) -> None:
        async with uow_factory() as uow:
            await uow.advisory_lock(name)

    async with uow_factory() as first:
        await first.advisory_lock("market-approval")
        await asyncio.wait_for(take("code-issuance"), UNBLOCKED_WITHIN)
        second = asyncio.create_task(take("market-approval"))
        await asyncio.sleep(BLOCKED_FOR)
        assert not second.done()

    await asyncio.wait_for(second, UNBLOCKED_WITHIN)


async def test_lock_user_accounts_locks_the_accounts_of_those_users_in_id_order(
    uow_factory: UowFactory, world: World
) -> None:
    issuance = await _system_account_id(uow_factory, AccountKind.VIS_ISSUANCE)
    async with uow_factory() as uow:
        others = [await uow.users.add(_user(name)) for name in ("bob", "carla", "dan")]
        for user in others[:2]:
            await uow.accounts.create_user_account(user.id)

    async with uow_factory() as uow:
        wanted = [others[1].id, world.user.id, others[0].id, others[2].id]
        locked = await uow.accounts.lock_user_accounts(wanted)
        # Dan has no account; the system and market accounts are nobody's.
        assert {account.owner_id for account in locked} == set(wanted) - {others[2].id}
        assert [account.id for account in locked] == sorted(account.id for account in locked)
        assert await uow.accounts.lock_user_accounts([]) == []

        payment = asyncio.create_task(_move(uow_factory, issuance, locked[0].id, CR))
        await asyncio.sleep(BLOCKED_FOR)
        assert not payment.done(), "a posting to a locked account waits for the lock"

    await asyncio.wait_for(payment, UNBLOCKED_WITHIN)
    async with uow_factory() as uow:
        assert await uow.accounts.sum_balance_by_kind(AccountKind.USER) == CR


async def test_a_posting_moves_the_balance_and_reload_many_shows_it(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE, for_update=True)
        account = await uow.accounts.get_user_account(world.user.id, for_update=True)
        assert account is not None

        await _post(uow, "grant:alice:SIGNUP", {issuance.id: -3 * CR, account.id: 3 * CR})

        # The database moved both balances; the copies loaded before do not know yet.
        assert (account.balance_micro, issuance.balance_micro) == (0, 0)
        assert await uow.accounts.sum_balance_by_kind(AccountKind.USER) == 3 * CR
        assert await uow.accounts.total_balance() == 0

        reloaded = await uow.accounts.reload_many([account.id, issuance.id])
        assert {row.id for row in reloaded} == {account.id, issuance.id}
        assert (account.balance_micro, issuance.balance_micro) == (3 * CR, -3 * CR)


async def test_postings_of_concurrent_transactions_add_up(
    uow_factory: UowFactory, world: World
) -> None:
    """The addition happens in the database, on the row as it is at that moment."""
    issuance = await _system_account_id(uow_factory, AccountKind.VIS_ISSUANCE)

    await asyncio.gather(
        *(_move(uow_factory, issuance, world.user_account.id, CR) for _ in range(8))
    )

    async with uow_factory() as uow:
        account = await uow.accounts.get(world.user_account.id)
        assert account is not None
        assert account.balance_micro == 8 * CR
        assert await uow.accounts.total_balance() == 0


# --- Idempotency and outbox -----------------------------------------------------------


async def test_idempotency_claim_returns_none_first_and_the_row_afterwards(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        claimed = await uow.idempotency.claim(
            world.user.id, IDEMPOTENCY_KEY, sha256_hex("buy 1 CR"), NOW
        )
        assert claimed is None
        await uow.idempotency.store_response(world.user.id, IDEMPOTENCY_KEY, {"trade": "t1"})

    async with uow_factory() as uow:
        stored = await uow.idempotency.claim(
            world.user.id, IDEMPOTENCY_KEY, sha256_hex("buy 2 CR"), NOW + DAY
        )
        other_user = await uow.idempotency.claim(
            world.admin.id, IDEMPOTENCY_KEY, sha256_hex("buy 1 CR"), NOW
        )

    assert stored is not None
    assert stored.request_hash == sha256_hex("buy 1 CR")
    assert stored.response_json == {"trade": "t1"}
    assert stored.created_at == NOW
    assert other_user is None


async def test_rolled_back_idempotency_claim_is_free_again(
    uow_factory: UowFactory, world: World
) -> None:
    async def claim(uow: UnitOfWork) -> IdempotencyKey | None:
        return await uow.idempotency.claim(
            world.user.id, IDEMPOTENCY_KEY, sha256_hex("buy 1 CR"), NOW
        )

    async def claim_then_fail() -> None:
        async with uow_factory() as uow:
            assert await claim(uow) is None
            raise RuntimeError("the trade failed")

    with pytest.raises(RuntimeError, match="trade failed"):
        await claim_then_fail()

    async with uow_factory() as uow:
        assert await claim(uow) is None


async def test_concurrent_idempotency_claim_waits_for_the_first_request(
    uow_factory: UowFactory, world: World
) -> None:
    async def retry() -> IdempotencyKey | None:
        async with uow_factory() as uow:
            return await uow.idempotency.claim(
                world.user.id, IDEMPOTENCY_KEY, sha256_hex("buy 1 CR"), NOW
            )

    async with uow_factory() as first:
        assert (
            await first.idempotency.claim(
                world.user.id, IDEMPOTENCY_KEY, sha256_hex("buy 1 CR"), NOW
            )
            is None
        )
        second = asyncio.create_task(retry())
        await asyncio.sleep(BLOCKED_FOR)
        assert not second.done()
        await first.idempotency.store_response(world.user.id, IDEMPOTENCY_KEY, {"trade": "t1"})

    stored = await asyncio.wait_for(second, UNBLOCKED_WITHIN)
    assert stored is not None
    assert stored.response_json == {"trade": "t1"}


async def test_claim_unpublished_skips_rows_locked_by_another_transaction(
    uow_factory: UowFactory,
) -> None:
    async with uow_factory() as uow:
        for version in range(3):
            await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, {"version": version}, NOW)

    async with uow_factory() as first, uow_factory() as second:
        held = await first.outbox.claim_unpublished(2)
        others = await second.outbox.claim_unpublished(2)
        assert [event.payload["version"] for event in held] == [0, 1]
        assert [event.payload["version"] for event in others] == [2]
        await first.outbox.mark_published([event.id for event in held], NOW)

    async with uow_factory() as uow:
        unpublished = await uow.outbox.claim_unpublished(10)
        assert [event.payload["version"] for event in unpublished] == [2]
        assert unpublished[0].topic is OutboxTopic.QUOTE_UPDATED


# --- Repository queries ---------------------------------------------------------------


async def test_user_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        carla = await uow.users.add(_user("carla", username="carla_m"))

    async with uow_factory() as uow:
        assert (await uow.users.get(carla.id)) is not None
        by_email = await uow.users.get_by_email("carla@ethz.ch", for_update=True)
        assert by_email is not None
        assert by_email.id == carla.id
        # The address is stored as the id is read; nothing here folds case or a domain.
        assert await uow.users.get_by_email("Carla@ethz.ch") is None
        assert await uow.users.get_by_email("carla@student.ethz.ch") is None
        assert await uow.users.get_by_email("nobody@ethz.ch") is None

        holder = await uow.users.get_by_username("carla_m")
        assert holder is not None
        assert (holder.id, holder.username) == (carla.id, "carla_m")
        # A username has one spelling, so the match is exact: no case is folded.
        for other_spelling in ("Carla_M", "CARLA_M", "carla_m ", "carla"):
            assert await uow.users.get_by_username(other_spelling) is None
        # An underscore is a character of the name, not a wildcard.
        assert await uow.users.get_by_username("carlaxm") is None

        everyone = await uow.users.list_all()
        assert [user.id for user in everyone] == [world.user.id, world.admin.id, carla.id]


async def test_a_user_row_holds_every_label_of_the_degree_catalogue_as_written(
    uow_factory: UowFactory,
) -> None:
    """The label is the stored value: the longest one fits the column, and what ETH
    writes with an en dash or an umlaut comes back character for character."""
    labels = [(entry.level, label) for entry in CATALOGUE for label in entry.programmes]
    longest = max(labels, key=lambda pair: len(pair[1]))
    outside_ascii = [pair for pair in labels if not pair[1].isascii()]
    assert len(outside_ascii) >= 2

    stored: dict[UUID, str] = {}
    async with uow_factory() as uow:
        for number, (level, label) in enumerate([longest, *outside_ascii]):
            user = await uow.users.add(
                _user(f"student{number}", degree_level=level, degree_programme=label)
            )
            stored[user.id] = label

    async with uow_factory() as uow:
        for user_id, label in stored.items():
            row = await uow.users.get(user_id)
            assert row is not None
            assert row.degree_programme == label


async def test_a_username_is_stored_as_written_up_to_32_characters(
    uow_factory: UowFactory,
) -> None:
    of_32_characters = "l" + "o" * 29 + "ng"
    assert len(of_32_characters) == 32
    async with uow_factory() as uow:
        longest = await uow.users.add(_user("lena", username=of_32_characters))
        mixed = await uow.users.add(_user("max", username="max_muster_99"))

    async with uow_factory() as uow:
        for user, username in ((longest, of_32_characters), (mixed, "max_muster_99")):
            row = await uow.users.get(user.id)
            assert row is not None
            assert row.username == username


async def test_a_username_is_free_again_once_its_holder_takes_another(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        alice = await uow.users.get(world.user.id, for_update=True)
        assert alice is not None
        alice.username = "alice_2"

    async with uow_factory() as uow:
        assert await uow.users.get_by_username("alice") is None
        alicia = await uow.users.add(_user("alicia", username="alice"))
        holder = await uow.users.get_by_username("alice")
        assert holder is not None
        assert holder.id == alicia.id


@pytest.mark.parametrize(
    ("username", "constraint"),
    [("admin", "uq_users_username"), ("Alice", "ck_users_username_format")],
    ids=["a-name-somebody-holds", "a-name-with-a-capital-letter"],
)
async def test_a_rename_is_held_to_the_rules_a_new_username_is_held_to(
    uow_factory: UowFactory, world: World, username: str, constraint: str
) -> None:
    """The backstop behind the profile service: an UPDATE is refused like an INSERT."""

    async def rename() -> None:
        async with uow_factory() as uow:
            alice = await uow.users.get(world.user.id, for_update=True)
            assert alice is not None
            alice.username = username

    with pytest.raises(IntegrityError, match=constraint):
        await rename()

    async with uow_factory() as uow:
        holder = await uow.users.get_by_username("alice")
        assert holder is not None
        assert holder.id == world.user.id


async def test_market_queries(uow_factory: UowFactory, world: World) -> None:
    live = (MarketStatus.OPEN, MarketStatus.HALTED)
    async with uow_factory() as uow:
        halted = await uow.markets.add(
            _market(
                world.admin.id,
                status=MarketStatus.HALTED,
                budget_micro=8 * CR,
                q_yes_micro=3 * CR,
                q_no_micro=CR,
                close_at=NOW + DAY,
                created_at=NOW + DAY,
            )
        )
        proposal = await uow.markets.add(
            _market(world.user.id, status=MarketStatus.PROPOSED, tier=None, b_micro=0)
        )
        halted_account = await uow.accounts.create_market_account(halted.id)
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        await _post(uow, "trade:paid-in", {issuance.id: -2 * CR, halted_account.id: 2 * CR})
        await uow.resolutions.add(
            Resolution(
                market_id=halted.id,
                outcome=ResolutionOutcome.VOID,
                evidence="The Mensa was closed.",
                resolved_by=world.admin.id,
                created_at=NOW,
            )
        )

    async with uow_factory() as uow:
        assert await uow.markets.get(uuid7()) is None
        assert [m.id for m in await uow.markets.list_by_statuses(live)] == [
            halted.id,
            world.market.id,
        ]
        assert await uow.markets.list_by_statuses([]) == []
        assert await uow.markets.count_by_statuses(live) == 2
        assert await uow.markets.sum_budget_by_statuses(live) == 8 * CR + 6_931_472
        assert await uow.markets.sum_budget_by_statuses([MarketStatus.VOID]) == 0
        # max(3, 1) - 2 for the halted market, max(0, 0) - 0 for the open one.
        assert await uow.markets.sum_exposure_by_statuses(live) == CR
        assert await uow.markets.count_created_by_since(world.user.id, NOW) == 2
        assert await uow.markets.count_created_by_since(world.user.id, NOW + DAY) == 0
        assert {m.id for m in await uow.markets.list_created_by(world.user.id)} == {
            world.market.id,
            proposal.id,
        }
        resolution = await uow.resolutions.get_by_market(halted.id)
        assert resolution is not None
        assert resolution.outcome is ResolutionOutcome.VOID
        assert await uow.resolutions.get_by_market(world.market.id) is None

        wanted = [halted.id, world.market.id, proposal.id, uuid7()]
        markets = await uow.markets.get_many(wanted)
        assert set(markets) == set(wanted[:3])
        assert markets[halted.id] is await uow.markets.get(halted.id)
        assert await uow.markets.get_many([]) == {}
        resolutions = await uow.resolutions.get_many_by_market(wanted)
        assert list(resolutions) == [halted.id]
        assert resolutions[halted.id] is resolution
        accounts = await uow.accounts.list_market_accounts(wanted)
        assert {market_id: a.balance_micro for market_id, a in accounts.items()} == {
            halted.id: 2 * CR,
            world.market.id: 0,
        }


async def test_a_market_read_as_stored_holds_what_its_transaction_wrote_as_the_database_keeps_it(
    uow_factory: UowFactory, world: World
) -> None:
    """`markets.get_as_stored` is how the answer of a write reads its market before the
    commit. A change the unit of work has not sent yet is sent, not lost; the copy it
    holds is filled from the row, so an instant written at an offset reads in UTC, as
    it will to every later reader; and the read takes no lock and waits for none."""
    kolkata = timezone(timedelta(hours=5, minutes=30))
    edited = "Will the Polymensa serve Älplermagronen next Friday?"

    async with uow_factory() as uow:
        held = await uow.markets.get_for_update(world.market.id)
        assert held is not None
        held.question = edited
        held.close_at = (NOW + 3 * DAY).astimezone(kolkata)
        assert held.close_at.utcoffset() == timedelta(hours=5, minutes=30)

        stored = await uow.markets.get_as_stored(world.market.id)

        assert stored is held, "the copy of the unit of work, brought up to date"
        assert stored.question == edited
        assert stored.close_at == NOW + 3 * DAY
        assert stored.close_at.utcoffset() == timedelta(0)
        assert await uow.markets.get_as_stored(uuid7()) is None
        # Somebody else reads the row as it is committed, and is not kept waiting by
        # the lock this transaction holds on it.
        async with uow_factory() as elsewhere:
            committed = await asyncio.wait_for(
                elsewhere.markets.get_as_stored(world.market.id), timeout=5
            )
        assert committed is not None
        assert committed is not held
        assert (committed.question, committed.close_at) == (world.market.question, NOW + 7 * DAY)

    async with uow_factory() as uow:
        afterwards = await uow.markets.get_as_stored(world.market.id)
        assert afterwards is not None
        assert (afterwards.question, afterwards.close_at) == (edited, NOW + 3 * DAY)


async def test_position_queries(uow_factory: UowFactory, world: World) -> None:
    def position(user: User, outcome: Outcome, shares_micro: int) -> Position:
        return Position(
            user_id=user.id,
            market_id=world.market.id,
            outcome=outcome,
            shares_micro=shares_micro,
            cost_micro=shares_micro // 2,
        )

    async with uow_factory() as uow:
        assert await uow.positions.sum_shares_by_outcome(world.market.id) == {
            Outcome.YES: 0,
            Outcome.NO: 0,
        }
        await uow.positions.add(position(world.user, Outcome.YES, 3 * CR))
        await uow.positions.add(position(world.user, Outcome.NO, CR))
        await uow.positions.add(position(world.admin, Outcome.YES, 2 * CR))

    async with uow_factory() as uow:
        held = await uow.positions.get(world.user.id, world.market.id, Outcome.YES, for_update=True)
        assert held is not None
        assert (held.shares_micro, held.cost_micro, held.proceeds_micro) == (3 * CR, 3 * CR // 2, 0)
        assert await uow.positions.get(world.admin.id, world.market.id, Outcome.NO) is None

        mine = await uow.positions.list_for_user_market(
            world.user.id, world.market.id, for_update=True
        )
        assert {p.outcome for p in mine} == {Outcome.YES, Outcome.NO}
        assert len(await uow.positions.list_for_market(world.market.id, for_update=True)) == 3
        assert len(await uow.positions.list_for_user(world.admin.id)) == 1
        assert await uow.positions.sum_shares_by_outcome(world.market.id) == {
            Outcome.YES: 5 * CR,
            Outcome.NO: CR,
        }


async def test_net_stake_totals_add_up_per_market_what_a_void_would_refund(
    uow_factory: UowFactory, world: World
) -> None:
    """Per trader max(0, cost - proceeds) over both outcomes, then the sum per market:
    a trader in profit counts as nothing, not as a negative amount (SPEC §6)."""

    def position(
        user: User, market: Market, outcome: Outcome, cost: int, proceeds: int
    ) -> Position:
        return Position(
            user_id=user.id,
            market_id=market.id,
            outcome=outcome,
            shares_micro=CR,
            cost_micro=cost,
            proceeds_micro=proceeds,
        )

    async with uow_factory() as uow:
        second = await uow.markets.add(_market(world.admin.id))
        untouched = await uow.markets.add(_market(world.admin.id))
        gone = await uow.markets.add(_market(world.admin.id))
        rows = [
            # Out of pocket by 80 - 30 + 20 = 70 over both outcomes.
            position(world.user, world.market, Outcome.YES, 80 * CR, 30 * CR),
            position(world.user, world.market, Outcome.NO, 20 * CR, 0),
            # In profit by 50: a void gives this trader nothing and takes nothing back.
            position(world.admin, world.market, Outcome.YES, 40 * CR, 90 * CR),
            # One outcome in profit, the other not: 10 - 25 + 30 - 5 = 10 in all.
            position(world.user, second, Outcome.YES, 10 * CR, 25 * CR),
            position(world.user, second, Outcome.NO, 30 * CR, 5 * CR),
            position(world.admin, second, Outcome.NO, 1, 0),
            # Everybody sold out above their cost.
            position(world.user, gone, Outcome.YES, 5 * CR, 6 * CR),
        ]
        for row in rows:
            await uow.positions.add(row)

    async with uow_factory() as uow:
        every_market = [world.market.id, second.id, untouched.id, gone.id, uuid7()]
        totals = await uow.positions.net_stake_totals(every_market)

        assert totals == {world.market.id: 70 * CR, second.id: 10 * CR + 1, gone.id: 0}
        assert all(type(total) is int for total in totals.values())
        assert await uow.positions.net_stake_totals([second.id]) == {second.id: 10 * CR + 1}
        assert await uow.positions.net_stake_totals([untouched.id]) == {}
        assert await uow.positions.net_stake_totals([]) == {}


async def test_trade_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        trades = [
            await uow.trades.add(
                _trade(
                    world,
                    market_version=version,
                    created_at=NOW + version * DAY,
                    p_yes_after_micro=500_000 + version,
                )
            )
            for version in (1, 2, 3)
        ]
        elsewhere = await uow.markets.add(_market(world.admin.id))
        await uow.trades.add(
            _trade(world, market_id=elsewhere.id, user_id=world.admin.id, market_version=9)
        )
    ids = [trade.id for trade in trades]
    prices = [(version, NOW + version * DAY, 500_000 + version) for version in (1, 2, 3)]

    async with uow_factory() as uow:
        newest = await uow.trades.list_for_user(world.user.id, limit=2)
        assert [t.id for t in newest] == [ids[2], ids[1]]
        older = await uow.trades.list_for_user(world.user.id, limit=2, before_id=ids[1])
        assert [t.id for t in older] == [ids[0]]

        history = await uow.trades.list_for_market(world.market.id, limit=10)
        assert [t.market_version for t in history] == [1, 2, 3]
        recent = await uow.trades.list_for_market(world.market.id, limit=10, since=NOW + 2 * DAY)
        assert [t.market_version for t in recent] == [2, 3]
        page = await uow.trades.list_for_market(world.market.id, limit=1, after_version=1)
        assert [t.market_version for t in page] == [2]

        # What a chart reads of the record: the price each trade left, newest first.
        # With more trades than the limit it loses the oldest, never the newest.
        points = world.market.id
        assert await uow.trades.list_price_points(points, after_version=0, limit=10) == prices[::-1]
        assert await uow.trades.list_price_points(points, after_version=0, limit=2) == prices[:0:-1]
        assert await uow.trades.list_price_points(points, after_version=2, limit=10) == prices[2:]
        assert await uow.trades.list_price_points(points, after_version=3, limit=10) == []
        assert await uow.trades.list_price_points(uuid7(), after_version=0, limit=10) == []

        assert await uow.trades.exists_for_user_market(world.user.id, world.market.id)
        assert not await uow.trades.exists_for_user_market(world.admin.id, world.market.id)


async def test_account_and_journal_queries(uow_factory: UowFactory, world: World) -> None:
    for month in ("SIGNUP", "2026-11", "2026-12"):
        await _grant(uow_factory, world, CR, f"grant:alice:{month}")

    async with uow_factory() as uow:
        assert (await uow.accounts.get_user_account(world.user.id)) is not None
        assert await uow.accounts.get_user_account(world.admin.id) is None
        market_account = await uow.accounts.get_market_account(world.market.id, for_update=True)
        assert market_account is not None
        assert market_account.id == world.market_account.id
        assert await uow.accounts.sum_balance_by_kind(AccountKind.VIS_ISSUANCE) == -3 * CR
        with pytest.raises(ValueError, match="not a system account"):
            await uow.accounts.get_system_account(AccountKind.USER)

        newest = await uow.journal.list_postings_for_account(world.user_account.id, limit=2)
        assert [entry.idempotency_key for _, entry in newest] == [
            "grant:alice:2026-12",
            "grant:alice:2026-11",
        ]
        assert all(posting.amount_micro == CR for posting, _ in newest)
        older = await uow.journal.list_postings_for_account(
            world.user_account.id, limit=2, before_id=newest[-1][0].id
        )
        assert [entry.idempotency_key for _, entry in older] == ["grant:alice:SIGNUP"]


async def test_grant_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        grant = await uow.grants.insert_if_absent(world.user.id, "2026-11", NOW)
        assert grant is not None
        assert grant.entry_id is None
        assert await uow.grants.insert_if_absent(world.user.id, "2026-11", NOW) is None
        assert await uow.grants.insert_if_absent(world.user.id, "SIGNUP", NOW) is not None
        assert await uow.grants.insert_if_absent(world.admin.id, "2026-11", NOW) is not None

        entry = await uow.journal.add_entry(_entry("grant:alice:2026-11"))
        await uow.grants.set_entry(grant.id, entry.id)
        assert grant.entry_id == entry.id
        assert await uow.grants.list_periods(world.user.id) == ["2026-11", "SIGNUP"]


async def test_code_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        await uow.codes.add_all([_code(world, code) for code in ("A", "B", "C")])
        expired = await uow.code_batches.add(
            CodeBatch(
                label="Last semester's quiz",
                value_micro=5 * CR,
                count=1,
                expires_at=NOW - DAY,
                one_per_user=False,
                created_by=world.admin.id,
                created_at=NOW - 40 * DAY,
            )
        )
        await uow.codes.add(_code(world, "OLD", batch_id=expired.id))

    async with uow_factory() as uow:
        # The codes of a batch that are ACTIVE, in the order they were made. Whether
        # the batch has expired is not this query's to say.
        assert await uow.codes.list_active_codes(world.batch.id) == ["A", "B", "C"]
        assert await uow.codes.list_active_codes(expired.id) == ["OLD"]
        assert await uow.codes.list_active_codes(uuid7()) == []

        code = await uow.codes.get_by_hash(sha256_hex("A"), for_update=True)
        assert code is not None
        assert not await uow.codes.user_has_redeemed_in_batch(world.user.id, world.batch.id)
        code.status = CodeStatus.REDEEMED
        code.redeemed_by = world.user.id
        code.redeemed_at = NOW
        code.code = None
        await uow.flush()
        assert await uow.codes.user_has_redeemed_in_batch(world.user.id, world.batch.id)
        assert not await uow.codes.user_has_redeemed_in_batch(world.admin.id, world.batch.id)
        assert await uow.codes.get_by_hash(sha256_hex("unknown")) is None
        assert await uow.codes.list_active_codes(world.batch.id) == ["B", "C"]

        # 3 codes of 2 CR this month; the 5 CR code is older and expired.
        assert await uow.codes.issued_value_since(NOW - DAY) == 6 * CR
        assert await uow.codes.issued_value_since(NOW - 60 * DAY) == 11 * CR
        assert await uow.codes.unredeemed_value(NOW) == 4 * CR

        assert await uow.codes.revoke(world.batch.id, [sha256_hex("A"), sha256_hex("B")]) == 1
        assert await uow.codes.list_active_codes(world.batch.id) == ["C"]
        assert await uow.codes.issued_value_since(NOW - DAY) == 4 * CR
        assert await uow.codes.revoke(world.batch.id, None) == 1
        assert await uow.codes.revoke(world.batch.id, None) == 0
        assert await uow.codes.unredeemed_value(NOW) == 0
        assert code.status is CodeStatus.REDEEMED
        # A revocation clears the code in the statement that revokes it: of this batch
        # nothing is kept, and the code of the other batch is where it was.
        assert await uow.codes.list_active_codes(world.batch.id) == []
        kept = await uow.session.execute(select(Code.status, Code.code).order_by(Code.id))
        assert [tuple(row) for row in kept] == [
            (CodeStatus.REDEEMED, None),
            (CodeStatus.REVOKED, None),
            (CodeStatus.REVOKED, None),
            (CodeStatus.ACTIVE, "OLD"),
        ]

        batch = await uow.code_batches.get(world.batch.id, for_update=True)
        assert batch is not None
        counts = await uow.code_batches.list_with_counts()
        assert [(c.batch.id, c.active, c.redeemed, c.revoked) for c in counts] == [
            (world.batch.id, 0, 1, 2),
            (expired.id, 1, 0, 0),
        ]
        one = await uow.code_batches.get_with_counts(world.batch.id)
        assert one is not None
        assert (one.batch, one.active, one.redeemed, one.revoked) == (batch, 0, 1, 2)
        assert await uow.code_batches.get_with_counts(uuid7()) is None


async def test_voucher_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        fresh = await uow.vouchers.add(_voucher(world, "FRESH"))
        overdue = await uow.vouchers.add(
            _voucher(
                world,
                "OVERDUE",
                product_id="iced-tea",
                product_name="Iced Tea",
                created_at=NOW - 31 * DAY,
                expires_at=NOW - DAY,
            )
        )
        await uow.vouchers.add(
            _voucher(
                world,
                "USED",
                status=VoucherStatus.REDEEMED,
                redeemed_at=NOW,
                redeemed_by=world.admin.id,
            )
        )

    async with uow_factory() as uow:
        voucher = await uow.vouchers.get_by_hash(sha256_hex("FRESH"), for_update=True)
        assert voucher is not None
        assert voucher.id == fresh.id
        assert (voucher.product_id, voucher.product_name) == ("mate", "Mate")
        assert await uow.vouchers.get_by_hash(sha256_hex("unknown")) is None
        assert await uow.vouchers.get_owned(fresh.id, world.user.id, for_update=True) is voucher
        assert await uow.vouchers.get_owned(overdue.id, world.user.id) is not None
        assert await uow.vouchers.get_owned(uuid7(), world.user.id) is None
        # Somebody else's voucher is found as little as one that does not exist.
        assert await uow.vouchers.get_owned(fresh.id, world.admin.id) is None
        assert await uow.vouchers.get_owned(fresh.id, world.admin.id, for_update=True) is None
        assert len(await uow.vouchers.list_for_user(world.user.id)) == 3
        assert await uow.vouchers.list_for_user(world.admin.id) == []
        refundable = await uow.vouchers.list_expired_active(world.user.id, NOW, for_update=True)
        assert [t.id for t in refundable] == [overdue.id]
        assert await uow.vouchers.pending_value() == 2 * CR
        assert await uow.vouchers.product_names([fresh.id, overdue.id, uuid7()]) == {
            fresh.id: "Mate",
            overdue.id: "Iced Tea",
        }
        assert await uow.vouchers.product_names([]) == {}


async def test_a_voucher_gets_another_code_and_is_found_under_that_one_alone(
    uow_factory: UowFactory, world: World
) -> None:
    """What replacing a code comes to in the database: one column of one row."""
    async with uow_factory() as uow:
        issued = await uow.vouchers.add(_voucher(world, "FIRST"))

    async with uow_factory() as uow:
        voucher = await uow.vouchers.get_owned(issued.id, world.user.id, for_update=True)
        assert voucher is not None
        voucher.code_hash = sha256_hex("SECOND")

    async with uow_factory() as uow:
        assert await uow.vouchers.get_by_hash(sha256_hex("FIRST")) is None
        found = await uow.vouchers.get_by_hash(sha256_hex("SECOND"))
        assert found is not None
        assert (found.id, found.status, found.expires_at) == (
            issued.id,
            VoucherStatus.ACTIVE,
            issued.expires_at,
        )


async def test_asking_for_somebody_elses_voucher_takes_no_lock_on_it(
    uow_factory: UowFactory, world: World
) -> None:
    """The row is selected by its id and its owner together. Whoever names a voucher
    that is not theirs holds nothing afterwards, so its owner and the barista who
    scans it never wait for a stranger."""
    async with uow_factory() as uow:
        issued = await uow.vouchers.add(_voucher(world, "MINE"))

    async def owner_locks() -> UUID:
        async with uow_factory() as uow:
            voucher = await uow.vouchers.get_owned(issued.id, world.user.id, for_update=True)
            assert voucher is not None
            return voucher.id

    async with uow_factory() as stranger:
        asked = await stranger.vouchers.get_owned(issued.id, world.admin.id, for_update=True)
        assert asked is None
        assert await asyncio.wait_for(owner_locks(), UNBLOCKED_WITHIN) == issued.id

    # The owner's own lock is one: a second transaction of theirs waits for it.
    async with uow_factory() as first:
        assert await first.vouchers.get_owned(issued.id, world.user.id, for_update=True)
        second = asyncio.create_task(owner_locks())
        await asyncio.sleep(BLOCKED_FOR)
        assert not second.done()

    assert await asyncio.wait_for(second, UNBLOCKED_WITHIN) == issued.id


async def test_a_voucher_keeps_a_product_as_long_as_its_columns_are_wide(
    uow_factory: UowFactory, world: World
) -> None:
    """`product_id` VARCHAR(64) and `product_name` VARCHAR(100), the lengths
    `VOUCHER_PRODUCTS` is held to when it is loaded."""
    longest_id, longest_name = "p" * PRODUCT_ID_MAX_LENGTH, "N" * PRODUCT_NAME_MAX_LENGTH
    async with uow_factory() as uow:
        kept = await uow.vouchers.add(
            _voucher(world, "WIDE", product_id=longest_id, product_name=longest_name)
        )

    async with uow_factory() as uow:
        assert await uow.vouchers.product_names([kept.id]) == {kept.id: longest_name}

    for too_wide in ({"product_id": longest_id + "p"}, {"product_name": longest_name + "N"}):
        with pytest.raises(DBAPIError, match="value too long"):
            await _insert(uow_factory, [_voucher(world, "WIDER", **too_wide)])
    for missing in ("product_id", "product_name"):
        with pytest.raises(IntegrityError, match=missing):
            await _insert(uow_factory, [_voucher(world, "BARE", **{missing: None})])


async def test_audit_log_and_alert_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        audit = await uow.audit.add(
            AuditLogEntry(
                actor_id=world.admin.id,
                action="market.halt",
                target=f"market:{world.market.id}",
                before={"status": "OPEN"},
                after={"status": "HALTED"},
                created_at=NOW,
            )
        )
        system = await uow.audit.add(
            AuditLogEntry(
                actor_id=None,
                action="market.halt",
                target=f"market:{world.market.id}",
                before=None,
                after=None,
                created_at=NOW,
            )
        )
        first = await uow.alerts.add(
            Alert(
                severity=AlertSeverity.CRITICAL,
                message="Invariant violated.",
                context={"invariant": "ledger_nets_to_zero"},
                created_at=NOW,
            )
        )
        second = await uow.alerts.add(
            Alert(
                severity=AlertSeverity.WARNING,
                message="Obligations high.",
                context={},
                created_at=NOW,
            )
        )
    assert (audit.id, system.id) == (1, 2)

    async with uow_factory() as uow:
        stored = await uow.session.get(AuditLogEntry, system.id)
        assert stored is not None
        assert (stored.actor_id, stored.before, stored.after) == (None, None, None)
        is_sql_null = await uow.session.scalar(
            select(AuditLogEntry.before.is_(None)).where(AuditLogEntry.id == system.id)
        )
        assert is_sql_null

        assert [a.id for a in await uow.alerts.list_unacknowledged()] == [second.id, first.id]
        assert await uow.alerts.acknowledge(first.id, world.admin.id, NOW + DAY)
        assert not await uow.alerts.acknowledge(first.id, world.user.id, NOW + 2 * DAY)
        assert not await uow.alerts.acknowledge(999, world.admin.id, NOW)
        assert [a.id for a in await uow.alerts.list_unacknowledged()] == [second.id]
        acked = await uow.alerts.get(first.id)
        assert acked is not None
        assert (acked.acked_by, acked.acked_at) == (world.admin.id, NOW + DAY)


async def test_conditional_update_changes_a_loaded_row_only_if_the_database_did(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        alert = await uow.alerts.add(
            Alert(severity=AlertSeverity.INFO, message="Look.", context={}, created_at=NOW)
        )

    async with uow_factory() as uow:
        stale = await uow.alerts.get(alert.id)
        assert stale is not None
        async with uow_factory() as other:
            assert await other.alerts.acknowledge(alert.id, world.admin.id, NOW)

        # This copy still looks unacknowledged, but the row is not: nothing may change.
        assert not await uow.alerts.acknowledge(alert.id, world.user.id, NOW + DAY)
        assert stale.acked_by is None


# --- The harness itself ---------------------------------------------------------------


async def test_reset_database_leaves_only_the_system_accounts(
    uow_factory: UowFactory, owner_engine: AsyncEngine, world: World
) -> None:
    await _grant(uow_factory, world, 5 * CR, "grant:alice:SIGNUP")
    async with uow_factory() as uow:
        await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, {"version": 0}, NOW)
        before = {a.kind: a.id for a in (await uow.session.scalars(select(Account))).all()}

    await reset_database(owner_engine)

    async with uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            rows = await uow.session.scalar(select(func.count()).select_from(table))
            assert rows == (4 if table.name == "accounts" else 0), table.name
        accounts = (await uow.session.scalars(select(Account))).all()
        assert {a.kind: a.id for a in accounts} == {
            kind: before[kind] for kind in SYSTEM_ACCOUNT_KINDS
        }
        assert await uow.accounts.total_balance() == 0
        event = await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, {"version": 0}, NOW)
        assert event.id == 1


# --- Audiences, streaks, questions, broadcasts and chat messages ----------------------

PROGRAMME: Final[str] = "BSc Computer Science"
OTHER_PROGRAMME: Final[str] = "MSc Data Science"


def _question(world: World, **overrides: Any) -> Question:
    values: dict[str, Any] = {
        "market_id": world.market.id,
        "asked_by": world.user.id,
        "text": "Which menu counts, the one at noon?",
        "created_at": NOW,
    }
    return Question(**(values | overrides))


def _broadcast(world: World, status: BroadcastStatus, **overrides: Any) -> Broadcast:
    """A broadcast of the world's market as a row of `status` reads when nothing is
    left out: decided unless it is REQUESTED, started once it is LIVE, ended once it
    is ENDED."""
    values: dict[str, Any] = {
        "id": uuid7(),
        "market_id": world.market.id,
        "broadcaster_id": world.user.id,
        "status": status,
        "requested_at": NOW,
    }
    if status is not BroadcastStatus.REQUESTED:
        values |= {"decided_by": world.admin.id, "decided_at": NOW}
    if status is BroadcastStatus.REFUSED:
        values |= {"refusal_reason": "Not this week."}
    if status in (BroadcastStatus.LIVE, BroadcastStatus.ENDED):
        values |= {"started_at": NOW, "last_chunk_at": NOW}
    if status is BroadcastStatus.ENDED:
        values |= {"ended_at": NOW + DAY, "end_reason": BroadcastEndReason.STALLED}
    return Broadcast(**(values | overrides))


def _chat_message(world: World, broadcast: Broadcast, **overrides: Any) -> ChatMessage:
    values: dict[str, Any] = {
        "market_id": world.market.id,
        "broadcast_id": broadcast.id,
        "user_id": world.user.id,
        "text": "Hopp!",
        "from_vis": False,
        "created_at": NOW,
    }
    return ChatMessage(**(values | overrides))


def _streak(world: World, **overrides: Any) -> Streak:
    values: dict[str, Any] = {
        "user_id": world.user.id,
        "current_days": 3,
        "longest_days": 5,
        "last_day": date(2026, 10, 9),
        "updated_at": NOW,
    }
    return Streak(**(values | overrides))


def _resumed(world: World, status: BroadcastStatus) -> list[Base]:
    """An ended broadcast, and one of `status` that runs on its approval."""
    ended = _broadcast(world, BroadcastStatus.ENDED)
    return [ended, _broadcast(world, status, resumed_from=ended.id)]


COLUMNS_OF_THE_ROUND: Final[dict[str, dict[str, tuple[str, bool]]]] = {
    "streaks": {
        "user_id": ("uuid", False),
        "current_days": ("bigint", False),
        "longest_days": ("bigint", False),
        "last_day": ("date", False),
        "updated_at": ("timestamp with time zone", False),
    },
    "questions": {
        "id": ("uuid", False),
        "market_id": ("uuid", False),
        "asked_by": ("uuid", False),
        "text": ("text", False),
        "created_at": ("timestamp with time zone", False),
        "answer_text": ("text", True),
        "answered_by": ("uuid", True),
        "answered_at": ("timestamp with time zone", True),
        "removed_by": ("uuid", True),
        "removed_at": ("timestamp with time zone", True),
    },
    "broadcasts": {
        "id": ("uuid", False),
        "market_id": ("uuid", False),
        "broadcaster_id": ("uuid", False),
        "status": ("character varying(32)", False),
        "requested_at": ("timestamp with time zone", False),
        "decided_by": ("uuid", True),
        "decided_at": ("timestamp with time zone", True),
        "refusal_reason": ("text", True),
        "resumed_from": ("uuid", True),
        "started_at": ("timestamp with time zone", True),
        "on_air_at": ("timestamp with time zone", True),
        "last_chunk_at": ("timestamp with time zone", True),
        "ended_at": ("timestamp with time zone", True),
        "ended_by": ("uuid", True),
        "end_reason": ("character varying(32)", True),
    },
    "chat_messages": {
        "id": ("uuid", False),
        "market_id": ("uuid", False),
        "broadcast_id": ("uuid", False),
        "user_id": ("uuid", False),
        "text": ("text", False),
        "from_vis": ("boolean", False),
        "created_at": ("timestamp with time zone", False),
    },
}
"""Every column of the four tables: its type as Postgres spells it, and whether it
takes NULL."""

ADDED_COLUMNS: Final[dict[tuple[str, str], tuple[str, bool]]] = {
    ("markets", "audience_programme"): ("character varying(100)", True),
    ("users", "degree_changed_at"): ("timestamp with time zone", True),
}

CHECKS_OF_THE_ROUND: Final[frozenset[str]] = frozenset(
    {
        "ck_markets_audience_programme_not_blank",
        "ck_streaks_current_days_positive",
        "ck_streaks_longest_not_below_current",
        "ck_questions_text_length",
        "ck_questions_answer_length",
        "ck_questions_answered_whole",
        "ck_questions_removed_whole",
        "ck_broadcasts_status",
        "ck_broadcasts_end_reason",
        "ck_broadcasts_decided_together",
        "ck_broadcasts_requested_is_undecided",
        "ck_broadcasts_refusal_has_reason",
        "ck_broadcasts_live_has_started",
        "ck_broadcasts_ended_is_complete",
        "ck_broadcasts_resumed_has_started",
        "ck_chat_messages_text_length",
    }
)

OPEN_BROADCASTS: Final[str] = (
    "WHERE ((status)::text = ANY ((ARRAY['REQUESTED'::character varying,"
    " 'APPROVED'::character varying, 'LIVE'::character varying])::text[]))"
)
"""The condition of the two partial indexes of `broadcasts`, as Postgres writes it back."""

INDEXES_OF_THE_ROUND: Final[dict[str, str]] = {
    "ix_markets_audience_programme_status_close_at": (
        "CREATE INDEX ix_markets_audience_programme_status_close_at ON public.markets"
        " USING btree (audience_programme, status, close_at)"
    ),
    "ix_trades_user_id_created_at": (
        "CREATE INDEX ix_trades_user_id_created_at ON public.trades"
        " USING btree (user_id, created_at)"
    ),
    "ix_questions_market_id_id": (
        "CREATE INDEX ix_questions_market_id_id ON public.questions USING btree (market_id, id)"
    ),
    "uq_broadcasts_market_open": (
        "CREATE UNIQUE INDEX uq_broadcasts_market_open ON public.broadcasts"
        f" USING btree (market_id) {OPEN_BROADCASTS}"
    ),
    "ix_broadcasts_open": (
        "CREATE INDEX ix_broadcasts_open ON public.broadcasts"
        f" USING btree (status, requested_at) {OPEN_BROADCASTS}"
    ),
    "ix_broadcasts_market_id_requested_at": (
        "CREATE INDEX ix_broadcasts_market_id_requested_at ON public.broadcasts"
        " USING btree (market_id, requested_at)"
    ),
    "ix_broadcasts_broadcaster_id_requested_at": (
        "CREATE INDEX ix_broadcasts_broadcaster_id_requested_at ON public.broadcasts"
        " USING btree (broadcaster_id, requested_at)"
    ),
    "ix_chat_messages_broadcast_id_id": (
        "CREATE INDEX ix_chat_messages_broadcast_id_id ON public.chat_messages"
        " USING btree (broadcast_id, id)"
    ),
}

FOREIGN_KEYS_OF_THE_ROUND: Final[dict[str, str]] = {
    "fk_streaks_user_id_users": "users",
    "fk_questions_market_id_markets": "markets",
    "fk_questions_asked_by_users": "users",
    "fk_questions_answered_by_users": "users",
    "fk_questions_removed_by_users": "users",
    "fk_broadcasts_market_id_markets": "markets",
    "fk_broadcasts_broadcaster_id_users": "users",
    "fk_broadcasts_decided_by_users": "users",
    "fk_broadcasts_resumed_from_broadcasts": "broadcasts",
    "fk_broadcasts_ended_by_users": "users",
    "fk_chat_messages_market_id_markets": "markets",
    "fk_chat_messages_broadcast_id_broadcasts": "broadcasts",
    "fk_chat_messages_user_id_users": "users",
}
"""Each foreign key of the four tables and the table it points at."""


async def test_the_columns_of_the_four_tables_and_the_two_added_columns_exist(
    owner_engine: AsyncEngine,
) -> None:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull"
                " FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid"
                " WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'"
                " AND a.attnum > 0 AND NOT a.attisdropped"
            )
        )
        actual: dict[str, dict[str, tuple[str, bool]]] = {}
        for table, column, type_name, not_null in rows:
            actual.setdefault(table, {})[column] = (type_name, not not_null)

    for table, columns in COLUMNS_OF_THE_ROUND.items():
        assert actual[table] == columns, table
    for (table, column), definition in ADDED_COLUMNS.items():
        assert actual[table][column] == definition, (table, column)


async def test_the_keys_checks_and_indexes_of_the_round_exist(owner_engine: AsyncEngine) -> None:
    async def pairs(statement: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for name, value in await connection.execute(text(statement)):
            found[name] = value
        return found

    async with owner_engine.connect() as connection:
        checks = set(
            await connection.scalars(
                text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
                )
            )
        )
        indexes = await pairs(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
        )
        foreign_keys = await pairs(
            "SELECT conname, confrelid::regclass::text FROM pg_constraint"
            " WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
        )
        primary_keys = await pairs(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE contype = 'p' AND connamespace = 'public'::regnamespace"
        )

    assert checks >= CHECKS_OF_THE_ROUND
    for name, definition in INDEXES_OF_THE_ROUND.items():
        assert indexes.get(name) == definition, name
    for name, target in FOREIGN_KEYS_OF_THE_ROUND.items():
        assert foreign_keys.get(name) == target, name
    assert primary_keys["streaks"] == "PRIMARY KEY (user_id)"
    for table in ("questions", "broadcasts", "chat_messages"):
        assert primary_keys[table] == "PRIMARY KEY (id)", table


async def test_the_journal_takes_the_two_kinds_a_streak_books(
    uow_factory: UowFactory, owner_engine: AsyncEngine, world: World
) -> None:
    async with owner_engine.connect() as connection:
        listed = await connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'ck_journal_entries_kind'"
            )
        )
    assert listed is not None
    assert all(f"'{kind.value}'" in listed for kind in JournalKind)
    assert {"STREAK_REWARD", "STREAK_BONUS"} <= {kind.value for kind in JournalKind}

    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        for kind in (JournalKind.STREAK_REWARD, JournalKind.STREAK_BONUS):
            entry = await uow.journal.add_entry(_entry(f"streak:{kind.value}", kind))
            await uow.journal.add_postings(
                [
                    Posting(entry_id=entry.id, account_id=issuance.id, amount_micro=-2 * CR),
                    Posting(
                        entry_id=entry.id, account_id=world.user_account.id, amount_micro=2 * CR
                    ),
                ]
            )

    async with uow_factory() as uow:
        account = await uow.accounts.get(world.user_account.id)
        assert account is not None
        assert account.balance_micro == 4 * CR


async def test_the_trigger_that_fixes_an_audience_is_the_fourth_and_exists(
    owner_engine: AsyncEngine,
) -> None:
    assert TRIGGER_FUNCTIONS[3] == "keep_market_audience"
    assert "CREATE FUNCTION keep_market_audience()" in TRIGGER_STATEMENTS[6]
    assert "CREATE TRIGGER markets_audience_fixed" in TRIGGER_STATEMENTS[7]
    assert len(TRIGGER_STATEMENTS) == 8

    async with owner_engine.connect() as connection:
        definition = await connection.scalar(
            text(
                "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t"
                " WHERE t.tgname = 'markets_audience_fixed' AND NOT t.tgisinternal"
            )
        )
    assert definition == (
        "CREATE TRIGGER markets_audience_fixed BEFORE UPDATE OF audience_programme"
        " ON public.markets FOR EACH ROW EXECUTE FUNCTION keep_market_audience()"
    )


@pytest.mark.parametrize("role", ["owner", "app"])
@pytest.mark.parametrize(
    ("stored", "written"),
    [(None, PROGRAMME), (PROGRAMME, None), (PROGRAMME, OTHER_PROGRAMME)],
    ids=["public-to-a-programme", "a-programme-to-public", "one-programme-to-another"],
)
async def test_no_role_changes_the_audience_of_a_market(
    uow_factory: UowFactory,
    owner_engine: AsyncEngine,
    database: Database,
    world: World,
    role: str,
    stored: str | None,
    written: str | None,
) -> None:
    """The audience of a market is decided when it is proposed. The trigger refuses
    the change from the owner role as from the app role, whatever the status."""
    engine = owner_engine if role == "owner" else database.engine
    async with uow_factory() as uow:
        market = await uow.markets.add(_market(world.user.id, audience_programme=stored))

    async def write(programme: str | None) -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE markets SET audience_programme = :programme WHERE id = :id"),
                {"programme": programme, "id": market.id},
            )

    with pytest.raises(IntegrityError, match="the audience of a market cannot change") as refused:
        await write(written)
    assert "markets_audience_fixed" in str(refused.value.orig.__cause__.constraint_name)  # type: ignore[union-attr]

    # Setting it to what it is changes nothing, and passes.
    await write(stored)

    async with owner_engine.connect() as connection:
        kept = await connection.scalar(
            text("SELECT audience_programme FROM markets WHERE id = :id"), {"id": market.id}
        )
    assert kept == stored


async def test_the_application_cannot_change_an_audience_either_and_other_columns_still_change(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        private = await uow.markets.add(_market(world.user.id, audience_programme=PROGRAMME))

    async def change() -> None:
        async with uow_factory() as uow:
            market = await uow.markets.get_for_update(private.id)
            assert market is not None
            market.audience_programme = None

    with pytest.raises(IntegrityError, match="the audience of a market cannot change"):
        await change()

    async with uow_factory() as uow:
        market = await uow.markets.get_for_update(private.id)
        assert market is not None
        market.status = MarketStatus.HALTED

    async with uow_factory() as uow:
        market = await uow.markets.get(private.id)
        assert market is not None
        assert (market.status, market.audience_programme) == (MarketStatus.HALTED, PROGRAMME)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM questions",
        "TRUNCATE questions",
        "DELETE FROM broadcasts",
        "TRUNCATE broadcasts CASCADE",
        "UPDATE chat_messages SET text = 'rewritten'",
        "UPDATE chat_messages SET from_vis = true",
    ],
)
async def test_the_app_role_erases_no_question_and_no_broadcast_and_edits_no_chat_message(
    uow_factory: UowFactory, database: Database, world: World, statement: str
) -> None:
    broadcast = _broadcast(world, BroadcastStatus.LIVE)
    await _insert(uow_factory, [_question(world), broadcast, _chat_message(world, broadcast)])

    async def run_statement() -> None:
        async with database.engine.begin() as connection:
            await connection.execute(text(statement))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await run_statement()

    async with uow_factory() as uow:
        counts = [
            await uow.session.scalar(select(func.count()).select_from(model))
            for model in (Question, Broadcast, ChatMessage)
        ]
        message = (await uow.session.scalars(select(ChatMessage))).one()
    assert counts == [1, 1, 1]
    assert (message.text, message.from_vis) == ("Hopp!", False)


async def test_the_app_role_writes_what_the_four_tables_are_for(
    uow_factory: UowFactory, database: Database, settings: Settings, world: World
) -> None:
    """It answers and removes a question, moves a broadcast on, posts and deletes a
    chat message, and counts, moves and deletes a streak."""
    broadcast = _broadcast(world, BroadcastStatus.REQUESTED)
    question = _question(world)
    await _insert(uow_factory, [question, broadcast, _streak(world)])

    async with uow_factory() as uow:
        assert await uow.session.scalar(text("SELECT current_user")) == settings.postgres.app_user
        asked = await uow.session.get(Question, question.id, with_for_update={"key_share": True})
        assert asked is not None
        asked.answer_text, asked.answered_by, asked.answered_at = "Yes.", world.admin.id, NOW
        asked.removed_by, asked.removed_at = world.admin.id, NOW + DAY
        running = await uow.session.get(
            Broadcast, broadcast.id, with_for_update={"key_share": True}
        )
        assert running is not None
        running.status = BroadcastStatus.LIVE
        running.decided_by, running.decided_at = world.admin.id, NOW
        running.started_at, running.last_chunk_at = NOW, NOW
        streak = await uow.session.get(Streak, world.user.id, with_for_update={"key_share": True})
        assert streak is not None
        streak.current_days, streak.last_day = 4, date(2026, 10, 10)
        message = _chat_message(world, broadcast)
        uow.session.add(message)
        await uow.flush()

    async with uow_factory() as uow:
        stored = await uow.session.get(ChatMessage, message.id)
        assert stored is not None
        await uow.session.delete(stored)
        streak = await uow.session.get(Streak, world.user.id)
        assert streak is not None
        assert (streak.current_days, streak.longest_days) == (4, 5)
        await uow.session.delete(streak)

    async with uow_factory() as uow:
        assert await uow.session.scalar(select(func.count()).select_from(ChatMessage)) == 0
        assert await uow.session.scalar(select(func.count()).select_from(Streak)) == 0
        kept = await uow.session.get(Question, question.id)
        assert kept is not None
        assert (kept.answer_text, kept.removed_by) == ("Yes.", world.admin.id)


REJECTED_ROWS_OF_THE_ROUND: Final = [
    pytest.param(
        lambda w: [_market(w.user.id, audience_programme="")],
        "ck_markets_audience_programme_not_blank",
        id="market-for-a-programme-without-a-name",
    ),
    pytest.param(
        lambda w: [_market(w.user.id, audience_programme="x" * 101)],
        "value too long for type character varying\\(100\\)",
        id="market-for-a-programme-longer-than-a-label",
    ),
    pytest.param(
        lambda w: [_streak(w, current_days=0, longest_days=0)],
        "ck_streaks_current_days_positive",
        id="streak-of-no-day",
    ),
    pytest.param(
        lambda w: [_streak(w, current_days=6, longest_days=5)],
        "ck_streaks_longest_not_below_current",
        id="streak-longer-than-its-longest",
    ),
    pytest.param(
        lambda w: [_streak(w, user_id=uuid7())],
        "fk_streaks_user_id_users",
        id="streak-of-nobody",
    ),
    pytest.param(
        lambda w: [_question(w, text="")],
        "ck_questions_text_length",
        id="question-without-text",
    ),
    pytest.param(
        lambda w: [_question(w, text="x" * (QUESTION_STORED_MAX_LENGTH + 1))],
        "ck_questions_text_length",
        id="question-longer-than-the-column-takes",
    ),
    pytest.param(
        lambda w: [_question(w, answer_text="", answered_by=w.admin.id, answered_at=NOW)],
        "ck_questions_answer_length",
        id="answer-without-text",
    ),
    pytest.param(
        lambda w: [
            _question(
                w,
                answer_text="x" * (ANSWER_STORED_MAX_LENGTH + 1),
                answered_by=w.admin.id,
                answered_at=NOW,
            )
        ],
        "ck_questions_answer_length",
        id="answer-longer-than-the-column-takes",
    ),
    pytest.param(
        lambda w: [_question(w, answer_text="Yes.", answered_at=NOW)],
        "ck_questions_answered_whole",
        id="answer-of-nobody",
    ),
    pytest.param(
        lambda w: [_question(w, answer_text="Yes.", answered_by=w.admin.id)],
        "ck_questions_answered_whole",
        id="answer-without-its-instant",
    ),
    pytest.param(
        lambda w: [_question(w, answered_by=w.admin.id, answered_at=NOW)],
        "ck_questions_answered_whole",
        id="answerer-without-an-answer",
    ),
    pytest.param(
        lambda w: [_question(w, removed_by=w.admin.id)],
        "ck_questions_removed_whole",
        id="removal-without-its-instant",
    ),
    pytest.param(
        lambda w: [_question(w, removed_at=NOW)],
        "ck_questions_removed_whole",
        id="removal-by-nobody",
    ),
    pytest.param(
        lambda w: [_question(w, market_id=uuid7())],
        "fk_questions_market_id_markets",
        id="question-about-no-market",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.REQUESTED, decided_by=w.admin.id)],
        "ck_broadcasts_decided_together",
        id="broadcast-decided-by-somebody-at-no-instant",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.REQUESTED, decided_by=w.admin.id, decided_at=NOW)],
        "ck_broadcasts_requested_is_undecided",
        id="requested-broadcast-that-is-decided",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.APPROVED, decided_by=None, decided_at=None)],
        "ck_broadcasts_requested_is_undecided",
        id="approved-broadcast-nobody-decided",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.REFUSED, refusal_reason=None)],
        "ck_broadcasts_refusal_has_reason",
        id="refusal-without-a-reason",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.APPROVED, refusal_reason="Not this week.")],
        "ck_broadcasts_refusal_has_reason",
        id="reason-without-a-refusal",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.LIVE, started_at=None)],
        "ck_broadcasts_live_has_started",
        id="live-broadcast-that-never-started",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.LIVE, last_chunk_at=None)],
        "ck_broadcasts_live_has_started",
        id="live-broadcast-without-a-latest-piece",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.ENDED, started_at=None)],
        "ck_broadcasts_live_has_started",
        id="ended-broadcast-that-never-started",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.ENDED, ended_at=None)],
        "ck_broadcasts_ended_is_complete",
        id="ended-broadcast-without-its-instant",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.ENDED, end_reason=None)],
        "ck_broadcasts_ended_is_complete",
        id="ended-broadcast-without-a-reason",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.LIVE, ended_at=NOW)],
        "ck_broadcasts_ended_is_complete",
        id="live-broadcast-with-an-end",
    ),
    pytest.param(
        lambda w: _resumed(w, BroadcastStatus.APPROVED),
        "ck_broadcasts_resumed_has_started",
        id="approved-broadcast-that-resumes-another",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.LIVE, resumed_from=uuid7())],
        "fk_broadcasts_resumed_from_broadcasts",
        id="broadcast-that-resumes-none",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.REQUESTED, market_id=uuid7())],
        "fk_broadcasts_market_id_markets",
        id="broadcast-of-no-market",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.REQUESTED), _broadcast(w, BroadcastStatus.LIVE)],
        "uq_broadcasts_market_open",
        id="second-open-broadcast-requested-then-live",
    ),
    pytest.param(
        lambda w: [
            _broadcast(w, BroadcastStatus.APPROVED),
            _broadcast(w, BroadcastStatus.REQUESTED),
        ],
        "uq_broadcasts_market_open",
        id="second-open-broadcast-approved-then-requested",
    ),
    pytest.param(
        lambda w: [_broadcast(w, BroadcastStatus.LIVE), _broadcast(w, BroadcastStatus.LIVE)],
        "uq_broadcasts_market_open",
        id="second-open-broadcast-two-live",
    ),
]


@pytest.mark.parametrize(("rows", "constraint"), REJECTED_ROWS_OF_THE_ROUND)
async def test_database_rejects_a_row_of_the_round(
    uow_factory: UowFactory,
    world: World,
    rows: Callable[[World], Sequence[Base]],
    constraint: str,
) -> None:
    with pytest.raises(DBAPIError, match=constraint):
        await _insert(uow_factory, rows(world))


@pytest.mark.parametrize(
    ("text_of_message", "accepted"),
    [
        ("", False),
        ("x", True),
        ("x" * CHAT_MESSAGE_STORED_MAX_LENGTH, True),
        ("x" * (CHAT_MESSAGE_STORED_MAX_LENGTH + 1), False),
    ],
    ids=["empty", "one-character", "the-longest", "one-too-long"],
)
async def test_a_chat_message_holds_one_to_five_hundred_characters(
    uow_factory: UowFactory, world: World, text_of_message: str, accepted: bool
) -> None:
    broadcast = _broadcast(world, BroadcastStatus.LIVE)
    rows = [broadcast, _chat_message(world, broadcast, text=text_of_message)]

    if accepted:
        await _insert(uow_factory, rows)
    else:
        with pytest.raises(IntegrityError, match="ck_chat_messages_text_length"):
            await _insert(uow_factory, rows)


async def test_a_chat_message_needs_its_broadcast_and_a_broadcast_with_messages_stays(
    uow_factory: UowFactory, owner_engine: AsyncEngine, world: World
) -> None:
    broadcast = _broadcast(world, BroadcastStatus.LIVE)
    with pytest.raises(IntegrityError, match="fk_chat_messages_broadcast_id_broadcasts"):
        await _insert(uow_factory, [_chat_message(world, broadcast)])

    await _insert(uow_factory, [broadcast, _chat_message(world, broadcast)])

    async def erase_as_owner() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(text("DELETE FROM broadcasts"))

    with pytest.raises(IntegrityError, match="fk_chat_messages_broadcast_id_broadcasts"):
        await erase_as_owner()


@pytest.mark.parametrize("closed", [BroadcastStatus.REFUSED, BroadcastStatus.ENDED])
async def test_a_market_has_one_open_broadcast_and_any_number_that_are_over(
    uow_factory: UowFactory, world: World, closed: BroadcastStatus
) -> None:
    ended = _broadcast(world, BroadcastStatus.ENDED)
    await _insert(
        uow_factory,
        [
            _broadcast(world, closed),
            _broadcast(world, closed),
            ended,
            # It runs on the approval of the ended one, and is the one open broadcast.
            _broadcast(world, BroadcastStatus.LIVE, resumed_from=ended.id),
        ],
    )

    with pytest.raises(IntegrityError, match="uq_broadcasts_market_open"):
        await _insert(uow_factory, [_broadcast(world, BroadcastStatus.REQUESTED)])

    # Another market has an open broadcast of its own.
    async with uow_factory() as uow:
        other = await uow.markets.add(_market(world.user.id))
    await _insert(uow_factory, [_broadcast(world, BroadcastStatus.REQUESTED, market_id=other.id)])


@pytest.mark.parametrize(
    ("column", "value", "of_status"),
    [("status", "PAUSED", "APPROVED"), ("end_reason", "BORED", "ENDED")],
)
async def test_a_broadcast_keeps_only_the_statuses_and_the_end_reasons_the_enums_name(
    uow_factory: UowFactory,
    owner_engine: AsyncEngine,
    world: World,
    column: str,
    value: str,
    of_status: str,
) -> None:
    """Each value is written to a row on which it breaks no other rule: an approved
    broadcast is decided and neither refused nor started, an ended one has a reason."""
    await _insert(
        uow_factory,
        [_broadcast(world, BroadcastStatus.APPROVED), _broadcast(world, BroadcastStatus.ENDED)],
    )

    async def write() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(f"UPDATE broadcasts SET {column} = :value WHERE status = :of_status"),
                {"value": value, "of_status": of_status},
            )

    with pytest.raises(IntegrityError, match=f"ck_broadcasts_{column}"):
        await write()

    async with owner_engine.connect() as connection:
        listed = {
            name: str(
                await connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"
                    ),
                    {"name": name},
                )
            )
            for name in ("ck_broadcasts_status", "ck_broadcasts_end_reason")
        }
    assert all(f"'{status.value}'" in listed["ck_broadcasts_status"] for status in BroadcastStatus)
    assert all(
        f"'{reason.value}'" in listed["ck_broadcasts_end_reason"] for reason in BroadcastEndReason
    )


async def test_the_two_reads_of_positions_say_where_a_user_holds_a_row(
    uow_factory: UowFactory, world: World
) -> None:
    """A holder is found by a position row, whatever its shares: the first buy of an
    outcome inserts it and nothing deletes it."""
    async with uow_factory() as uow:
        second = await uow.markets.add(_market(world.user.id))
        third = await uow.markets.add(_market(world.user.id))
        await uow.positions.add(
            Position(user_id=world.user.id, market_id=world.market.id, outcome=Outcome.YES)
        )
        await uow.positions.add(
            Position(user_id=world.user.id, market_id=world.market.id, outcome=Outcome.NO)
        )
        await uow.positions.add(
            Position(
                user_id=world.user.id, market_id=second.id, outcome=Outcome.NO, shares_micro=CR
            )
        )
        await uow.positions.add(
            Position(user_id=world.admin.id, market_id=third.id, outcome=Outcome.YES)
        )

    async with uow_factory() as uow:
        assert await uow.positions.exists_for_user_market(world.user.id, world.market.id)
        assert await uow.positions.exists_for_user_market(world.user.id, second.id)
        assert not await uow.positions.exists_for_user_market(world.user.id, third.id)
        assert not await uow.positions.exists_for_user_market(world.admin.id, world.market.id)
        assert not await uow.positions.exists_for_user_market(world.user.id, uuid7())

        everything = [world.market.id, second.id, third.id]
        assert await uow.positions.market_ids_of_user(world.user.id, among=everything) == {
            world.market.id,
            second.id,
        }
        assert await uow.positions.market_ids_of_user(world.user.id, among=[third.id]) == set()
        assert await uow.positions.market_ids_of_user(world.admin.id, among=everything) == {
            third.id
        }
        assert await uow.positions.market_ids_of_user(world.user.id, among=[]) == set()
