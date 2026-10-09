"""`UnitOfWork`: one session, one transaction, every repository (backend/CLAUDE.md).

A service opens exactly one per business operation:

    async with self._uow_factory() as uow:      # BEGIN (READ COMMITTED)
        market = await uow.markets.get_for_update(market_id)
        ...
    # COMMIT on normal exit, ROLLBACK if the block raised

Committing here, and nowhere else, is what makes an operation atomic: a trade's journal
entry, position, market quantities, outbox row and idempotency response exist together
or not at all (SPEC §8).

A database error inside the block ends the transaction on the spot; nothing else can
run on that unit of work, so let it leave the block.

* A violated constraint (`IntegrityError`) that a service expects, such as a second
  resolution of a market, is translated by that service outside the block. One nobody
  expected is a bug and becomes a 500. The deferred ledger check runs at COMMIT, so it
  surfaces from the end of the block.
* A lost or refused connection is nobody's to translate: it leaves the service as it
  is, and the API answers it with 503 for every route (`app/api/errors.py`, SPEC §9).
  The relay and the scripts are not behind the API and catch it themselves.
"""

import asyncio
from types import TracebackType
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.auth import LoginTokenRepository, SessionRepository
from app.repositories.coffee_ticket import CoffeeTicketRepository
from app.repositories.ledger import AccountRepository, JournalRepository
from app.repositories.market import MarketRepository, ResolutionRepository
from app.repositories.ops import AlertRepository, AuditLogRepository, OutboxRepository
from app.repositories.trading import IdempotencyRepository, PositionRepository, TradeRepository
from app.repositories.user import UserRepository
from app.repositories.voucher import (
    VoucherCodeBatchRepository,
    VoucherCodeRepository,
    VoucherGrantRepository,
)


class UnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session = session_factory()
        self.users = UserRepository(self._session)
        self.login_tokens = LoginTokenRepository(self._session)
        self.sessions = SessionRepository(self._session)
        self.markets = MarketRepository(self._session)
        self.resolutions = ResolutionRepository(self._session)
        self.positions = PositionRepository(self._session)
        self.trades = TradeRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.accounts = AccountRepository(self._session)
        self.journal = JournalRepository(self._session)
        self.voucher_grants = VoucherGrantRepository(self._session)
        self.code_batches = VoucherCodeBatchRepository(self._session)
        self.codes = VoucherCodeRepository(self._session)
        self.coffee_tickets = CoffeeTicketRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.audit = AuditLogRepository(self._session)
        self.alerts = AlertRepository(self._session)

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def __aenter__(self) -> Self:
        await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            # Shielded like AsyncSession's own exit: a cancelled request must still hand
            # its connection back to the pool.
            await asyncio.shield(asyncio.create_task(self._session.close()))

    async def flush(self) -> None:
        """Send pending changes to Postgres without committing, e.g. before a raw query."""
        await self._session.flush()

    async def advisory_lock(self, name: str) -> None:
        """Hold a named lock until this transaction ends.

        Serialises check-then-insert rules that no single row lock covers, such as the
        caps checked at market approval (A.5) and at code batch creation (A.6).
        """
        await self._session.execute(select(func.pg_advisory_xact_lock(func.hashtext(name))))
