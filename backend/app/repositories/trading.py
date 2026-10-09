"""Queries on `positions`, `trades` and `idempotency_keys` (SPEC §4, §5, §8, A.4)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Outcome
from app.models.trading import IdempotencyKey, Position, Trade
from app.repositories.base import BaseRepository, sum_micro


class PositionRepository(BaseRepository[Position]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Position)

    async def get(
        self, user_id: UUID, market_id: UUID, outcome: Outcome, *, for_update: bool = False
    ) -> Position | None:
        return await self._get((user_id, market_id, outcome), for_update=for_update)

    async def list_for_user_market(
        self, user_id: UUID, market_id: UUID, *, for_update: bool = False
    ) -> Sequence[Position]:
        """A user's positions in one market: at most one per outcome."""
        statement = (
            select(Position)
            .where(Position.user_id == user_id, Position.market_id == market_id)
            .order_by(Position.outcome)
        )
        return (await self._session.scalars(self._locked(statement, for_update))).all()

    async def list_for_market(
        self, market_id: UUID, *, for_update: bool = False
    ) -> Sequence[Position]:
        """Every position in a market, in a fixed order so settlements lock consistently."""
        statement = (
            select(Position)
            .where(Position.market_id == market_id)
            .order_by(Position.user_id, Position.outcome)
        )
        return (await self._session.scalars(self._locked(statement, for_update))).all()

    async def list_for_user(self, user_id: UUID) -> Sequence[Position]:
        statement = (
            select(Position)
            .where(Position.user_id == user_id)
            .order_by(Position.market_id, Position.outcome)
        )
        return (await self._session.scalars(statement)).all()

    async def sum_shares_by_outcome(self, market_id: UUID) -> dict[Outcome, int]:
        """Shares held per outcome; must equal the market's q (SPEC §5 circuit breaker)."""
        statement = (
            select(Position.outcome, sum_micro(Position.shares_micro))
            .where(Position.market_id == market_id)
            .group_by(Position.outcome)
        )
        totals = dict.fromkeys(Outcome, 0)
        for outcome, shares_micro in await self._session.execute(statement):
            totals[outcome] = shares_micro
        return totals


class TradeRepository(BaseRepository[Trade]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Trade)

    async def list_for_user(
        self, user_id: UUID, *, limit: int, before_id: UUID | None = None
    ) -> Sequence[Trade]:
        """A user's trades, newest first. `before_id` is the id of the last trade seen."""
        statement = (
            select(Trade).where(Trade.user_id == user_id).order_by(Trade.id.desc()).limit(limit)
        )
        if before_id is not None:
            statement = statement.where(Trade.id < before_id)
        return (await self._session.scalars(statement)).all()

    async def list_for_market(
        self,
        market_id: UUID,
        *,
        limit: int,
        since: datetime | None = None,
        after_version: int | None = None,
    ) -> Sequence[Trade]:
        """A market's trades in the order they happened, oldest first.

        `since` bounds `created_at` from below (inclusive); `after_version` continues
        after the last `market_version` seen.
        """
        statement = (
            select(Trade)
            .where(Trade.market_id == market_id)
            .order_by(Trade.market_version, Trade.id)
            .limit(limit)
        )
        if since is not None:
            statement = statement.where(Trade.created_at >= since)
        if after_version is not None:
            statement = statement.where(Trade.market_version > after_version)
        return (await self._session.scalars(statement)).all()

    async def exists_for_user_market(self, user_id: UUID, market_id: UUID) -> bool:
        """Whether a user ever traded a market (A.4: such an admin may not resolve it)."""
        statement = select(exists().where(Trade.user_id == user_id, Trade.market_id == market_id))
        return (await self._session.execute(statement)).scalar_one()


class IdempotencyRepository(BaseRepository[IdempotencyKey]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, IdempotencyKey)

    async def claim(
        self, user_id: UUID, key: str, request_hash: str, now: datetime
    ) -> IdempotencyKey | None:
        """Claim (user, key) for this transaction.

        Returns None when the key is new: the caller now owns it and a rollback releases
        it again. Returns the stored row when an earlier request committed under the same
        key. A concurrent request with the same key blocks in the INSERT until the first
        one commits or rolls back, so two can never both own the key.
        """
        claimed = await self._session.execute(
            insert(IdempotencyKey)
            .values(user_id=user_id, key=key, request_hash=request_hash, created_at=now)
            .on_conflict_do_nothing(index_elements=[IdempotencyKey.user_id, IdempotencyKey.key])
            .returning(IdempotencyKey.key)
        )
        if claimed.first() is not None:
            return None
        statement = (
            select(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(statement)).one()

    async def store_response(self, user_id: UUID, key: str, response_json: dict[str, Any]) -> None:
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
            .values(response_json=response_json)
        )
