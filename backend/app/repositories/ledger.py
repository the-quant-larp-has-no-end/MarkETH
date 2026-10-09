"""Queries on `accounts`, `journal_entries` and `postings` (SPEC §5, §7, A.8).

These are the building blocks of `LedgerService`, the only writer of the ledger. No
method here keeps an entry balanced or a balance in step with its postings; the service
does, and the database checks it again at commit.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SYSTEM_ACCOUNT_KINDS, AccountKind
from app.models.ledger import Account, JournalEntry, Posting
from app.repositories.base import BaseRepository, sum_micro


class AccountRepository(BaseRepository[Account]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Account)

    async def get(self, account_id: UUID, *, for_update: bool = False) -> Account | None:
        return await self._get(account_id, for_update=for_update)

    async def get_user_account(self, user_id: UUID, *, for_update: bool = False) -> Account | None:
        statement = select(Account).where(
            Account.kind == AccountKind.USER, Account.owner_id == user_id
        )
        return await self._session.scalar(self._locked(statement, for_update))

    async def get_market_account(
        self, market_id: UUID, *, for_update: bool = False
    ) -> Account | None:
        statement = select(Account).where(
            Account.kind == AccountKind.MARKET, Account.market_id == market_id
        )
        return await self._session.scalar(self._locked(statement, for_update))

    async def get_system_account(self, kind: AccountKind, *, for_update: bool = False) -> Account:
        """One of the four VIS_* accounts. They always exist: the baseline migration adds them."""
        if kind not in SYSTEM_ACCOUNT_KINDS:
            raise ValueError(f"{kind} is not a system account kind")
        statement = select(Account).where(Account.kind == kind)
        return (await self._session.scalars(self._locked(statement, for_update))).one()

    async def create_user_account(self, user_id: UUID) -> Account:
        return await self.add(Account(kind=AccountKind.USER, owner_id=user_id))

    async def create_market_account(self, market_id: UUID) -> Account:
        return await self.add(Account(kind=AccountKind.MARKET, market_id=market_id))

    async def lock_many(self, account_ids: Collection[UUID]) -> Sequence[Account]:
        """Lock several accounts in ascending id order and return them in that order.

        One fixed order for everyone means two transactions that need the same accounts
        can never wait on each other in a cycle.
        """
        statement = select(Account).where(Account.id.in_(account_ids)).order_by(Account.id)
        return (await self._session.scalars(self._locked(statement, True))).all()

    async def add_to_balance(self, account_id: UUID, delta_micro: int) -> int:
        """Add a signed amount to a balance in the database and return the new balance.

        The addition happens in SQL on the current row, so it is correct whether or not
        the caller holds the row lock, and a copy of the account loaded in this unit of
        work is refreshed from the result. A user balance that would go negative violates
        `ck_accounts_user_balance_non_negative` and raises `IntegrityError`.
        """
        statement = (
            update(Account)
            .where(Account.id == account_id)
            .values(balance_micro=Account.balance_micro + delta_micro)
            .returning(Account)
            # Take the row as RETURNING reports it. The default would redo the addition
            # in Python on the loaded copy, which may predate another transaction's commit.
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        return (await self._session.scalars(statement)).one().balance_micro

    async def total_balance(self) -> int:
        """Sum of all balances. Zero, or credit was created or destroyed (SPEC §1)."""
        statement = select(sum_micro(Account.balance_micro))
        return (await self._session.execute(statement)).scalar_one()

    async def sum_balance_by_kind(self, kind: AccountKind) -> int:
        statement = select(sum_micro(Account.balance_micro)).where(Account.kind == kind)
        return (await self._session.execute(statement)).scalar_one()

    async def has_negative_user_balance(self) -> bool:
        statement = select(
            exists().where(Account.kind == AccountKind.USER, Account.balance_micro < 0)
        )
        return (await self._session.execute(statement)).scalar_one()


class JournalRepository(BaseRepository[JournalEntry]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, JournalEntry)

    async def add_entry(self, entry: JournalEntry) -> JournalEntry:
        return await self.add(entry)

    async def add_postings(self, postings: Sequence[Posting]) -> None:
        """Insert the legs of an entry. Whether they sum to zero is checked at commit."""
        self._session.add_all(postings)
        await self._session.flush()

    async def get_entry_by_idempotency_key(self, idempotency_key: str) -> JournalEntry | None:
        statement = select(JournalEntry).where(JournalEntry.idempotency_key == idempotency_key)
        return await self._session.scalar(statement)

    async def list_postings_for_account(
        self, account_id: UUID, *, limit: int, before_id: int | None = None
    ) -> Sequence[tuple[Posting, JournalEntry]]:
        """An account's postings with their entries, newest first.

        `before_id` is the id of the last posting seen (`postings.id` is the cursor).
        """
        statement = (
            select(Posting, JournalEntry)
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .where(Posting.account_id == account_id)
            .order_by(Posting.id.desc())
            .limit(limit)
        )
        if before_id is not None:
            statement = statement.where(Posting.id < before_id)
        rows = await self._session.execute(statement)
        return [(posting, entry) for posting, entry in rows]
