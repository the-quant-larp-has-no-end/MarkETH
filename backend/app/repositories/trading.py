"""Queries on `positions`, `trades` and `idempotency_keys` (SPEC §4, §5, §8, A.4)."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, select, update
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

    async def exists_for_user_market(self, user_id: UUID, market_id: UUID) -> bool:
        """Whether a user holds a position row in a market, whatever its shares.

        The first buy of an outcome inserts the row and nothing deletes it, so this is
        true for everybody who ever bought there, also after they sold everything.
        """
        statement = select(
            exists().where(Position.user_id == user_id, Position.market_id == market_id)
        )
        return (await self._session.execute(statement)).scalar_one()

    async def market_ids_of_user(self, user_id: UUID, *, among: Collection[UUID]) -> set[UUID]:
        """The markets of `among` a user holds a position row in, in one query. None
        is asked for an empty `among`."""
        if not among:
            return set()
        statement = (
            select(Position.market_id)
            .where(Position.user_id == user_id, Position.market_id.in_(among))
            .distinct()
        )
        return set((await self._session.scalars(statement)).all())

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

    async def net_stake_totals(self, market_ids: Collection[UUID]) -> dict[UUID, int]:
        """Per market, the net stakes of its traders added up, in one query.

        A trader's net stake is max(0, Σ cost - Σ proceeds) over both of their positions
        in the market: what a void refunds them (SPEC §6). Their sum is what a void of
        the market would pay out in all, which the risk page sets against the market's
        account (SPEC §5). A market nobody holds a position in is missing from the
        result.
        """
        net_stake_micro = func.greatest(
            sum_micro(Position.cost_micro) - sum_micro(Position.proceeds_micro), 0
        )
        per_trader = (
            select(Position.market_id, net_stake_micro.label("net_stake_micro"))
            .where(Position.market_id.in_(market_ids))
            .group_by(Position.market_id, Position.user_id)
            .subquery()
        )
        statement = select(
            per_trader.c.market_id, sum_micro(per_trader.c.net_stake_micro)
        ).group_by(per_trader.c.market_id)
        rows = await self._session.execute(statement)
        return dict(rows.all())


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
        after the last `market_version` seen. With more matching trades than `limit`,
        the oldest `limit` are returned.
        """
        statement = (
            select(Trade)
            .where(Trade.market_id == market_id)
            .order_by(Trade.market_version)
            .limit(limit)
        )
        if since is not None:
            statement = statement.where(Trade.created_at >= since)
        if after_version is not None:
            statement = statement.where(Trade.market_version > after_version)
        return (await self._session.scalars(statement)).all()

    async def list_price_points(
        self, market_id: UUID, *, after_version: int, limit: int
    ) -> Sequence[tuple[int, datetime, int]]:
        """The price a market's trades left it at, as a chart reads them from the record
        (SPEC §9): `(market_version, created_at, p_yes_after_micro)` of the trades after
        `after_version`, the newest first.

        With more such trades than `limit`, the newest `limit` are returned: a chart
        may lose its oldest points, never the latest price. Three columns and no
        `Trade` rows, because a chart of a busy market reads `limit` of them with
        every request.
        """
        statement = (
            select(Trade.market_version, Trade.created_at, Trade.p_yes_after_micro)
            .where(Trade.market_id == market_id, Trade.market_version > after_version)
            .order_by(Trade.market_version.desc())
            .limit(limit)
        )
        return (await self._session.execute(statement)).all()

    async def first_trade_at_by_market(self, user_id: UUID) -> dict[UUID, datetime]:
        """Per market a user ever traded, the instant of their first trade there, in
        one query: when they entered the market (`PortfolioEntry.entered_at`)."""
        statement = (
            select(Trade.market_id, func.min(Trade.created_at))
            .where(Trade.user_id == user_id)
            .group_by(Trade.market_id)
        )
        rows = await self._session.execute(statement)
        return dict(rows.all())

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
