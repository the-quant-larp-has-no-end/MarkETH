"""What an adversarial review of the data layer found, and now holds (SPEC §1, §7, A.7, A.8).

The schema was attacked as the app role and as the owner. Each test here states a
guarantee the database gives whatever the code above it does:

* a balance cannot move without a posting, nor a posting without its balance;
* a committed journal entry cannot gain postings;
* hashes are hashes and timestamps are instants.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid7
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.enums import (
    AccountKind,
    DegreeLevel,
    JournalKind,
    OutboxTopic,
    UserRole,
    UserStatus,
)
from app.db.database import Database
from app.models import Account, JournalEntry, Posting, User, Voucher
from app.repositories.unit_of_work import UnitOfWork

pytestmark = pytest.mark.integration

type UowFactory = Callable[[], UnitOfWork]

NOW: Final[datetime] = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
CR: Final[int] = 1_000_000
IDEMPOTENCY_KEY: Final[str] = "0199c1d4-7a55-7f0e-9a53-3f1f0c7f7a10"

LARGE_ENTRY_LEGS: Final[int] = 20_000
LARGE_ENTRY_SECONDS: Final[float] = 10.0
"""Generous: the commit takes a fraction of a second. A check that sums the entry once
for every posting takes 27 seconds for the same commit (`app.models.triggers`)."""

BALANCES_THAT_DIFFER_FROM_THEIR_POSTINGS: Final[str] = (
    "SELECT count(*) FROM accounts a"
    " WHERE a.balance_micro <> (SELECT coalesce(sum(p.amount_micro), 0) FROM postings p"
    "                           WHERE p.account_id = a.id)"
)


@dataclass(frozen=True, slots=True)
class Books:
    """Two users with accounts and one committed entry: the signup grant of 5 CR to Alice."""

    alice: User
    alice_account: Account
    bob_account: Account
    issuance: UUID
    grant: JournalEntry


def _user(name: str) -> User:
    return User(
        email=f"{name}@ethz.ch",
        name=name.capitalize(),
        username=name,
        degree_level=DegreeLevel.BACHELOR,
        degree_programme="BSc Computer Science",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        policy_accepted_at=NOW,
        created_at=NOW,
    )


def _entry(idempotency_key: str) -> JournalEntry:
    return JournalEntry(
        kind=JournalKind.SIGNUP_GRANT,
        ref_type="test",
        ref_id=uuid7(),
        idempotency_key=idempotency_key,
        created_at=NOW,
    )


@pytest.fixture
async def books(uow_factory: UowFactory) -> Books:
    async with uow_factory() as uow:
        alice = await uow.users.add(_user("alice"))
        bob = await uow.users.add(_user("bob"))
        alice_account = await uow.accounts.create_user_account(alice.id)
        bob_account = await uow.accounts.create_user_account(bob.id)
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        grant = await uow.journal.add_entry(_entry("grant:alice:SIGNUP"))
        await uow.journal.add_postings(
            [
                Posting(entry_id=grant.id, account_id=issuance.id, amount_micro=-5 * CR),
                Posting(entry_id=grant.id, account_id=alice_account.id, amount_micro=5 * CR),
            ]
        )
    return Books(alice, alice_account, bob_account, issuance.id, grant)


async def balance(uow_factory: UowFactory, account_id: UUID) -> int:
    async with uow_factory() as uow:
        account = await uow.accounts.get(account_id)
        assert account is not None
        return account.balance_micro


async def drifted_accounts(owner_engine: AsyncEngine) -> int:
    """Accounts whose balance is not the sum of their postings. Always zero."""
    async with owner_engine.connect() as connection:
        drifted: int = (
            await connection.execute(text(BALANCES_THAT_DIFFER_FROM_THEIR_POSTINGS))
        ).scalar_one()
    return drifted


# --- SPEC §1: no credit outside an audited ledger entry ---------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE accounts SET balance_micro = balance_micro + 1000000000 WHERE kind = 'USER'",
        "UPDATE accounts SET balance_micro = 0",
        "UPDATE accounts SET kind = 'VIS_REDEEMED' WHERE kind = 'VIS_ISSUANCE'",
        "UPDATE accounts SET owner_id = NULL",
        "DELETE FROM accounts WHERE kind = 'USER'",
        "INSERT INTO accounts (id, kind, balance_micro)"
        " SELECT gen_random_uuid(), 'VIS_MM_PNL', 1000000000",
    ],
    ids=["mint", "wipe", "re-label", "disown", "delete", "open-with-a-balance"],
)
async def test_app_role_cannot_move_a_balance_without_a_posting(
    database: Database, owner_engine: AsyncEngine, books: Books, statement: str
) -> None:
    async def run_statement() -> None:
        async with database.engine.begin() as connection:
            await connection.execute(text(statement))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await run_statement()

    assert await drifted_accounts(owner_engine) == 0


async def test_a_balance_assigned_in_python_never_reaches_the_database(
    uow_factory: UowFactory, books: Books
) -> None:
    """The mistake a service would make: `account.balance_micro += ...` on a loaded row."""

    async def assign() -> None:
        async with uow_factory() as uow:
            account = await uow.accounts.get_user_account(books.alice.id, for_update=True)
            assert account is not None
            account.balance_micro += 1_000 * CR

    with pytest.raises(ProgrammingError, match="permission denied"):
        await assign()

    assert await balance(uow_factory, books.alice_account.id) == 5 * CR


async def test_a_posting_always_moves_its_balance(
    uow_factory: UowFactory, owner_engine: AsyncEngine, books: Books
) -> None:
    """Nothing but the two INSERTs: the balances follow, so they cannot be forgotten."""
    async with uow_factory() as uow:
        entry = await uow.journal.add_entry(_entry("transfer:alice-to-bob"))
        await uow.journal.add_postings(
            [
                Posting(entry_id=entry.id, account_id=books.alice_account.id, amount_micro=-CR),
                Posting(entry_id=entry.id, account_id=books.bob_account.id, amount_micro=CR),
            ]
        )

    assert await balance(uow_factory, books.alice_account.id) == 4 * CR
    assert await balance(uow_factory, books.bob_account.id) == CR
    assert await balance(uow_factory, books.issuance) == -5 * CR
    assert await drifted_accounts(owner_engine) == 0


async def test_a_rolled_back_posting_leaves_the_balance_alone(
    uow_factory: UowFactory, owner_engine: AsyncEngine, books: Books
) -> None:
    async def book_half_an_entry() -> None:
        async with uow_factory() as uow:
            entry = await uow.journal.add_entry(_entry("transfer:one-leg"))
            await uow.journal.add_postings(
                [Posting(entry_id=entry.id, account_id=books.bob_account.id, amount_micro=CR)]
            )
            # The balance moved with the INSERT; the commit will refuse the entry.
            assert (await uow.accounts.reload_many([books.bob_account.id]))[0].balance_micro == CR

    with pytest.raises(IntegrityError, match="is unbalanced"):
        await book_half_an_entry()

    assert await balance(uow_factory, books.bob_account.id) == 0
    assert await drifted_accounts(owner_engine) == 0


@pytest.mark.parametrize(
    ("tamper", "alice_micro"),
    [
        ("UPDATE postings SET amount_micro = amount_micro * 2", 10 * CR),
        ("DELETE FROM postings", 0),
        (
            "UPDATE postings SET account_id = (SELECT id FROM accounts WHERE kind = 'VIS_MM_PNL')"
            " WHERE amount_micro > 0",
            0,
        ),
    ],
    ids=["double-every-leg", "delete-the-entry's-legs", "move-a-leg-to-another-account"],
)
async def test_balances_follow_the_postings_whoever_changes_them(
    uow_factory: UowFactory,
    owner_engine: AsyncEngine,
    books: Books,
    tamper: str,
    alice_micro: int,
) -> None:
    """The owner role may rewrite postings (the app role may not). The books stay
    consistent even then: an entry still nets to zero and balances still match."""
    async with owner_engine.begin() as connection:
        await connection.execute(text(tamper))

    assert await balance(uow_factory, books.alice_account.id) == alice_micro
    assert await drifted_accounts(owner_engine) == 0
    async with uow_factory() as uow:
        assert await uow.accounts.total_balance() == 0


# --- SPEC §7: the ledger is append-only --------------------------------------------------


async def test_committed_entry_cannot_gain_postings(
    uow_factory: UowFactory, owner_engine: AsyncEngine, books: Books
) -> None:
    """Two balanced INSERTs would otherwise turn Alice's signup grant into a payment to
    Bob, under the grant's kind, timestamp and idempotency key."""

    async def move_the_grant_to_bob() -> None:
        async with uow_factory() as uow:
            await uow.journal.add_postings(
                [
                    Posting(
                        entry_id=books.grant.id,
                        account_id=books.alice_account.id,
                        amount_micro=-5 * CR,
                    ),
                    Posting(
                        entry_id=books.grant.id,
                        account_id=books.bob_account.id,
                        amount_micro=5 * CR,
                    ),
                ]
            )

    with pytest.raises(IntegrityError, match="is sealed"):
        await move_the_grant_to_bob()

    assert await balance(uow_factory, books.alice_account.id) == 5 * CR
    assert await balance(uow_factory, books.bob_account.id) == 0
    async with uow_factory() as uow:
        legs = await uow.session.scalar(
            select(func.count()).select_from(Posting).where(Posting.entry_id == books.grant.id)
        )
    assert legs == 2
    assert await drifted_accounts(owner_engine) == 0


