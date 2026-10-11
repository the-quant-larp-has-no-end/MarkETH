"""Queries on `broadcasts`."""

from collections.abc import Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import BroadcastStatus
from app.models.broadcast import Broadcast
from app.models.market import Market
from app.models.user import User
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository

OPEN_STATUSES: Final[frozenset[BroadcastStatus]] = frozenset(
    {BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED, BroadcastStatus.LIVE}
)
"""The statuses of a broadcast that is asked for, approved or running: the condition of
`uq_broadcasts_market_open` and `ix_broadcasts_open`. A market has at most one such
broadcast."""


class BroadcastRepository(BaseRepository[Broadcast]):
    """A broadcast is locked as `BaseRepository` locks every row, FOR NO KEY UPDATE: the
    foreign key of a chat message that is being inserted checks its broadcast with FOR
    KEY SHARE, and so waits for no lock the end of that broadcast holds.

    Every status here is the stored one. A row stored LIVE may be over by the clock;
    `BroadcastView.over` says so, and the service stores that end."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Broadcast)

    async def get(self, broadcast_id: UUID) -> Broadcast | None:
        return await self._get(broadcast_id, for_update=False)

    async def get_for_update(self, broadcast_id: UUID) -> Broadcast | None:
        """Lock the row of a broadcast: what every write of its life does, after the
        advisory lock of the lifecycle."""
        return await self._get(broadcast_id, for_update=True)

    async def latest_for_market(self, market_id: UUID) -> Broadcast | None:
        """The broadcast of a market that was asked for or started last, or None for a
        market that never had one. An open broadcast of a market is always its latest:
        a row is only added while the market has no open one."""
        statement = (
            select(Broadcast)
            .where(Broadcast.market_id == market_id)
            .order_by(Broadcast.requested_at.desc(), Broadcast.id.desc())
            .limit(1)
        )
        return await self._session.scalar(statement)

    async def live_for_market(self, market_id: UUID) -> Broadcast | None:
        """The broadcast of a market that is stored LIVE, or None. The unique index on
        open broadcasts makes it at most one."""
        statement = select(Broadcast).where(
            Broadcast.market_id == market_id, Broadcast.status == BroadcastStatus.LIVE
        )
        return await self._session.scalar(statement)

    async def list_live(self) -> Sequence[Broadcast]:
        """Every broadcast that is stored LIVE, earliest start first."""
        statement = (
            select(Broadcast)
            .where(Broadcast.status == BroadcastStatus.LIVE)
            .order_by(Broadcast.started_at, Broadcast.id)
        )
        return (await self._session.scalars(statement)).all()

    async def list_open_with_market_and_broadcaster(
        self,
    ) -> Sequence[tuple[Broadcast, Market, User]]:
        """Every open broadcast with its market and the user who broadcasts, earliest
        request first: the admin's list, in one query."""
        statement = (
            select(Broadcast, Market, User)
            .join(Market, Market.id == Broadcast.market_id)
            .join(User, User.id == Broadcast.broadcaster_id)
            .where(Broadcast.status.in_(OPEN_STATUSES))
            .order_by(Broadcast.requested_at, Broadcast.id)
        )
        rows = await self._session.execute(statement)
        return [(broadcast, market, user) for broadcast, market, user in rows]

    async def count_asked_by_since(self, broadcaster_id: UUID, since: datetime) -> int:
        """How many broadcasts a user asked for or started at or after `since`: what
        `BROADCAST_REQUESTS_PER_USER_PER_DAY` limits. A broadcast that runs on the
        approval of an earlier one (`resumed_from`) was not asked for and is not
        counted."""
        statement = (
            select(func.count())
            .select_from(Broadcast)
            .where(
                Broadcast.broadcaster_id == broadcaster_id,
                Broadcast.requested_at >= since,
                Broadcast.resumed_from.is_(None),
            )
        )
        return (await self._session.execute(statement)).scalar_one()

    async def heartbeat(self, broadcast_id: UUID, now: datetime, *, on_air: bool) -> bool:
        """A piece of a LIVE broadcast was taken at `now`: move `last_chunk_at` there.
        With `on_air`, the broadcast also has something to play from `now` on, unless
        it had before.

        True when this call set `on_air_at`: of any number of calls that say `on_air`
        for one broadcast, exactly one. A broadcast that is not stored LIVE is not
        touched, so a piece that arrives beside the end of its broadcast revives
        nothing.
        """
        live = (Broadcast.id == broadcast_id, Broadcast.status == BroadcastStatus.LIVE)
        if on_air:
            first = (
                update(Broadcast)
                .where(*live, Broadcast.on_air_at.is_(None))
                .values(last_chunk_at=now, on_air_at=now)
                .returning(Broadcast.id)
                .execution_options(**MATCHED_BY_DATABASE)
            )
            if (await self._session.scalars(first)).first() is not None:
                return True
        await self._session.execute(
            update(Broadcast)
            .where(*live)
            .values(last_chunk_at=now)
            .execution_options(**MATCHED_BY_DATABASE)
        )
        return False
