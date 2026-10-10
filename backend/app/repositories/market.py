"""Queries on `markets` and `resolutions` (SPEC §5, §6, §8, A.5)."""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AccountKind, MarketStatus
from app.models.ledger import Account
from app.models.market import Market, Resolution
from app.models.trading import Position
from app.repositories.base import BaseRepository, sum_micro


class MarketRepository(BaseRepository[Market]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Market)

    async def get(self, market_id: UUID) -> Market | None:
        return await self._get(market_id, for_update=False)

    async def get_for_update(self, market_id: UUID) -> Market | None:
        """Lock the market row: the first lock of every transaction that touches a market."""
        return await self._get(market_id, for_update=True)

    async def get_many(self, market_ids: Collection[UUID]) -> dict[UUID, Market]:
        """Several markets by id, in one query. An unknown id is missing from the result."""
        statement = select(Market).where(Market.id.in_(market_ids))
        return {market.id: market for market in (await self._session.scalars(statement)).all()}

    async def list_by_statuses(self, statuses: Collection[MarketStatus]) -> Sequence[Market]:
        """Markets whose stored status is one of `statuses`, soonest `close_at` first."""
        statement = (
            select(Market).where(Market.status.in_(statuses)).order_by(Market.close_at, Market.id)
        )
        return (await self._session.scalars(statement)).all()

    async def list_held_outside(self, statuses: Collection[MarketStatus]) -> Sequence[Market]:
        """Markets whose stored status is none of `statuses` and of which somebody
        holds shares: with the settled statuses, the markets a holding has a current
        price in."""
        held = exists().where(Position.market_id == Market.id, Position.shares_micro > 0)
        statement = select(Market).where(Market.status.not_in(statuses), held).order_by(Market.id)
        return (await self._session.scalars(statement)).all()

    async def count_by_statuses(self, statuses: Collection[MarketStatus]) -> int:
        statement = select(func.count()).select_from(Market).where(Market.status.in_(statuses))
        return (await self._session.execute(statement)).scalar_one()

    async def sum_budget_by_statuses(self, statuses: Collection[MarketStatus]) -> int:
        """Summed loss budgets, the figure `MAX_AGGREGATE_LOSS_BUDGET` caps (SPEC §5)."""
        statement = select(sum_micro(Market.budget_micro)).where(Market.status.in_(statuses))
        return (await self._session.execute(statement)).scalar_one()

    async def sum_exposure_by_statuses(self, statuses: Collection[MarketStatus]) -> int:
        """Sum over markets of max(q_yes, q_no) minus the market account's balance.

        This is the market term of the outstanding obligations of SPEC §5: what VIS would
        still have to add if every such market resolved to its more heavily held outcome.
        """
        exposure = func.greatest(Market.q_yes_micro, Market.q_no_micro) - Account.balance_micro
        statement = (
            select(sum_micro(exposure))
            .select_from(Market)
            .join(Account, (Account.market_id == Market.id) & (Account.kind == AccountKind.MARKET))
            .where(Market.status.in_(statuses))
        )
        return (await self._session.execute(statement)).scalar_one()

    async def count_created_by_since(self, user_id: UUID, since: datetime) -> int:
        """Proposals a user made at or after `since`, for the daily proposal limit."""
        statement = (
            select(func.count())
            .select_from(Market)
            .where(Market.created_by == user_id, Market.created_at >= since)
        )
        return (await self._session.execute(statement)).scalar_one()

    async def list_created_by(self, user_id: UUID) -> Sequence[Market]:
        """A user's proposals in every status, newest first."""
        statement = (
            select(Market)
            .where(Market.created_by == user_id)
            .order_by(Market.created_at.desc(), Market.id.desc())
        )
        return (await self._session.scalars(statement)).all()


class ResolutionRepository(BaseRepository[Resolution]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Resolution)

    async def get_by_market(self, market_id: UUID) -> Resolution | None:
        statement = select(Resolution).where(Resolution.market_id == market_id)
        return await self._session.scalar(statement)

    async def get_many_by_market(self, market_ids: Collection[UUID]) -> dict[UUID, Resolution]:
        """The resolutions of several markets, by market id, in one query.

        A market that is not settled has none and is missing from the result.
        """
        statement = select(Resolution).where(Resolution.market_id.in_(market_ids))
        resolutions = (await self._session.scalars(statement)).all()
        return {resolution.market_id: resolution for resolution in resolutions}
