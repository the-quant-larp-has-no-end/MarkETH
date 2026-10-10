"""What a unit of work does for the code that tells open tabs what happened, and for a
step that may fail without failing its transaction (`app/repositories/unit_of_work.py`).

An event exists exactly if its transaction committed: `uow.announce` hands events over
after the commit, in order, and never after a rollback or a failed commit. A hand-over
that fails is logged and goes no further. `uow.savepoint` and `uow.set_lock_timeout`
bound a step inside a transaction.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid7

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import Settings
from app.core.enums import DegreeLevel, JournalKind, UserRole, UserStatus
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.db.database import Database
from app.models import JournalEntry, Posting, User
from app.repositories.unit_of_work import UnitOfWork

pytestmark = pytest.mark.integration

NOW: Final[datetime] = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Body:
    type: str

    def model_dump_json(self) -> str:
        return f'{{"type":"{self.type}"}}'


def event(name: str) -> LiveEvent:
    return LiveEvent((Topic(TopicKind.MARKETS),), Audience.everyone(), Body(name))


def user(name: str) -> User:
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


class Heard:
    """Stands where the announcer stands: keeps every hand-over, and reads the database
    at that moment to see what a listener would find."""

    def __init__(self, database: Database) -> None:
        self._database = database
        self.hand_overs: list[list[str]] = []
        self.usernames_found: list[list[str]] = []
        self.connections_in_use: list[int] = []

    async def __call__(self, events: Sequence[LiveEvent]) -> None:
        self.hand_overs.append([event.payload.type for event in events])
        self.connections_in_use.append(self._database.engine.pool.checkedout())  # type: ignore[attr-defined]
        async with UnitOfWork(self._database.session_factory) as uow:
            found = await uow.session.scalars(select(User.username).order_by(User.username))
            self.usernames_found.append(list(found))


async def test_events_are_handed_over_once_after_the_commit_in_the_order_announced(
    database: Database,
) -> None:
    heard = Heard(database)

    async with UnitOfWork(database.session_factory, heard) as uow:
        uow.announce(event("first"))
        await uow.users.add(user("alice"))
        uow.announce(event("second"))
        uow.announce(event("third"))
        assert heard.hand_overs == []

    assert heard.hand_overs == [["first", "second", "third"]]
    # The commit has returned: a listener that reads because of the event finds the row,
    # and the connection of the transaction is back in the pool.
    assert heard.usernames_found == [["alice"]]
    assert heard.connections_in_use == [0]


async def test_a_transaction_that_announces_nothing_hands_nothing_over(database: Database) -> None:
    heard = Heard(database)

    async with UnitOfWork(database.session_factory, heard) as uow:
        await uow.users.add(user("alice"))

    assert heard.hand_overs == []


async def test_a_transaction_that_rolls_back_announces_nothing(database: Database) -> None:
    heard = Heard(database)

    async def give_up() -> None:
        async with UnitOfWork(database.session_factory, heard) as uow:
            await uow.users.add(user("alice"))
            uow.announce(event("never"))
            raise RuntimeError("the service gave up")

    with pytest.raises(RuntimeError, match="gave up"):
        await give_up()

    assert heard.hand_overs == []
    async with UnitOfWork(database.session_factory) as uow:
        assert (await uow.users.list_all()) == []


async def test_a_commit_that_fails_announces_nothing(database: Database) -> None:
    """The deferred check of the ledger runs at COMMIT: an entry whose postings do not
    sum to zero fails there, after the block has ended without an error."""
    heard = Heard(database)

    async def book_an_unbalanced_entry() -> None:
        async with UnitOfWork(database.session_factory, heard) as uow:
            owner = await uow.users.add(user("alice"))
            account = await uow.accounts.create_user_account(owner.id)
            entry = await uow.journal.add_entry(
                JournalEntry(
                    id=uuid7(),
                    kind=JournalKind.SIGNUP_GRANT,
                    ref_type="test",
                    ref_id=uuid7(),
                    idempotency_key="unbalanced",
                    created_at=NOW,
                )
            )
            await uow.journal.add_postings(
                [Posting(entry_id=entry.id, account_id=account.id, amount_micro=5)]
            )
            uow.announce(event("never"))

    with pytest.raises(IntegrityError, match="is unbalanced"):
        await book_an_unbalanced_entry()

    assert heard.hand_overs == []
    async with UnitOfWork(database.session_factory) as uow:
        assert (await uow.users.list_all()) == []


async def test_a_hand_over_that_fails_is_logged_and_the_transaction_stands(
    database: Database, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(events: Sequence[LiveEvent]) -> None:
        raise RuntimeError("nobody is listening")

    with caplog.at_level(logging.ERROR, logger="app.repositories.unit_of_work"):
        async with UnitOfWork(database.session_factory, broken) as uow:
            await uow.users.add(user("alice"))
            uow.announce(event("lost"))
            uow.announce(event("lost-too"))

    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.getMessage() == (
        "2 live event(s) of a committed transaction were not handed over"
    )
    assert record.exc_info is not None
    assert "nobody is listening" in str(record.exc_info[1])
    async with UnitOfWork(database.session_factory) as uow:
        assert [row.username for row in await uow.users.list_all()] == ["alice"]


async def test_without_an_announcer_events_go_nowhere_and_nothing_fails(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    async with uow_factory() as uow:
        await uow.users.add(user("alice"))
        uow.announce(event("unheard"))

    async with uow_factory() as uow:
        assert [row.username for row in await uow.users.list_all()] == ["alice"]


async def test_each_unit_of_work_hands_over_its_own_events(database: Database) -> None:
    heard = Heard(database)

    async with UnitOfWork(database.session_factory, heard) as first:
        first.announce(event("of-the-first"))
        async with UnitOfWork(database.session_factory, heard) as second:
            second.announce(event("of-the-second"))

    assert heard.hand_overs == [["of-the-second"], ["of-the-first"]]


# --- A step inside a transaction -------------------------------------------------------


async def _lock_timeout_ms(uow: UnitOfWork) -> int:
    """What a statement of this transaction waits for a lock, in milliseconds."""
    setting = await uow.session.scalar(
        text("SELECT setting FROM pg_settings WHERE name = 'lock_timeout'")
    )
    assert isinstance(setting, str)
    return int(setting)


async def test_a_savepoint_that_fails_rolls_back_its_own_writes_and_the_transaction_goes_on(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    async def add_bob_and_a_second_alice(uow: UnitOfWork) -> None:
        async with uow.savepoint():
            await uow.users.add(user("bob"))
            # A second alice breaks the unique username: the statement fails.
            twin = user("alice")
            twin.email = "twin@ethz.ch"
            await uow.users.add(twin)

    async with uow_factory() as uow:
        await uow.users.add(user("alice"))
        with pytest.raises(IntegrityError, match="uq_users_username"):
            await add_bob_and_a_second_alice(uow)
        async with uow.savepoint():
            await uow.users.add(user("carol"))

    async with uow_factory() as uow:
        assert [row.username for row in await uow.users.list_all()] == ["alice", "carol"]


async def test_an_event_announced_in_a_savepoint_that_is_rolled_back_is_handed_over_all_the_same(
    database: Database,
) -> None:
    """Announcements belong to the transaction, not to a savepoint of it. A step that
    may fail therefore announces after its block, or only that something is to be read
    again, which is true whether the step stood or not."""
    heard = Heard(database)

    async def a_step_that_fails(uow: UnitOfWork) -> None:
        async with uow.savepoint():
            await uow.users.add(user("bob"))
            uow.announce(event("of-the-step"))
            raise RuntimeError("the step failed")

    async with UnitOfWork(database.session_factory, heard) as uow:
        await uow.users.add(user("alice"))
        uow.announce(event("of-the-transaction"))
        with pytest.raises(RuntimeError, match="the step failed"):
            await a_step_that_fails(uow)

    assert heard.hand_overs == [["of-the-transaction", "of-the-step"]]
    assert heard.usernames_found == [["alice"]]


async def test_a_lock_timeout_holds_from_where_it_is_set_and_ends_with_its_transaction(
    uow_factory: Callable[[], UnitOfWork], settings: Settings
) -> None:
    of_the_pool = settings.postgres.lock_timeout_ms
    assert of_the_pool not in (250, 1500)

    async with uow_factory() as uow:
        assert await _lock_timeout_ms(uow) == of_the_pool
        await uow.set_lock_timeout(250)
        assert await _lock_timeout_ms(uow) == 250
        await uow.set_lock_timeout(1500)
        assert await _lock_timeout_ms(uow) == 1500

    async with uow_factory() as uow:
        assert await _lock_timeout_ms(uow) == of_the_pool


async def test_a_lock_timeout_set_in_a_savepoint_ends_with_a_savepoint_that_is_rolled_back(
    uow_factory: Callable[[], UnitOfWork], settings: Settings
) -> None:
    of_the_pool = settings.postgres.lock_timeout_ms

    async def a_step_that_fails(uow: UnitOfWork) -> None:
        async with uow.savepoint():
            await uow.set_lock_timeout(250)
            assert await _lock_timeout_ms(uow) == 250
            raise RuntimeError("the step failed")

    async with uow_factory() as uow:
        with pytest.raises(RuntimeError, match="the step failed"):
            await a_step_that_fails(uow)
        assert await _lock_timeout_ms(uow) == of_the_pool

        # A savepoint that is released keeps it: the caller sets what it wants next.
        async with uow.savepoint():
            await uow.set_lock_timeout(250)
        assert await _lock_timeout_ms(uow) == 250
        await uow.set_lock_timeout(of_the_pool)
        assert await _lock_timeout_ms(uow) == of_the_pool


async def test_a_step_gives_up_on_a_lock_within_its_timeout_and_its_transaction_commits(
    uow_factory: Callable[[], UnitOfWork],
) -> None:
    async with uow_factory() as uow:
        alice = await uow.users.add(user("alice"))
    alice_id: UUID = alice.id

    async def a_step_that_needs_the_lock(uow: UnitOfWork) -> None:
        async with uow.savepoint():
            await uow.set_lock_timeout(50)
            await uow.users.get(alice_id, for_update=True)

    async with uow_factory() as holder:
        assert await holder.users.get(alice_id, for_update=True) is not None

        async with uow_factory() as waiter:
            await waiter.users.add(user("bob"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await a_step_that_needs_the_lock(waiter)

    async with uow_factory() as uow:
        assert [row.username for row in await uow.users.list_all()] == ["alice", "bob"]


@pytest.mark.parametrize("milliseconds", [0, -1])
async def test_a_lock_timeout_that_would_wait_for_ever_is_refused(
    uow_factory: Callable[[], UnitOfWork], milliseconds: int
) -> None:
    async with uow_factory() as uow:
        with pytest.raises(ValueError, match="above zero"):
            await uow.set_lock_timeout(milliseconds)
