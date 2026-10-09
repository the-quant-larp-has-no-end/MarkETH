"""Queries on `coffee_tickets` (SPEC §2, §5, A.6)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CoffeeTicketStatus
from app.models.coffee_ticket import CoffeeTicket
from app.repositories.base import BaseRepository, sum_micro


class CoffeeTicketRepository(BaseRepository[CoffeeTicket]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CoffeeTicket)

    async def get_by_hash(self, code_hash: str, *, for_update: bool = False) -> CoffeeTicket | None:
        """Lock the ticket before redeeming it, so a second scan is rejected."""
        statement = select(CoffeeTicket).where(CoffeeTicket.code_hash == code_hash)
        return await self._session.scalar(self._locked(statement, for_update))

    async def list_for_user(self, user_id: UUID) -> Sequence[CoffeeTicket]:
        """A user's tickets in every status, newest first."""
        statement = (
            select(CoffeeTicket)
            .where(CoffeeTicket.user_id == user_id)
            .order_by(CoffeeTicket.id.desc())
        )
        return (await self._session.scalars(statement)).all()

    async def list_expired_active(
        self, user_id: UUID, now: datetime, *, for_update: bool = False
    ) -> Sequence[CoffeeTicket]:
        """A user's tickets that are still ACTIVE although they expired: refunds owed."""
        statement = (
            select(CoffeeTicket)
            .where(
                CoffeeTicket.user_id == user_id,
                CoffeeTicket.status == CoffeeTicketStatus.ACTIVE,
                CoffeeTicket.expires_at <= now,
            )
            .order_by(CoffeeTicket.id)
        )
        return (await self._session.scalars(self._locked(statement, for_update))).all()

    async def pending_value(self) -> int:
        """Value of all ACTIVE tickets: coffee VIS owes on tickets (SPEC §5)."""
        statement = select(sum_micro(CoffeeTicket.value_micro)).where(
            CoffeeTicket.status == CoffeeTicketStatus.ACTIVE
        )
        return (await self._session.execute(statement)).scalar_one()
