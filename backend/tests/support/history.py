"""Chart points, written and read without the services that normally do it (SPEC §9,
A.4, A.10).

The chart of a market exists three times: as `trades` rows in Postgres, which are the
record; as `quote.updated` rows in the outbox, which the relay still owes; and as rows
in ScyllaDB, which the chart is read from. A test of the history or of the relay needs
all three and should wait for neither the trading nor the lifecycle service:

    store = MemoryHistory()                                  # ScyllaDB, in memory
    await store.ensure_schema()                              # a fresh node has no keyspace
    market = await factory.market()
    await queue_point(container.uow_factory, opening_point(market))    # as approval does
    point = await buy_and_queue(factory, container.uow_factory, alice, market,
                                Outcome.YES, shares_micro=10 * MICRO)  # as a trade does
    relay = OutboxRelay(container.uow_factory, store, settings.scylla, container.clock)
    await relay.drain_once()
    assert store.points(market.id) == [opening_point(market), point]

`recorded_chart` reads back what Postgres holds, which is what every chart has to equal
wherever it was read from.

`MemoryHistory` stands where `QuoteHistoryStore` stands and keeps points the way
ScyllaDB keeps them. It is for the default suite, which runs without a ScyllaDB node;
the tests marked `scylla` use the real store.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select, text

from app.core.enums import OutboxTopic, Outcome
from app.integrations.scylla.client import ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from app.models import Market, Trade, User
from app.models.ops import OutboxEvent
from app.services import UowFactory
from app.services.market_view import EVEN_ODDS_MICRO
from app.services.outbox_events import quote_updated_payload
from tests.support.factories import Factory

NODE_STARTUP_SECONDS: Final[float] = 120.0
"""How long `ready` waits for a ScyllaDB node that has just started."""

type RecordedPoint = tuple[int, datetime, int]
"""One point of a chart as Postgres records it: version, instant, YES price in millionths."""


def to_milliseconds(instant: datetime) -> datetime:
    """`instant` as a `timestamp` column of ScyllaDB gives it back: in UTC and cut off,
    not rounded, at the millisecond."""
    if instant.tzinfo is None:
        raise ValueError("quote timestamps must be timezone-aware")
    in_utc = instant.astimezone(UTC)
    return in_utc.replace(microsecond=in_utc.microsecond // 1000 * 1000)


class MemoryHistory(QuoteHistoryStore):
    """The price history of ScyllaDB, kept in memory.

    What it has in common with the real store, because the relay and the history
    service lean on it:

    * A point is keyed by market, instant and version, so writing one twice leaves one
      row. The instant is kept in UTC and cut to the millisecond; a read gives it back
      that way.
    * `read` returns the newest `limit` points with `since <= ts <= until`, in
      ascending order of instant, then version. A `limit` below 1 is refused with
      `ValueError`.
    * It starts as a node without a keyspace does. Until `ensure_schema` ran, `write`
      and `read` raise `ScyllaUnavailableError`: a missing keyspace and an outage are
      one error to the callers. `wipe` takes the keyspace away again.
    * While `down` is true every method raises `ScyllaUnavailableError`.
    * An instant without a timezone is refused with `ValueError`, and one that has no
      day in UTC with `OverflowError`, before anything else happens.

    What a test can look at and steer:

        calls       the name of every method called, in order, failed calls included
        written     every point a `write` stored, in order, repeats included
        reads       the market, the range and the limit of every `read`, in order,
                    failed ones too
        on_write    awaited by `write` just before it stores a point: raise from it to
                    make that write fail, or wait in it to hold the write
        on_read     awaited by `read` before it answers
    """

    def __init__(self) -> None:
        # The parent's constructor wants a ScyllaClient and the CQL of the real schema.
        # Nothing here speaks CQL, so it is left out on purpose.
        self._rows: dict[tuple[UUID, datetime, int], QuotePoint] = {}
        self._has_schema = False
        self.down = False
        self.calls: list[str] = []
        self.written: list[QuotePoint] = []
        self.reads: list[tuple[UUID, datetime, datetime, int]] = []
        self.on_write: Callable[[QuotePoint], Awaitable[None]] | None = None
        self.on_read: Callable[[], Awaitable[None]] | None = None

    @property
    def has_schema(self) -> bool:
        return self._has_schema

    @property
    def rows(self) -> int:
        """How many rows the table holds, over all markets."""
        return len(self._rows)

    def points(self, market_id: UUID) -> list[QuotePoint]:
        """Every point kept for a market, as a read of all time would return them."""
        return _in_reading_order(point for point in self._rows.values() if _of(point, market_id))

    def wipe(self) -> None:
        """What a wiped ScyllaDB is (A.10): the keyspace is gone and every point with it."""
        self._rows.clear()
        self._has_schema = False

    async def ensure_schema(self) -> None:
        self.calls.append("ensure_schema")
        if self.down:
            raise ScyllaUnavailableError("the stand-in for ScyllaDB is down")
        self._has_schema = True

    async def write(self, point: QuotePoint) -> None:
        self.calls.append("write")
        stored = replace(point, ts=to_milliseconds(point.ts))
        self._answer()
        if self.on_write is not None:
            await self.on_write(point)
        self._rows[(stored.market_id, stored.ts, stored.version)] = stored
        self.written.append(point)

    async def read(
        self, market_id: UUID, since: datetime, until: datetime, *, limit: int
    ) -> list[QuotePoint]:
        self.calls.append("read")
        self.reads.append((market_id, since, until, limit))
        if limit < 1:
            raise ValueError(f"a read asks for at least one point, got {limit}")
        first, last = to_milliseconds(since), to_milliseconds(until)
        if self.on_read is not None:
            await self.on_read()
        self._answer()
        in_range = _in_reading_order(
            point
            for point in self._rows.values()
            if _of(point, market_id) and first <= point.ts <= last
        )
        return in_range[-limit:]

    def _answer(self) -> None:
        """Raise what the real store raises when ScyllaDB did not run a statement."""
        if self.down:
            raise ScyllaUnavailableError("the stand-in for ScyllaDB is down")
        if not self._has_schema:
            raise ScyllaUnavailableError("the stand-in for ScyllaDB has no keyspace")


def _of(point: QuotePoint, market_id: UUID) -> bool:
    return point.market_id == market_id


def _in_reading_order(points: Iterable[QuotePoint]) -> list[QuotePoint]:
    return sorted(points, key=lambda point: (point.ts, point.version))


async def ready(store: QuoteHistoryStore) -> QuoteHistoryStore:
    """`store` with its schema in place, on a ScyllaDB node that may have just started.

    The log line the container fixture waits for comes shortly before the node takes
    its first connection, so the first attempts may still be refused.
    """
    async with asyncio.timeout(NODE_STARTUP_SECONDS):
        while True:
            try:
                await store.ensure_schema()
            except ScyllaUnavailableError:
                await asyncio.sleep(1)
            else:
                return store


def opening_point(market: Market) -> QuotePoint:
    """The point approval queues for a market: version 0 at even odds, at the instant
    it opened, with no trade behind it (A.4)."""
    if market.opened_at is None:
        raise ValueError(f"market {market.id} was never opened, so its chart has no start")
    return QuotePoint(
        market_id=market.id,
        ts=market.opened_at,
        version=0,
        p_yes_micro=EVEN_ODDS_MICRO,
        q_yes_micro=0,
        q_no_micro=0,
        trade_id=None,
    )


async def queue_point(uow_factory: UowFactory, point: QuotePoint) -> int:
    """Queue the `quote.updated` row of `point`, dated like the point, as a trade or an
    approval does in its own transaction. Returns the id of the row."""
    async with uow_factory() as uow:
        event = await uow.outbox.add(
            OutboxTopic.QUOTE_UPDATED, quote_updated_payload(point), point.ts
        )
        return event.id


async def queue_payload(uow_factory: UowFactory, payload_json: str, at: datetime) -> int:
    """Queue a `quote.updated` row whose payload is `payload_json`, whatever JSON that
    is. Returns the id of the row.

    For the rows no writer of the application queues: a payload that lacks a field, or
    one that is a list, a text or `null`. The column is JSONB and stores them all.
    """
    statement = text(
        "INSERT INTO outbox (topic, payload, created_at) "
        "VALUES (:topic, CAST(:payload AS jsonb), :at) RETURNING id"
    )
    async with uow_factory() as uow:
        row_id = await uow.session.scalar(
            statement,
            {"topic": OutboxTopic.QUOTE_UPDATED.value, "payload": payload_json, "at": at},
        )
    assert isinstance(row_id, int)
    return row_id


async def outbox_rows(uow_factory: UowFactory) -> Sequence[OutboxEvent]:
    """Every outbox row, published or not, oldest first."""
    async with uow_factory() as uow:
        return (await uow.session.scalars(select(OutboxEvent).order_by(OutboxEvent.id))).all()


async def unpublished_ids(uow_factory: UowFactory) -> list[int]:
    """The ids of the rows the relay still owes, oldest first."""
    return [row.id for row in await outbox_rows(uow_factory) if row.published_at is None]


async def buy_and_queue(
    factory: Factory,
    uow_factory: UowFactory,
    user: User,
    market: Market,
    outcome: Outcome,
    *,
    shares_micro: int,
) -> QuotePoint:
    """Let `user` buy as `Factory.position` does, and queue the outbox row the trading
    service queues with a trade (A.4). Returns the point of that row.

    The point is read off the committed rows: instant, version and YES price from the
    trade, the quantities from the market.
    """
    await factory.position(user, market, outcome, shares_micro=shares_micro)
    latest = (
        select(Trade)
        .where(Trade.market_id == market.id)
        .order_by(Trade.market_version.desc())
        .limit(1)
    )
    async with uow_factory() as uow:
        trade = (await uow.session.scalars(latest)).one()
    point = QuotePoint(
        market_id=market.id,
        ts=trade.created_at,
        version=trade.market_version,
        p_yes_micro=trade.p_yes_after_micro,
        q_yes_micro=market.q_yes_micro,
        q_no_micro=market.q_no_micro,
        trade_id=trade.id,
    )
    await queue_point(uow_factory, point)
    return point


async def recorded_chart(uow_factory: UowFactory, market: Market) -> list[RecordedPoint]:
    """The chart of `market` as Postgres records it, in version order: the opening at
    even odds, then the YES price after every trade."""
    if market.opened_at is None:
        raise ValueError(f"market {market.id} was never opened, so it has no chart")
    async with uow_factory() as uow:
        trades = await uow.session.scalars(
            select(Trade).where(Trade.market_id == market.id).order_by(Trade.market_version)
        )
        return [
            (0, market.opened_at, EVEN_ODDS_MICRO),
            *(
                (trade.market_version, trade.created_at, trade.p_yes_after_micro)
                for trade in trades
            ),
        ]
