"""The price chart of a market (SPEC §9, §10, Appendix A.4).

Points are read from ScyllaDB, where the relay copies them. Postgres remains the
record: the `trades` table holds the same points, and it is what the chart is built
from when ScyllaDB is down or has not caught up yet.

A chart is bounded, however much its market traded. Neither store is asked for more
than `HISTORY_READ_LIMIT` points, the newest ones, and an answer holds at most
`HISTORY_MAX_POINTS` of what was read. A trade costs next to nothing to make, so
without the two bounds the work of a request and the size of its answer would grow
with whatever one person cares to trade.
"""

import logging
from collections.abc import Collection, Sequence
from datetime import datetime
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
        self._read_limit = settings.scylla.history_read_limit
        self._max_points = settings.scylla.history_max_points
        self._clock = clock
        self._view = view
        self._store = store

    async def history(self, market_id: UUID) -> HistoryResponse:
        """The price chart of a market, oldest point first (`GET /markets/{id}/history`).

        1. Read the market from Postgres, in a transaction that ends before ScyllaDB is
           asked: no Postgres connection is held across a ScyllaDB read. Unknown,
           PROPOSED or REJECTED: `NotFoundError` (404). Remember its `version` and
           `opened_at`. Every other market was approved and has an `opened_at`; one
           without is a broken row and raises `RuntimeError` (500).
        2. The right edge of the chart: `clock.now()` while the market can still
           trade, otherwise the moment its trading ended
           (`MarketView.trading_ended_at`).
        3. ScyllaDB: `QuoteHistoryStore.read(market_id, since=opened_at, until=<the
           edge>, limit=HISTORY_READ_LIMIT)`, outside any transaction. It returns the
           newest points, at most that many. No trade happens after the edge, so the
           range never exceeds the time the market traded (the store sends a query
           per day). `ScyllaUnavailableError` means there are no ScyllaDB points; it is
           not an error of the request.
        4. How far ScyllaDB got: the largest k for which it returned a point of every
           version from its first one to k. Its first one is version 0, and k is -1
           when that is missing. A read that filled the limit left older points in
           the store, so there the first one is the lowest version it returned.
           Looking at the newest point alone would overlook a version that is missing
           below it. When k is below the market's `version`, catch up from Postgres,
           in a second short transaction: `trades.list_price_points(market_id,
           after_version=max(k, 0), limit=HISTORY_READ_LIMIT)`, each as
           `t=created_at`, `p_yes_micro=p_yes_after_micro`, `version=market_version`.
           They are the newest trades after k, because the limit must cut the oldest
           points of a very busy market, never the latest price. A point of Postgres
           replaces a ScyllaDB point of the same version: Postgres is the record.
        5. The opening. Unless a point of version 0 was read, it is `t=opened_at`,
           `p_yes_micro=EVEN_ODDS_MICRO`, from the market's row: every chart starts
           where its market opened, also one whose oldest trades were not read.
        6. Order by `version` and keep one point per version. More than
           `HISTORY_MAX_POINTS` of them are thinned evenly (`thinned`): the first and
           the latest stay, and versions go on rising from point to point. `p_yes` is
           `MarketView.probabilities(p_yes_micro)[0]`.
        7. `source` is `scylla` when ScyllaDB returned at least one point, else
           `postgres`.
        8. `until` is the edge of step 2. No point lies after it: should the latest
           one be dated later, the edge is that point's instant. That is a trade
           which committed while its chart was being read, or one that a clock set
           back in between dated after the close of its market.

        With ScyllaDB stopped the chart comes from Postgres alone; once it is back and
        the relay has drained the outbox, the same chart comes from ScyllaDB.

        Each store is asked for `HISTORY_READ_LIMIT` rows at most, so a request reads
        at most twice that many, and its answer holds at most `HISTORY_MAX_POINTS`
        points.
        """
        async with self._uow_factory() as uow:
            market = await uow.markets.get(market_id)
        if market is None or market.status in HIDDEN_STATUSES:
            raise NotFoundError
        opened_at = market.opened_at
        if opened_at is None:
            raise RuntimeError(f"market {market.id} is {market.status} but has no opened_at")

        now = self._clock.now()
        ended_at = self._view.trading_ended_at(market, now)
        edge = now if ended_at is None else ended_at
        try:
            stored = await self._store.read(
                market_id, since=opened_at, until=edge, limit=self._read_limit
            )
        except ScyllaUnavailableError as exc:
            logger.debug("ScyllaDB has no chart for market %s: %s", market_id, exc)
            stored = []
        # Ascending by time, so of two points with one version the later one stays.
        points: dict[int, tuple[datetime, int]] = {
            point.version: (point.ts, point.p_yes_micro) for point in stored
        }

        # A read that filled the limit left older points in the store: what ScyllaDB
        # contributes begins at the lowest version it returned, not at the opening.
        first = min(points) if len(stored) >= self._read_limit else 0
        reached = _gap_free_up_to(points, first)
        if reached < market.version:
            async with self._uow_factory() as uow:
                recorded = await uow.trades.list_price_points(
                    market_id, after_version=max(reached, 0), limit=self._read_limit
                )
            for version, at, p_yes_micro in recorded:
                points[version] = (at, p_yes_micro)
        points.setdefault(0, (opened_at, EVEN_ODDS_MICRO))

        chart = thinned(sorted(points.items()), self._max_points)
        _, (latest_at, _) = chart[-1]
        return HistoryResponse(
            points=[
                HistoryPoint(t=at, p_yes=self._view.probabilities(p_yes_micro)[0], version=version)
                for version, (at, p_yes_micro) in chart
            ],
            source=HistorySource.SCYLLA if stored else HistorySource.POSTGRES,
            until=max(edge, latest_at),
        )


def thinned[PointT](points: Sequence[PointT], most: int) -> list[PointT]:
    """At most `most` of `points`, in their order: the first, the last, and between
    them the ones that lie evenly spread over the positions of the series.

    A series of `most` points or fewer is returned whole. `most` is at least 2
    (`HISTORY_MAX_POINTS`), so the first and the last always fit. Of a longer series
    no point is taken twice: each position taken lies after the one before it, and
    the steps between them differ by one position at most.
    """
    if len(points) <= most:
        return list(points)
    last = len(points) - 1
    return [points[place * last // (most - 1)] for place in range(most)]


def _gap_free_up_to(versions: Collection[int], first: int) -> int:
    """The largest k for which every version from `first` to k is among `versions`,
    or `first - 1` when `first` is not."""
    reached = first - 1
    while reached + 1 in versions:
        reached += 1
    return reached
