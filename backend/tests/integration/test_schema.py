"""The data layer against a real Postgres (SPEC §5, §7, §8, A.7, A.10).

What the database itself must guarantee, whatever the services above it do: the schema
matches the models, journal entries balance, the app role cannot rewrite the ledger, and
duplicates the spec forbids are rejected. Then the queries of each repository, and the
test harness the other integration tests stand on.
"""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import pytest
from sqlalchemy import CheckConstraint, func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    SYSTEM_ACCOUNT_KINDS,
    AccountKind,
    AlertSeverity,
    CoffeeTicketStatus,
    JournalKind,
    MarketStatus,
    MarketTier,
    OutboxTopic,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
    VoucherCodeStatus,
)
from app.core.security import sha256_hex
from app.db.base import Base
from app.db.database import Database
from app.models import (
    Account,
    Alert,
    AuditLogEntry,
    CoffeeTicket,
    IdempotencyKey,
    JournalEntry,
    LoginToken,
    Market,
    Position,
    Posting,
    Resolution,
    Trade,
    User,
    UserSession,
    VoucherCode,
    VoucherCodeBatch,
    VoucherGrant,
)
from app.repositories.unit_of_work import UnitOfWork
from tests.support.database import (
    check_database_matches_models,
    downgrade_database,
    reset_database,
    upgrade_database,
)

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
    batch: VoucherCodeBatch


def _user(name: str, role: UserRole = UserRole.USER) -> User:
    return User(
        canonical_email=f"{name}@ethz.ch",
        email=f"{name}@student.ethz.ch",
        role=role,
        status=UserStatus.ACTIVE,
        created_at=NOW,
    )


