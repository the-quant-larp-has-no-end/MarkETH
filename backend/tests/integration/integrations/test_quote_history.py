"""The price history in ScyllaDB (SPEC §9, A.10). Slow to start: run with `-m scylla`."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Self
from uuid import UUID, uuid7

import pytest

from app.core.config import ScyllaSettings, Settings
from app.integrations.scylla.client import (
    CONNECT_RETRY_SECONDS,
    ScyllaClient,
    ScyllaUnavailableError,
)
from app.integrations.scylla.quote_history import TABLE, QuoteHistoryStore, QuotePoint

from .conftest import LOOPBACK, SilentServer, SwitchableProxy

pytestmark = [pytest.mark.integration, pytest.mark.scylla]

MIDNIGHT = datetime(2026, 10, 10, tzinfo=UTC)


class LoopWatch:
    """Measures the longest time the event loop went without running another task."""

    TICK_SECONDS = 0.01

    def __init__(self) -> None:
        self.longest_stall = 0.0
        self._watcher: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self._watcher = asyncio.create_task(self._watch())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        assert self._watcher is not None
        self._watcher.cancel()
        await asyncio.gather(self._watcher, return_exceptions=True)

    async def _watch(self) -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(self.TICK_SECONDS)
            late = time.perf_counter() - before - self.TICK_SECONDS
            self.longest_stall = max(self.longest_stall, late)


@pytest.fixture
async def history(
    scylla_client: ScyllaClient, scylla_settings: ScyllaSettings
) -> QuoteHistoryStore:
    """A store on a freshly started node, once the node accepts the schema.

    The log line the container waits for comes shortly before the first connection
    succeeds, so the first attempts may still be refused.
    """
    store = QuoteHistoryStore(scylla_client, scylla_settings)
    async with asyncio.timeout(120):
        while True:
            try:
                await store.ensure_schema()
                return store
            except ScyllaUnavailableError:
                await asyncio.sleep(1)


def point(market_id: UUID, ts: datetime, version: int) -> QuotePoint:
    return QuotePoint(
        market_id=market_id,
        ts=ts,
        version=version,
        p_yes_micro=500_000 + version,
        q_yes_micro=version * 1_000_000,
        q_no_micro=0,
        trade_id=uuid7() if version else None,
    )


async def test_ensure_schema_is_repeatable(
    history: QuoteHistoryStore, scylla_client: ScyllaClient, scylla_settings: ScyllaSettings
) -> None:
    await history.ensure_schema()
    await history.ensure_schema()

    tables = await scylla_client.execute(
        "SELECT table_name FROM system_schema.tables WHERE keyspace_name = %s",
        (scylla_settings.keyspace,),
    )
    assert [row["table_name"] for row in tables] == [TABLE]
    assert scylla_client.is_connected


async def test_writing_the_same_point_twice_yields_one_row(
    history: QuoteHistoryStore, scylla_client: ScyllaClient, scylla_settings: ScyllaSettings
) -> None:
    market_id = uuid7()
    trade = point(market_id, MIDNIGHT + timedelta(hours=9, microseconds=123_456), version=1)

    await history.write(trade)
    await history.write(trade)

    stored = trade.ts.replace(microsecond=123_000)
    assert await history.read(market_id, MIDNIGHT, MIDNIGHT + timedelta(days=1)) == [
        QuotePoint(market_id, stored, 1, trade.p_yes_micro, trade.q_yes_micro, 0, trade.trade_id)
    ]
    rows = await scylla_client.execute(
        f"SELECT version FROM {scylla_settings.keyspace}.{TABLE} WHERE market_id = %s AND day = %s",
        (market_id, MIDNIGHT.date()),
    )
    assert len(rows) == 1


async def test_read_returns_points_in_ascending_order_across_two_days(
    history: QuoteHistoryStore,
) -> None:
    market_id, other_market = uuid7(), uuid7()
    opened = point(market_id, MIDNIGHT - timedelta(hours=2), version=0)
    late = point(market_id, MIDNIGHT - timedelta(milliseconds=1), version=1)
    same_instant_a = point(market_id, MIDNIGHT + timedelta(milliseconds=1), version=2)
    same_instant_b = point(market_id, MIDNIGHT + timedelta(milliseconds=1), version=3)
    last = point(market_id, MIDNIGHT + timedelta(hours=5), version=4)
    for written in (last, same_instant_b, opened, same_instant_a, late):
        await history.write(written)
    await history.write(point(other_market, MIDNIGHT, version=1))

    everything = await history.read(market_id, opened.ts, last.ts)
    second_day = await history.read(market_id, MIDNIGHT, MIDNIGHT + timedelta(days=1))
    inside = await history.read(market_id, late.ts, same_instant_a.ts)

    assert everything == [opened, late, same_instant_a, same_instant_b, last]
    assert everything[0].trade_id is None
    assert second_day == [same_instant_a, same_instant_b, last]
    assert inside == [late, same_instant_a, same_instant_b]
    assert await history.read(market_id, last.ts, opened.ts) == []


async def test_read_returns_a_day_that_is_larger_than_one_result_page(
    history: QuoteHistoryStore,
) -> None:
    market_id = uuid7()
    points = [point(market_id, MIDNIGHT + timedelta(seconds=n), version=n) for n in range(5_500)]
    for batch in batched(points, 250, strict=False):
        await asyncio.gather(*(history.write(written) for written in batch))

    assert await history.read(market_id, MIDNIGHT, MIDNIGHT + timedelta(days=1)) == points


async def test_naive_timestamps_are_rejected_before_anything_is_sent(
    history: QuoteHistoryStore,
) -> None:
    naive = datetime(2026, 10, 10, 9, 0)  # noqa: DTZ001

    with pytest.raises(ValueError, match="timezone-aware"):
        await history.write(point(uuid7(), naive, version=1))


async def test_unreachable_scylla_raises_without_blocking_the_event_loop(
    base_settings: Settings, silent_server: SilentServer
) -> None:
    settings = base_settings.scylla.model_copy(
        update={"hosts": [LOOPBACK], "port": silent_server.port, "request_timeout_ms": 400}
    )
    timeout = settings.request_timeout_seconds
    client = ScyllaClient(settings)
    async with LoopWatch() as watch:
        try:
            started = time.perf_counter()
            with pytest.raises(ScyllaUnavailableError):
                await client.execute("SELECT now() FROM system.local")
            waited = time.perf_counter() - started

            assert timeout * 0.8 <= waited < timeout + 0.3, "the caller waits one request timeout"
            assert silent_server.connections >= 1
            assert not client.is_connected
        finally:
            # Closing waits for the attempt still running in its worker thread.
            await client.close()
    assert watch.longest_stall < timeout, f"the loop stood still for {watch.longest_stall:.3f} s"


async def test_node_that_dies_and_returns_never_stalls_the_event_loop(
    history: QuoteHistoryStore, scylla_settings: ScyllaSettings, scylla_proxy: SwitchableProxy
) -> None:
    settings = scylla_settings.model_copy(update={"hosts": [LOOPBACK], "port": scylla_proxy.port})
    timeout = settings.request_timeout_seconds
    client = ScyllaClient(settings)
    store = QuoteHistoryStore(client, settings)
    market_id = uuid7()
    before = point(market_id, MIDNIGHT, version=1)
    after = point(market_id, MIDNIGHT + timedelta(seconds=1), version=2)
    try:
        await store.write(before)

        async with LoopWatch() as watch:
            scylla_proxy.cut()
            # Let the driver notice that its connections are gone before the next statement.
            await asyncio.sleep(0.2)
            started = time.perf_counter()
            with pytest.raises(ScyllaUnavailableError):
                await store.write(after)
            assert time.perf_counter() - started < timeout + 0.3

            scylla_proxy.restore()
            async with asyncio.timeout(CONNECT_RETRY_SECONDS * 6):
                while True:
                    try:
                        await store.write(after)
                        break
                    except ScyllaUnavailableError:
                        await asyncio.sleep(0.25)

        assert watch.longest_stall < timeout, (
            f"the loop stood still for {watch.longest_stall:.3f} s"
        )
        assert client.is_connected
        assert await store.read(market_id, before.ts, after.ts) == [before, after]
    finally:
        await client.close()


async def test_failed_connection_attempt_is_not_repeated_for_a_few_seconds(
    base_settings: Settings,
) -> None:
    connections = 0

    async def turn_away(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal connections
        connections += 1
        writer.close()

    async with await asyncio.start_server(turn_away, LOOPBACK, 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = base_settings.scylla.model_copy(update={"hosts": [LOOPBACK], "port": port})
        client = ScyllaClient(settings)
        try:
            with pytest.raises(ScyllaUnavailableError):
                await client.execute("SELECT now() FROM system.local")
            after_first_attempt = connections
            assert after_first_attempt >= 1

            started = time.perf_counter()
            for _ in range(20):
                with pytest.raises(ScyllaUnavailableError):
                    await client.execute("SELECT now() FROM system.local")

            assert time.perf_counter() - started < 0.1 < CONNECT_RETRY_SECONDS
            assert connections == after_first_attempt, "a new attempt started inside the pause"
        finally:
            await client.close()
