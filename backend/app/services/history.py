"""The price chart of a market (SPEC §9, §10, Appendix A.4).

Points are read from ScyllaDB, where the relay copies them. Postgres remains the
record: the `trades` table holds the same points, and it is what the chart is built
from when ScyllaDB is down or has not caught up yet.
"""

from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.integrations.scylla.quote_history import QuoteHistoryStore
from app.schemas.live import HistoryResponse
from app.services import UowFactory
from app.services.market_view import MarketView

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

        1. Read the market from Postgres. Unknown, PROPOSED or REJECTED:
           `NotFoundError` (404). Remember its `version`, `opened_at` and `close_at`.
        2. ScyllaDB: `QuoteHistoryStore.read(market_id, since=opened_at,
           until=min(clock.now(), close_at))`, outside any transaction. No trade
           happens after `close_at`, so the range never exceeds a market's lifetime
           (the store sends one query per day). `ScyllaUnavailableError` means there
           are no ScyllaDB points; it is not an error of the request.
        3. Catch up from Postgres whenever ScyllaDB has not delivered everything, that
           is when its newest point is older than the market's `version`, or there are
           no points at all:
           * if the version-0 point is missing, add it: `t=opened_at`,
             `p_yes_micro=EVEN_ODDS_MICRO`;
           * append `trades.list_for_market(market_id, limit=MAX_POSTGRES_POINTS,
             after_version=<newest ScyllaDB version, or 0>)`, each as
             `t=created_at`, `p_yes_micro=p_yes_after_micro`, `version=market_version`.
        4. Order by `version` and keep one point per version. `p_yes` is
           `MarketView.probabilities(p_yes_micro)[0]`.
        5. `source` is `scylla` when ScyllaDB returned at least one point, else
           `postgres`.

        With ScyllaDB stopped the chart is complete from Postgres; once it is back and
        the relay has drained the outbox, the same chart comes from ScyllaDB.
        """
        raise NotImplementedError