def _market(created_by: UUID, **overrides: Any) -> Market:
    values: dict[str, Any] = {
        "question": "Will the Polymensa serve Älplermagronen on Friday?",
        "yes_def": "It is on the menu.",
        "no_def": "It is not on the menu.",
        "resolution_criteria": "The published menu of that day.",
        "resolution_source": "ethz.ch/menu",
        "status": MarketStatus.OPEN,
        "requested_tier": MarketTier.DEFAULT,
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


def _ticket(world: World, code: str, **overrides: Any) -> CoffeeTicket:
    values: dict[str, Any] = {
        "user_id": world.user.id,
        "code_hash": sha256_hex(code),
        "status": CoffeeTicketStatus.ACTIVE,
        "value_micro": CR,
        "created_at": NOW,
        "expires_at": NOW + 30 * DAY,
    }
    return CoffeeTicket(**(values | overrides))


def _code(world: World, code: str, **overrides: Any) -> VoucherCode:
    values: dict[str, Any] = {
        "batch_id": world.batch.id,
        "code_hash": sha256_hex(code),
        "status": VoucherCodeStatus.ACTIVE,
    }
    return VoucherCode(**(values | overrides))


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
    """Book an entry the way `LedgerService` will: entry, postings, then the balances."""
    entry = await uow.journal.add_entry(_entry(idempotency_key))
    await uow.journal.add_postings(
        [
            Posting(entry_id=entry.id, account_id=account_id, amount_micro=amount_micro)
            for account_id, amount_micro in legs.items()
        ]
    )
    for account_id, amount_micro in legs.items():
        await uow.accounts.add_to_balance(account_id, amount_micro)
    return entry


async def _grant(uow_factory: UowFactory, world: World, amount_micro: int, key: str) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        await _post(uow, key, {issuance.id: -amount_micro, world.user_account.id: amount_micro})


@pytest.fixture
async def world(uow_factory: UowFactory) -> World:
    async with uow_factory() as uow:
        user = await uow.users.add(_user("alice"))
        admin = await uow.users.add(_user("admin", UserRole.ADMIN))
        market = await uow.markets.add(_market(user.id))
        user_account = await uow.accounts.create_user_account(user.id)
        market_account = await uow.accounts.create_market_account(market.id)
        batch = await uow.code_batches.add(
            VoucherCodeBatch(
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


# --- Schema and migration -----------------------------------------------------------


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
    scratch_name = "marketh_downgrade_check"
    scratch = settings.model_copy(
        update={"postgres": settings.postgres.model_copy(update={"database": scratch_name})}
    )
    app_role = settings.postgres.app_user
    leftovers = text(
        "SELECT"
        " (SELECT count(*) FROM pg_tables"
        "   WHERE schemaname = 'public' AND tablename <> 'alembic_version'),"
        " (SELECT count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace),"
        " (SELECT count(*) FROM pg_default_acl),"
        " (SELECT count(*) FROM information_schema.role_table_grants WHERE grantee = :role),"
        " (SELECT count(*) FROM alembic_version),"
        " (SELECT count(*) FROM pg_roles WHERE rolname = :role)"
    )
    async with owner_engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(text(f"CREATE DATABASE {scratch_name}"))
    scratch_engine = create_async_engine(scratch.postgres.owner_url, poolclass=NullPool)
    try:
        await asyncio.to_thread(upgrade_database, scratch)
        await asyncio.to_thread(downgrade_database, scratch)
        async with scratch_engine.connect() as connection:
            row = (await connection.execute(leftovers, {"role": app_role})).one()
        assert tuple(row) == (0, 0, 0, 0, 0, 1)

        await asyncio.to_thread(upgrade_database, scratch)
        async with scratch_engine.connect() as connection:
            assert await connection.scalar(select(func.count()).select_from(Account)) == 4
    finally:
        async with owner_engine.connect() as connection:
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await autocommit.execute(text(f"DROP DATABASE {scratch_name} WITH (FORCE)"))


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


async def test_negative_user_balance_is_rejected(uow_factory: UowFactory, world: World) -> None:
    with pytest.raises(IntegrityError, match="ck_accounts_user_balance_non_negative"):
        async with uow_factory() as uow:
            await uow.accounts.add_to_balance(world.user_account.id, -1)

    async with uow_factory() as uow:
        assert not await uow.accounts.has_negative_user_balance()


async def test_system_and_market_accounts_may_go_negative(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        assert await uow.accounts.add_to_balance(issuance.id, -CR) == -CR
        assert await uow.accounts.add_to_balance(world.market_account.id, -CR) == -CR


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
            VoucherGrant(user_id=w.user.id, period=SIGNUP_GRANT_PERIOD, created_at=NOW)
            for _ in range(2)
        ],
        "uq_voucher_grants_user_id_period",
        id="duplicate-voucher-grant",
    ),
    pytest.param(
        lambda w: [VoucherGrant(user_id=w.user.id, period="2026-13", created_at=NOW)],
        "ck_voucher_grants_period_format",
        id="grant-period-that-is-no-month",
    ),
    pytest.param(
        lambda w: [_code(w, "SAME-CODE") for _ in range(2)],
        "uq_voucher_codes_code_hash",
        id="duplicate-code-hash",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", status=VoucherCodeStatus.REDEEMED)],
        "ck_voucher_codes_redeemed_iff_redeemer",
        id="redeemed-code-without-redeemer",
    ),
    pytest.param(
        lambda w: [_code(w, "CODE", code_hash="XXXX-XXXX-XXXX")],
        "ck_voucher_codes_code_hash_sha256_hex",
        id="plain-voucher-code",
    ),
    pytest.param(
        lambda w: [_ticket(w, "SAME-CODE") for _ in range(2)],
        "uq_coffee_tickets_code_hash",
        id="duplicate-ticket-hash",
    ),
    pytest.param(
        lambda w: [_ticket(w, "CODE", status=CoffeeTicketStatus.REDEEMED)],
        "ck_coffee_tickets_redeemed_iff_redeemer",
        id="redeemed-ticket-without-barista",
    ),
    pytest.param(
        lambda w: [_user("alice")],
        "uq_users_canonical_email",
        id="duplicate-canonical-email",
    ),
    pytest.param(
        lambda w: [
            LoginToken(
                email="alice@student.ethz.ch",
                token_hash=sha256_hex("token"),
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=15),
            )
            for _ in range(2)
        ],
        "uq_login_tokens_token_hash",
        id="duplicate-token-hash",
    ),
    pytest.param(
        lambda w: [
            UserSession(
                id="the-raw-session-token",
                user_id=w.user.id,
                created_at=NOW,
                expires_at=NOW + 30 * DAY,
                last_seen_at=NOW,
            )
        ],
        "ck_sessions_id_sha256_hex",
        id="session-id-that-is-no-hash",
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
    pytest.param(
        lambda w: [Account(kind=AccountKind.USER, owner_id=w.admin.id, balance_micro=-1)],
        "ck_accounts_user_balance_non_negative",
        id="user-account-opened-in-debt",
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
        for status in MarketStatus
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


@pytest.mark.parametrize("status", [MarketStatus.PROPOSED, MarketStatus.REJECTED])
async def test_market_needs_no_liquidity_before_approval(
    uow_factory: UowFactory, world: World, status: MarketStatus
) -> None:
    proposal = _market(world.user.id, status=status, tier=None, budget_micro=0, b_micro=0)
    await _insert(uow_factory, [proposal])
    assert (proposal.q_yes_micro, proposal.q_no_micro, proposal.version) == (0, 0, 0)


async def test_enum_columns_reject_unknown_values(owner_engine: AsyncEngine, world: World) -> None:
    async def promote() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(text("UPDATE users SET role = 'ROOT'"))

    with pytest.raises(IntegrityError, match="ck_users_role"):
        await promote()


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
        assert [user.canonical_email for user in await uow.users.list_all()] == ["alice@ethz.ch"]


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
    session = UserSession(
        id=sha256_hex("session"),
        user_id=world.user.id,
        created_at=NOW,
        expires_at=NOW + 30 * DAY,
        last_seen_at=NOW,
    )
    async with uow_factory() as uow:
        assert await uow.users.get(world.user.id, for_update=True) is not None
        # The foreign-key check on sessions.user_id must not wait for the lock above.
        await asyncio.wait_for(_insert(uow_factory, [session]), UNBLOCKED_WITHIN)


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


async def test_add_to_balance_updates_the_loaded_account(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE, for_update=True)
        account = await uow.accounts.get_user_account(world.user.id, for_update=True)
        assert account is not None

        assert await uow.accounts.add_to_balance(issuance.id, -3 * CR) == -3 * CR
        assert await uow.accounts.add_to_balance(account.id, 3 * CR) == 3 * CR

        assert (account.balance_micro, issuance.balance_micro) == (3 * CR, -3 * CR)
        assert await uow.accounts.sum_balance_by_kind(AccountKind.USER) == 3 * CR
        assert await uow.accounts.total_balance() == 0


async def test_add_to_balance_adds_to_the_database_value_not_to_a_stale_copy(
    uow_factory: UowFactory, world: World
) -> None:
    async with uow_factory() as uow:
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        assert issuance.balance_micro == 0

        async with uow_factory() as other:
            await other.accounts.add_to_balance(issuance.id, -5 * CR)

        assert await uow.accounts.add_to_balance(issuance.id, -CR) == -6 * CR
        assert issuance.balance_micro == -6 * CR


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


async def test_user_insert_if_absent_keeps_the_first_row(uow_factory: UowFactory) -> None:
    async def sign_in(email: str, role: UserRole) -> User:
        async with uow_factory() as uow:
            return await uow.users.insert_if_absent(
                canonical_email="carla@ethz.ch",
                email=email,
                role=role,
                status=UserStatus.ACTIVE,
                now=NOW,
            )

    first = await sign_in("carla@student.ethz.ch", UserRole.USER)
    again = await sign_in("carla@ethz.ch", UserRole.ADMIN)

    assert again.id == first.id
    assert (again.email, again.role) == ("carla@student.ethz.ch", UserRole.USER)
    async with uow_factory() as uow:
        assert (await uow.users.get(first.id)) is not None
        assert (
            await uow.users.get_by_canonical_email("carla@ethz.ch", for_update=True)
        ) is not None
        assert await uow.users.get_by_canonical_email("nobody@ethz.ch") is None
        assert len(await uow.users.list_all()) == 1


async def test_login_tokens_and_sessions(uow_factory: UowFactory, world: World) -> None:
    session_id = sha256_hex("session")
    async with uow_factory() as uow:
        await uow.login_tokens.add(
            LoginToken(
                email="alice@student.ethz.ch",
                token_hash=sha256_hex("token"),
                created_at=NOW,
                expires_at=NOW + timedelta(minutes=15),
            )
        )
        await uow.sessions.add(
            UserSession(
                id=session_id,
                user_id=world.user.id,
                created_at=NOW,
                expires_at=NOW + 30 * DAY,
                last_seen_at=NOW,
            )
        )

    async with uow_factory() as uow:
        token = await uow.login_tokens.get_by_hash(sha256_hex("token"), for_update=True)
        assert token is not None
        assert token.consumed_at is None
        assert await uow.login_tokens.get_by_hash(sha256_hex("unknown")) is None

        session = await uow.sessions.get(session_id)
        assert session is not None
        await uow.sessions.touch(session_id, NOW + DAY)
        assert session.last_seen_at == NOW + DAY
        await uow.sessions.touch(session_id, NOW)
        assert session.last_seen_at == NOW + DAY

    async with uow_factory() as uow:
        await uow.sessions.delete(session_id)
        await uow.sessions.touch(session_id, NOW + 2 * DAY)
        assert await uow.sessions.get(session_id) is None


async def test_market_queries(uow_factory: UowFactory, world: World) -> None:
    live = (MarketStatus.OPEN, MarketStatus.HALTED)
    async with uow_factory() as uow:
        halted = await uow.markets.add(
            _market(
                world.admin.id,
                status=MarketStatus.HALTED,
                budget_micro=4 * CR,
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
        await uow.accounts.add_to_balance(halted_account.id, 2 * CR)
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
        assert await uow.markets.sum_budget_by_statuses(live) == 4 * CR + 6_931_472
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


async def test_trade_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        trades = [
            await uow.trades.add(
                _trade(world, market_version=version, created_at=NOW + version * DAY)
            )
            for version in (1, 2, 3)
        ]
    ids = [trade.id for trade in trades]

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


async def test_voucher_grant_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        grant = await uow.voucher_grants.insert_if_absent(world.user.id, "2026-11", NOW)
        assert grant is not None
        assert grant.entry_id is None
        assert await uow.voucher_grants.insert_if_absent(world.user.id, "2026-11", NOW) is None
        assert await uow.voucher_grants.insert_if_absent(world.user.id, "SIGNUP", NOW) is not None
        assert await uow.voucher_grants.insert_if_absent(world.admin.id, "2026-11", NOW) is not None

        entry = await uow.journal.add_entry(_entry("grant:alice:2026-11"))
        await uow.voucher_grants.set_entry(grant.id, entry.id)
        assert grant.entry_id == entry.id
        assert await uow.voucher_grants.list_periods(world.user.id) == ["2026-11", "SIGNUP"]


async def test_voucher_code_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        await uow.codes.add_all([_code(world, code) for code in ("A", "B", "C")])
        expired = await uow.code_batches.add(
            VoucherCodeBatch(
                label="Last semester's quiz",
                value_micro=5 * CR,
                count=1,
                expires_at=NOW - DAY,
                one_per_user=False,
                created_by=world.admin.id,
                created_at=NOW - 40 * DAY,
            )
        )
        await uow.codes.add(
            VoucherCode(
                batch_id=expired.id, code_hash=sha256_hex("OLD"), status=VoucherCodeStatus.ACTIVE
            )
        )

    async with uow_factory() as uow:
        code = await uow.codes.get_by_hash(sha256_hex("A"), for_update=True)
        assert code is not None
        assert not await uow.codes.user_has_redeemed_in_batch(world.user.id, world.batch.id)
        code.status = VoucherCodeStatus.REDEEMED
        code.redeemed_by = world.user.id
        code.redeemed_at = NOW
        await uow.flush()
        assert await uow.codes.user_has_redeemed_in_batch(world.user.id, world.batch.id)
        assert not await uow.codes.user_has_redeemed_in_batch(world.admin.id, world.batch.id)
        assert await uow.codes.get_by_hash(sha256_hex("unknown")) is None

        # 3 codes of 2 CR this month; the 5 CR code is older and expired.
        assert await uow.codes.issued_value_since(NOW - DAY) == 6 * CR
        assert await uow.codes.issued_value_since(NOW - 60 * DAY) == 11 * CR
        assert await uow.codes.unredeemed_value(NOW) == 4 * CR

        assert await uow.codes.revoke(world.batch.id, [sha256_hex("A"), sha256_hex("B")]) == 1
        assert await uow.codes.issued_value_since(NOW - DAY) == 4 * CR
        assert await uow.codes.revoke(world.batch.id, None) == 1
        assert await uow.codes.revoke(world.batch.id, None) == 0
        assert await uow.codes.unredeemed_value(NOW) == 0
        assert code.status is VoucherCodeStatus.REDEEMED

        batch = await uow.code_batches.get(world.batch.id, for_update=True)
        assert batch is not None
        counts = await uow.code_batches.list_with_counts()
        assert [(c.batch.id, c.active, c.redeemed, c.revoked) for c in counts] == [
            (world.batch.id, 0, 1, 2),
            (expired.id, 1, 0, 0),
        ]


async def test_coffee_ticket_queries(uow_factory: UowFactory, world: World) -> None:
    async with uow_factory() as uow:
        fresh = await uow.coffee_tickets.add(_ticket(world, "FRESH"))
        overdue = await uow.coffee_tickets.add(
            _ticket(world, "OVERDUE", created_at=NOW - 31 * DAY, expires_at=NOW - DAY)
        )
        await uow.coffee_tickets.add(
            _ticket(
                world,
                "USED",
                status=CoffeeTicketStatus.REDEEMED,
                redeemed_at=NOW,
                redeemed_by=world.admin.id,
            )
        )

    async with uow_factory() as uow:
        ticket = await uow.coffee_tickets.get_by_hash(sha256_hex("FRESH"), for_update=True)
        assert ticket is not None
        assert ticket.id == fresh.id
        assert await uow.coffee_tickets.get_by_hash(sha256_hex("unknown")) is None
        assert len(await uow.coffee_tickets.list_for_user(world.user.id)) == 3
        assert await uow.coffee_tickets.list_for_user(world.admin.id) == []
        refundable = await uow.coffee_tickets.list_expired_active(
            world.user.id, NOW, for_update=True
        )
        assert [t.id for t in refundable] == [overdue.id]
        assert await uow.coffee_tickets.pending_value() == 2 * CR


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
