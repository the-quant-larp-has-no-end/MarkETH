"""The price chart of a market (SPEC §9, §10, Appendix A.4).

Points are read from ScyllaDB, where the relay copies them. Postgres remains the
record: the `trades` table holds the same points, and it is what the chart is built
from when ScyllaDB is down or has not caught up yet.
"""

import logging
from collections.abc import Collection
from datetime import datetime
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.integrations.scylla.client import ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore
from app.schemas.live import HistoryPoint, HistoryResponse, HistorySource
from app.services import UowFactory
from app.services.market_view import EVEN_ODDS_MICRO, HIDDEN_STATUSES, MarketView

logger = logging.getLogger(__name__)

MAX_POSTGRES_POINTS: Final[int] = 10_000
"""Most trades one request reads from Postgres, as fallback or catch-up."""


class HistoryService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: MarketView,
        store: QuoteHistoryStore,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings
        self._clock = clock
        self._view = view
        self._store = store

    async def history(self, market_id: UUID) -> HistoryResponse:
        """Every price point of a market, oldest first (`GET /markets/{id}/history`).

        1. Read the market from Postgres, in a transaction that ends before ScyllaDB is
           asked: no Postgres connection is held across a ScyllaDB read. Unknown,
           PROPOSED or REJECTED: `NotFoundError` (404). Remember its `version`,
           `opened_at` and `close_at`. Every other market was approved and has an
           `opened_at`; one without is a broken row and raises `RuntimeError` (500).
        2. ScyllaDB: `QuoteHistoryStore.read(market_id, since=opened_at,
           until=min(clock.now(), close_at))`, outside any transaction. No trade
           happens after `close_at`, so the range never exceeds a market's lifetime
           (the store sends one query per day). `ScyllaUnavailableError` means there
           are no ScyllaDB points; it is not an error of the request.
        3. How far ScyllaDB got: the largest k for which it returned a point of every
           version from 0 to k, or -1 when version 0 is missing. Looking at the newest
           point alone would overlook a version that is missing below it. When k is
           below the market's `version`, catch up from Postgres, in a second short
           transaction:
           * if the version-0 point is missing, add it: `t=opened_at`,
             `p_yes_micro=EVEN_ODDS_MICRO`;
           * add `trades.list_for_market(market_id, limit=MAX_POSTGRES_POINTS,
             after_version=max(k, 0), newest_first=True)`, each as `t=created_at`,
             `p_yes_micro=p_yes_after_micro`, `version=market_version`. Newest first,
             because the limit must cut the oldest points of a very busy market, never
             the latest price. A point of Postgres replaces a ScyllaDB point of the
             same version: Postgres is the record.
        4. Order by `version` and keep one point per version. `p_yes` is
           `MarketView.probabilities(p_yes_micro)[0]`.
        5. `source` is `scylla` when ScyllaDB returned at least one point, else
           `postgres`.

        With ScyllaDB stopped the chart is complete from Postgres; once it is back and
        the relay has drained the outbox, the same chart comes from ScyllaDB.
        """
        async with self._uow_factory() as uow:
            market = await uow.markets.get(market_id)
        if market is None or market.status in HIDDEN_STATUSES:
            raise NotFoundError
        opened_at = market.opened_at
        if opened_at is None:
            raise RuntimeError(f"market {market.id} is {market.status} but has no opened_at")

        try:
            stored = await self._store.read(
                market_id, since=opened_at, until=min(self._clock.now(), market.close_at)
            )
        except ScyllaUnavailableError as exc:
            logger.debug("ScyllaDB has no chart for market %s: %s", market_id, exc)
            stored = []
        # Ascending by time, so of two points with one version the later one stays.
        points: dict[int, tuple[datetime, int]] = {
            point.version: (point.ts, point.p_yes_micro) for point in stored
        }

        reached = _gap_free_up_to(points)
        if reached < market.version:
            points.setdefault(0, (opened_at, EVEN_ODDS_MICRO))
            async with self._uow_factory() as uow:
                trades = await uow.trades.list_for_market(
                    market_id,
                    limit=MAX_POSTGRES_POINTS,
                    after_version=max(reached, 0),
                    newest_first=True,
                )
            for trade in trades:
                points[trade.market_version] = (trade.created_at, trade.p_yes_after_micro)

        return HistoryResponse(
            points=[
                HistoryPoint(t=at, p_yes=self._view.probabilities(p_yes_micro)[0], version=version)
                for version, (at, p_yes_micro) in sorted(points.items())
            ],
            source=HistorySource.SCYLLA if stored else HistorySource.POSTGRES,
        )


def _gap_free_up_to(versions: Collection[int]) -> int:
    """The largest k for which every version from 0 to k is among `versions`, or -1
    when version 0 is not."""
    reached = -1
    while reached + 1 in versions:
        reached += 1
    return reached