async def test_the_owner_role_cannot_add_to_a_committed_entry_either(
    owner_engine: AsyncEngine, books: Books
) -> None:
    async def add_two_legs() -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO postings (entry_id, account_id, amount_micro)"
                    " VALUES (:entry, :alice, -1), (:entry, :bob, 1)"
                ),
                {
                    "entry": books.grant.id,
                    "alice": books.alice_account.id,
                    "bob": books.bob_account.id,
                },
            )

    with pytest.raises(IntegrityError, match="is sealed"):
        await add_two_legs()


async def test_app_role_cannot_choose_the_transaction_that_seals_an_entry(
    database: Database,
) -> None:
    """An entry stamped with a transaction still to come would be open to that one."""

    async def forge() -> None:
        async with database.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO journal_entries"
                    " (id, kind, ref_type, ref_id, idempotency_key, created_at, created_xid)"
                    " VALUES (gen_random_uuid(), 'SIGNUP_GRANT', 'test', gen_random_uuid(),"
                    "         'forged', now(), (pg_current_xact_id())::text::bigint + 1)"
                )
            )

    with pytest.raises(ProgrammingError, match="permission denied"):
        await forge()


async def test_an_entry_records_the_transaction_that_created_it(
    uow_factory: UowFactory, books: Books
) -> None:
    async with uow_factory() as uow:
        current = await uow.session.scalar(text("SELECT (pg_current_xact_id())::text::bigint"))
        entry = await uow.journal.add_entry(_entry("empty"))
        assert entry.created_xid == current

    assert books.grant.created_xid < entry.created_xid


