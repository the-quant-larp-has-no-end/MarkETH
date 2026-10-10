"""The price history of a market in ScyllaDB (SPEC §9, A.4, A.10).

The relay copies every `quote.updated` outbox row into `quotes_by_market_day`; the
history endpoint reads it back for the chart. Postgres remains the record: the same
points can always be rebuilt from the `trades` table, which is what the endpoint falls
back to when a call here raises `ScyllaUnavailableError`.

A partition is one market on one UTC day, with its newest point first. `ts` has
millisecond precision in ScyllaDB, so a point read back carries its timestamp truncated
to the millisecond.

A read is bounded: it names how many points it wants, gets the newest ones, and is
answered within one request timeout however many days it covers.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID

from app.core.config import ScyllaSettings
from app.integrations.scylla.client import Row, ScyllaClient, ScyllaUnavailableError

TABLE: Final[str] = "quotes_by_market_day"

_SCHEMA_FILE: Final[Path] = Path(__file__).with_name("schema.cql")

_SCHEMA_TIMEOUT_FACTOR: Final[int] = 10
"""A schema change waits for schema agreement, so it gets ten request timeouts."""

_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND: Final[timedelta] = timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class QuotePoint:
    """The state of a market right after one trade. `ts` is timezone-aware."""

    market_id: UUID
    ts: datetime
    version: int
    p_yes_micro: int
    q_yes_micro: int
    q_no_micro: int
    trade_id: UUID | None
    """None for the version-0 point written when a market opens (SPEC A.4)."""


class QuoteHistoryStore:
    """Writes and reads quote points.

    When ScyllaDB is unreachable: every method raises `ScyllaUnavailableError`. The
    relay leaves its outbox rows unpublished and retries them, which is safe even if a
    write that raised did land, because writes are upserts. The history endpoint falls
    back to Postgres.
    """

    def __init__(self, client: ScyllaClient, settings: ScyllaSettings) -> None:
        self._client = client
        table = f"{settings.keyspace}.{TABLE}"
        self._read_timeout_seconds = settings.request_timeout_seconds
        self._schema_timeout_seconds = settings.request_timeout_seconds * _SCHEMA_TIMEOUT_FACTOR
        self._create_keyspace = (
            f"CREATE KEYSPACE IF NOT EXISTS {settings.keyspace} WITH replication = "
            f"{{'class': 'SimpleStrategy', 'replication_factor': {settings.replication_factor}}}"
        )
        # Every statement names its keyspace instead of relying on USE: the client may
        # connect before the keyspace exists, and a wiped ScyllaDB has to heal (A.10).
        self._create_table = _SCHEMA_FILE.read_text(encoding="utf-8").replace(TABLE, table)
        self._insert = (
            f"INSERT INTO {table} (market_id, day, ts, version, "
            "p_yes_micro, q_yes_micro, q_no_micro, trade_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        )
        # Newest first is the order a partition is stored in, so a LIMIT reads no row
        # beyond the ones it returns.
        self._select = (
            "SELECT ts, version, p_yes_micro, q_yes_micro, q_no_micro, trade_id "
            f"FROM {table} WHERE market_id = %s AND day = %s AND ts >= %s AND ts <= %s "
            "ORDER BY ts DESC, version DESC LIMIT %s"
        )

    async def ensure_schema(self) -> None:
        """Create the keyspace and the table if they are missing. Safe to repeat (A.10)."""
        for statement in (self._create_keyspace, self._create_table):
            await self._client.execute(statement, timeout_seconds=self._schema_timeout_seconds)

    async def write(self, point: QuotePoint) -> None:
        """Upsert one point. Its key is (market, day, ts, version), so writing it twice
        leaves one row and the relay may deliver an outbox row more than once."""
        ts_ms = _epoch_ms(point.ts)
        await self._client.execute(
            self._insert,
            (
                point.market_id,
                _day_of(ts_ms),
                ts_ms,
                point.version,
                point.p_yes_micro,
                point.q_yes_micro,
                point.q_no_micro,
                point.trade_id,
            ),
        )

    async def read(
        self, market_id: UUID, since: datetime, until: datetime, *, limit: int
    ) -> list[QuotePoint]:
        """The newest `limit` points with `since <= ts <= until`, ascending by
        (ts, version).

        One query per UTC day of the range, the newest day first, each for no more
        rows than are still missing, and none once `limit` is reached. However much a
        market traded, ScyllaDB is asked for at most `limit` rows, and a market that
        trades a lot costs a query or two. One that trades little is asked day by day
        back to `since`: the caller keeps the range bounded, and a market's lifetime
        (`MAX_MARKET_LIFETIME`) is the natural limit.

        The read as a whole ends within one request timeout
        (`SCYLLA_REQUEST_TIMEOUT_MS`), however many days it covers. After that it
        raises `ScyllaUnavailableError`, like a statement that got no answer.

        A `limit` below 1 is refused with `ValueError` before anything is sent.
        """
        if limit < 1:
            raise ValueError(f"a read asks for at least one point, got {limit}")
        since_ms, until_ms = _epoch_ms(since), _epoch_ms(until)
        first_day, last_day = _day_of(since_ms), _day_of(until_ms)
        newest_first: list[Row] = []
        try:
            async with asyncio.timeout(self._read_timeout_seconds):
                for days_back in range((last_day - first_day).days + 1):
                    missing = limit - len(newest_first)
                    if missing <= 0:
                        break
                    day = last_day - timedelta(days=days_back)
                    newest_first += await self._client.execute(
                        self._select, (market_id, day, since_ms, until_ms, missing)
                    )
        except TimeoutError as exc:
            raise ScyllaUnavailableError(
                f"ScyllaDB did not return a chart within {self._read_timeout_seconds:g} s"
            ) from exc
        return [_to_point(market_id, row) for row in reversed(newest_first)]


def _epoch_ms(instant: datetime) -> int:
    """Milliseconds since the epoch, rounded down: what a `timestamp` column stores."""
    if instant.tzinfo is None:
        raise ValueError("quote timestamps must be timezone-aware")
    return (instant - _EPOCH) // _MILLISECOND


def _day_of(epoch_ms: int) -> date:
    return (_EPOCH + epoch_ms * _MILLISECOND).date()


def _to_point(market_id: UUID, row: Row) -> QuotePoint:
    return QuotePoint(
        market_id=market_id,
        # The driver returns naive datetimes in UTC.
        ts=row["ts"].replace(tzinfo=UTC),
        version=row["version"],
        p_yes_micro=row["p_yes_micro"],
        q_yes_micro=row["q_yes_micro"],
        q_no_micro=row["q_no_micro"],
        trade_id=row["trade_id"],
    )
