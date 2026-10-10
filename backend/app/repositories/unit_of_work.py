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
  So does a lock that another transaction held for longer than
  `POSTGRES_LOCK_TIMEOUT_MS`. The relay and the scripts are not behind the API and
  catch both themselves.

What a transaction tells the open tabs goes through here as well. A service calls
`uow.announce(event)` inside the block; the events are handed to the announcer the unit
of work was given once the commit has returned and the connection is back in the pool,
in the order they were announced. So an event exists exactly if its transaction
committed: a block that raised, and a commit that failed, announce nothing. A hand-over
that fails is logged and goes no further, because the transaction it follows stands.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from types import TracebackType
from typing import Self

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction, async_sessionmaker

from app.core.live import LiveEvent
from app.repositories.broadcast import BroadcastRepository
from app.repositories.chat import ChatMessageRepository
from app.repositories.code import CodeBatchRepository, CodeRepository
from app.repositories.discussion import QuestionRepository
from app.repositories.grant import GrantRepository
from app.repositories.leaderboard import LeaderboardRepository
from app.repositories.ledger import AccountRepository, JournalRepository
from app.repositories.market import MarketRepository, ResolutionRepository
from app.repositories.notice import NoticeRepository
from app.repositories.ops import AlertRepository, AuditLogRepository, OutboxRepository
from app.repositories.streak import StreakRepository
from app.repositories.trading import IdempotencyRepository, PositionRepository, TradeRepository
from app.repositories.user import UserRepository
from app.repositories.voucher import VoucherRepository

logger = logging.getLogger(__name__)

Announcer = Callable[[Sequence[LiveEvent]], Awaitable[None]]
"""What a unit of work hands its events to after its commit."""


class UnitOfWork:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        announcer: Announcer | None = None,
    ) -> None:
        self._session = session_factory()
        self._announcer = announcer
        self._announced: list[LiveEvent] = []
        self.users = UserRepository(self._session)
        self.markets = MarketRepository(self._session)
        self.resolutions = ResolutionRepository(self._session)
        self.positions = PositionRepository(self._session)
        self.trades = TradeRepository(self._session)
        self.idempotency = IdempotencyRepository(self._session)
        self.accounts = AccountRepository(self._session)
        self.journal = JournalRepository(self._session)
        self.grants = GrantRepository(self._session)
        self.code_batches = CodeBatchRepository(self._session)
        self.codes = CodeRepository(self._session)
        self.vouchers = VoucherRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.audit = AuditLogRepository(self._session)
        self.alerts = AlertRepository(self._session)
        self.notices = NoticeRepository(self._session)
        self.leaderboard = LeaderboardRepository(self._session)
        self.streaks = StreakRepository(self._session)
        self.questions = QuestionRepository(self._session)
        self.chat_messages = ChatMessageRepository(self._session)
        self.broadcasts = BroadcastRepository(self._session)

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
        committed = False
        try:
            if exc_type is None:
                await self._session.commit()
                committed = True
            else:
                await self._session.rollback()
        finally:
            # Shielded like AsyncSession's own exit: a cancelled request must still hand
            # its connection back to the pool.
            await asyncio.shield(asyncio.create_task(self._session.close()))
        events, self._announced = tuple(self._announced), []
        if committed and events and self._announcer is not None:
            await self._hand_over(self._announcer, events)

    @staticmethod
    async def _hand_over(announcer: Announcer, events: Sequence[LiveEvent]) -> None:
        """Give the events of a committed transaction to the announcer.

        Whatever the announcer raises is logged and goes no further: the transaction
        stands, and the request it belongs to is answered as if nobody were listening.
        """
        try:
            await announcer(events)
        except Exception:
            logger.exception(
                "%d live event(s) of a committed transaction were not handed over", len(events)
            )

    def announce(self, event: LiveEvent) -> None:
        """Tell the open tabs about what this transaction writes.

        The event is delivered after the commit, never on a rollback and never when the
        commit fails. Events are delivered in the order they were announced.
        """
        self._announced.append(event)

    def savepoint(self) -> AsyncSessionTransaction:
        """A savepoint inside this transaction, as a context manager:

            async with uow.savepoint():
                ...

        What the block wrote is rolled back when it raises, and the transaction around
        it goes on. A database error inside the block ends the block alone.

        Announcements are not part of a savepoint: an event announced inside the block
        is handed over with the commit also when the block is rolled back. A step that
        may fail announces what it wrote after its block, or announces only that
        something is to be read again.
        """
        return self._session.begin_nested()

    async def set_lock_timeout(self, milliseconds: int) -> None:
        """How long a statement of this transaction waits for a lock from now on
        (`SET LOCAL lock_timeout`).

        The setting ends with the transaction. Set inside a savepoint that is rolled
        back, it ends with the savepoint; after a savepoint that was released, the
        caller sets the value it wants next. 0 would wait for ever and is refused.
        """
        if milliseconds <= 0:
            raise ValueError("a lock timeout is a number of milliseconds above zero")
        await self._session.execute(
            select(func.set_config("lock_timeout", f"{milliseconds}ms", True))
        )

    async def flush(self) -> None:
        """Send pending changes to Postgres without committing, e.g. before a raw query."""
        await self._session.flush()

    async def advisory_lock(self, name: str) -> None:
        """Hold a named lock until this transaction ends.

        Serialises check-then-insert rules that no single row lock covers, such as the
        caps checked at market approval (A.5) and at code batch creation (A.6), and
        the choice of a username (A.3).
        """
        await self._session.execute(select(func.pg_advisory_xact_lock(func.hashtext(name))))