async def test_a_large_entry_commits_in_one_pass(
    database: Database, owner_engine: AsyncEngine, books: Books
) -> None:
    """A.5 settles a market as one entry with one posting per holder. Its commit must
    not re-sum the entry for every posting."""
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, email, name, username, degree_level,"
                "                   degree_programme, role, status, policy_accepted_at,"
                "                   created_at)"
                " SELECT gen_random_uuid(), 'holder' || n || '@ethz.ch', 'Holder ' || n,"
                "        'holder' || n, 'BACHELOR', 'BSc Computer Science', 'USER', 'ACTIVE',"
                "        :now, :now"
                " FROM generate_series(1, :holders) AS n"
            ),
            {"holders": LARGE_ENTRY_LEGS, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO accounts (id, kind, owner_id)"
                " SELECT gen_random_uuid(), 'USER', id FROM users"
                " WHERE email LIKE 'holder%'"
            )
        )

    entry_id = uuid7()
    async with database.engine.connect() as connection:
        await connection.execute(
            text(
                "INSERT INTO journal_entries (id, kind, ref_type, ref_id, idempotency_key,"
                " created_at) VALUES (:id, 'MARKET_RESOLUTION', 'market', :id, 'big', :now)"
            ),
            {"id": entry_id, "now": NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO postings (entry_id, account_id, amount_micro)"
                " SELECT :entry, a.id, 1 FROM accounts a JOIN users u ON u.id = a.owner_id"
                " WHERE u.email LIKE 'holder%'"
            ),
            {"entry": entry_id},
        )
        await connection.execute(
            text(
                "INSERT INTO postings (entry_id, account_id, amount_micro)"
                " VALUES (:entry, :issuance, :total)"
            ),
            {"entry": entry_id, "issuance": books.issuance, "total": -LARGE_ENTRY_LEGS},
        )
        started = time.perf_counter()
        await connection.commit()
        elapsed = time.perf_counter() - started

    assert elapsed < LARGE_ENTRY_SECONDS
    async with owner_engine.connect() as connection:
        paid = await connection.scalar(
            text("SELECT count(*) FROM accounts WHERE kind = 'USER' AND balance_micro = 1")
        )
    assert paid == LARGE_ENTRY_LEGS
    assert await drifted_accounts(owner_engine) == 0


async def test_a_large_unbalanced_entry_is_still_refused(
    database: Database, uow_factory: UowFactory, books: Books
) -> None:
    """The last posting speaks for the whole entry, wherever the missing amount is."""

    async def book(legs: int) -> None:
        async with database.engine.begin() as connection:
            entry_id = uuid7()
            await connection.execute(
                text(
                    "INSERT INTO journal_entries (id, kind, ref_type, ref_id, idempotency_key,"
                    " created_at) VALUES (:id, 'MARKET_RESOLUTION', 'market', :id, :key, :now)"
                ),
                {"id": entry_id, "key": f"odd:{legs}", "now": NOW},
            )
            for leg in range(legs):
                await connection.execute(
                    text(
                        "INSERT INTO postings (entry_id, account_id, amount_micro)"
                        " VALUES (:entry, :account, :amount)"
                    ),
                    {
                        "entry": entry_id,
                        "account": books.alice_account.id if leg % 2 else books.issuance,
                        "amount": 1 if leg % 2 else -1,
                    },
                )

    await book(4)
    for odd in (1, 3, 5):
        with pytest.raises(IntegrityError, match="is unbalanced"):
            await book(odd)

    assert await balance(uow_factory, books.alice_account.id) == 5 * CR + 2


# --- A.7: columns ------------------------------------------------------------------------


async def test_request_hash_must_be_a_sha256_hex_digest(
    uow_factory: UowFactory, books: Books
) -> None:
    async def claim_with_plain_text() -> None:
        async with uow_factory() as uow:
            await uow.idempotency.claim(books.alice.id, IDEMPOTENCY_KEY, "not-a-hash", NOW)

    with pytest.raises(IntegrityError, match="request_hash_sha256_hex"):
        await claim_with_plain_text()


async def test_naive_datetime_is_refused(uow_factory: UowFactory) -> None:
    """Read as local time, the same code would store another instant on another machine."""
    naive = NOW.replace(tzinfo=None)

    async def queue_event() -> None:
        async with uow_factory() as uow:
            await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, {"version": 0}, naive)

    with pytest.raises(StatementError, match="needs a timezone"):
        await queue_event()

    async with uow_factory() as uow:
        assert await uow.outbox.claim_unpublished(10) == []


async def test_naive_datetime_is_refused_in_a_comparison_too(
    uow_factory: UowFactory, books: Books
) -> None:
    naive = NOW.replace(tzinfo=None)

    async with uow_factory() as uow:
        with pytest.raises(StatementError, match="needs a timezone"):
            await uow.vouchers.list_expired_active(books.alice.id, naive)
        with pytest.raises(StatementError, match="needs a timezone"):
            await uow.session.execute(select(Voucher).where(Voucher.expires_at <= naive))


async def test_an_instant_in_any_timezone_is_stored_as_that_instant(
    uow_factory: UowFactory,
) -> None:
    in_zurich = NOW.astimezone(ZoneInfo("Europe/Zurich"))
    async with uow_factory() as uow:
        event = await uow.outbox.add(OutboxTopic.QUOTE_UPDATED, {"version": 0}, in_zurich)

    async with uow_factory() as uow:
        (stored,) = await uow.outbox.claim_unpublished(1)
        assert stored.id == event.id
        assert stored.created_at == NOW
        assert stored.created_at.utcoffset() is not None
