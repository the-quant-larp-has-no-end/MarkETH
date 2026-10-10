"""The outbox relay: `OutboxRelay.drain_once` and `run_forever` (SPEC §9, §12, A.4, A.10).

SPEC §9: "it takes up to 100 unpublished `outbox` rows with FOR UPDATE SKIP LOCKED,
writes them to Scylla, sets `published_at` and commits. If Scylla is down nothing is
marked, and the rows are retried later." Delivery is at least once, and a point written
twice is one row.

ScyllaDB is `MemoryHistory` (tests/support/history.py) here: a store a test can take
down, wipe and hold. The suite's own ScyllaDB is a closed port, which is the outage; the
real node is in the tests marked `scylla`, at the end of the file. The rows are queued
straight into the outbox, as a trade and an approval queue them.

The last part runs the relay the way its container does, `python -m app.relay`, and
stops it with a signal.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Final
from uuid import UUID, uuid7

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import text

from app.container import Container
from app.core.config import ScyllaSettings, Settings
from app.core.units import MICRO
from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from app.relay.outbox_relay import OutboxRelay
from app.repositories.ops import OutboxRepository
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.outbox_events import quote_updated_payload
from scripts.production_env import assignments
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.database import BACKEND_ROOT
from tests.support.history import (
    MemoryHistory,
    outbox_rows,
    queue_payload,
    queue_point,
    ready,
    to_milliseconds,
    unpublished_ids,
)
from tests.support.settings import EXAMPLE_ENV_FILE

pytestmark = pytest.mark.integration

BATCH_SIZE: Final[int] = 3
"""Rows per batch in these tests, so that a handful of rows makes several batches."""

POLL_MS: Final[int] = 10
"""A short idle wait, for the tests that watch the loop go round."""

LONG_POLL_MS: Final[int] = 60_000
"""An idle wait no test sits out: what happens during it happens without waiting."""

PATIENCE_SECONDS: Final[float] = 10.0
"""How long a test waits for something the relay is about to do."""

NODE_SECONDS: Final[float] = 120.0
"""The patience a ScyllaDB node needs that has just started and is asked for a schema."""

RELAY_LOGGER: Final[str] = "app.relay.outbox_relay"

STARTED: Final[str] = "Outbox relay started"
STOPPED: Final[str] = "Outbox relay stopped"

MARKET_ID: Final[UUID] = uuid7()
"""The market of the points below. An outbox row needs no market row behind it."""


def point(version: int, market_id: UUID = MARKET_ID) -> QuotePoint:
    """A point of `market_id`: one per version, a second apart."""
    return QuotePoint(
        market_id=market_id,
        ts=TEST_NOW + timedelta(seconds=version),
        version=version,
        p_yes_micro=500_000 + version,
        q_yes_micro=version * MICRO,
        q_no_micro=0,
        trade_id=uuid7() if version else None,
    )


def relay_on(
    container: Container,
    store: QuoteHistoryStore,
    *,
    batch_size: int = BATCH_SIZE,
    poll_ms: int = POLL_MS,
    uow_factory: UowFactory | None = None,
) -> OutboxRelay:
    """The relay of the application on `store`, with a batch size and an idle wait that
    suit a test. `uow_factory` stands in for the application's when a test has to count
    the relay's transactions or refuse them."""
    tuned = container.settings.scylla.model_copy(
        update={"outbox_batch_size": batch_size, "outbox_poll_interval_ms": poll_ms}
    )
    return OutboxRelay(uow_factory or container.uow_factory, store, tuned, container.clock)


@pytest.fixture
async def store() -> MemoryHistory:
    """A ScyllaDB that answers: its schema is in place and it holds nothing."""
    memory = MemoryHistory()
    await memory.ensure_schema()
    return memory


@pytest.fixture
def relay(container: Container, store: MemoryHistory) -> OutboxRelay:
    return relay_on(container, store)


async def queue(container: Container, *versions: int) -> list[QuotePoint]:
    """Queue one row per version, in the order given, each in a transaction of its own."""
    points = [point(version) for version in versions]
    for queued in points:
        await queue_point(container.uow_factory, queued)
    return points


@asynccontextmanager
async def running(relay: OutboxRelay) -> AsyncIterator[asyncio.Event]:
    """`relay.run_forever` as a task. Yields its stop event. On the way out the event is
    set and the task has to end by itself within the patience of the tests: one that
    raised, or that did not return, fails the test here."""
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run_forever(stop))
    try:
        yield stop
    finally:
        stop.set()
        await asyncio.wait_for(task, PATIENCE_SECONDS)


async def until(
    condition: Callable[[], Awaitable[bool] | bool], *, within_seconds: float = PATIENCE_SECONDS
) -> None:
    """Wait until `condition` holds; fail when it does not within the time given."""
    async with asyncio.timeout(within_seconds):
        while True:
            met = condition()
            if not isinstance(met, bool):
                met = await met
            if met:
                return
            await asyncio.sleep(0.005)


def drained(container: Container) -> Callable[[], Awaitable[bool]]:
    """A condition for `until`: the relay owes nothing."""

    async def nothing_owed() -> bool:
        return not await unpublished_ids(container.uow_factory)

    return nothing_owed


def logged(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    """What the relay logged at `level`."""
    return [
        record
        for record in caplog.records
        if record.name == RELAY_LOGGER and record.levelno == level
    ]


# --- One batch --------------------------------------------------------------------------


async def test_a_batch_is_written_in_id_order_and_marked_published(
    container: Container, clock: FrozenClock, store: MemoryHistory, relay: OutboxRelay
) -> None:
    """In the order the rows were queued, which is not the order of their versions
    here, and stamped with the relay's clock."""
    queued = await queue(container, 2, 0, 1)
    clock.advance(timedelta(minutes=5))

    delivered = await relay.drain_once()

    assert delivered == 3
    assert store.written == queued
    rows = await outbox_rows(container.uow_factory)
    assert [row.id for row in rows] == [1, 2, 3]
    assert [row.published_at for row in rows] == [clock.now()] * 3
    assert [row.created_at for row in rows] == [owed.ts for owed in queued]
    assert store.points(MARKET_ID) == sorted(queued, key=lambda owed: owed.version)


async def test_an_empty_outbox_is_a_batch_of_nothing_and_a_published_row_is_not_sent_again(
    container: Container, store: MemoryHistory, relay: OutboxRelay
) -> None:
    assert await relay.drain_once() == 0
    assert store.written == []

    queued = await queue(container, 0, 1)
    assert await relay.drain_once() == 2
    assert await relay.drain_once() == 0

    assert store.written == queued


async def test_a_batch_holds_the_oldest_rows_up_to_the_batch_size(
    container: Container, store: MemoryHistory, relay: OutboxRelay
) -> None:
    queued = await queue(container, *range(7))

    first = await relay.drain_once()

    assert first == BATCH_SIZE
    assert store.written == queued[:BATCH_SIZE]
    assert await unpublished_ids(container.uow_factory) == [4, 5, 6, 7]
    assert [await relay.drain_once() for _ in range(3)] == [BATCH_SIZE, 1, 0]
    assert store.written == queued


async def test_with_scylla_down_nothing_is_marked_and_the_batch_is_retried(
    container: Container, store: MemoryHistory, relay: OutboxRelay
) -> None:
    queued = await queue(container, 0, 1, 2)
    store.down = True

    with pytest.raises(ScyllaUnavailableError):
        await relay.drain_once()

    assert await unpublished_ids(container.uow_factory) == [1, 2, 3]
    assert store.written == []

    store.down = False
    assert await relay.drain_once() == 3
    assert store.written == queued
    assert await unpublished_ids(container.uow_factory) == []


async def test_a_write_that_fails_in_the_middle_of_a_batch_leaves_the_whole_batch_owed(
    container: Container, store: MemoryHistory, relay: OutboxRelay
) -> None:
    """The first point of the batch is in ScyllaDB and its row is not marked: the
    retry writes it a second time, which leaves one row (SPEC §9: "delivering a row
    twice is harmless")."""
    first, second, third = await queue(container, 0, 1, 2)

    async def refuse_the_second(written: QuotePoint) -> None:
        if written.version == second.version:
            raise ScyllaUnavailableError("ScyllaDB went away between two writes")

    store.on_write = refuse_the_second
    with pytest.raises(ScyllaUnavailableError):
        await relay.drain_once()

    assert store.written == [first]
    assert await unpublished_ids(container.uow_factory) == [1, 2, 3]

    store.on_write = None
    assert await relay.drain_once() == 3
    assert store.written == [first, first, second, third]
    assert store.points(MARKET_ID) == [first, second, third]
    assert await unpublished_ids(container.uow_factory) == []


async def test_a_batch_scylla_took_whose_marks_were_lost_is_delivered_again_harmlessly(
    container: Container,
    store: MemoryHistory,
    relay: OutboxRelay,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At least once: the relay dies after ScyllaDB took the points and before Postgres
    committed their marks. The rows are still owed, are sent again, and every point is
    there once."""
    queued = await queue(container, 0, 1, 2)
    mark_published = OutboxRepository.mark_published

    async def die(*_: object) -> None:
        raise RuntimeError("the relay died before it could mark the batch")

    monkeypatch.setattr(OutboxRepository, "mark_published", die)
    with pytest.raises(RuntimeError, match="died"):
        await relay.drain_once()
    assert store.points(MARKET_ID) == queued
    assert await unpublished_ids(container.uow_factory) == [1, 2, 3]

    monkeypatch.setattr(OutboxRepository, "mark_published", mark_published)
    assert await relay.drain_once() == 3

    assert store.written == queued * 2
    assert store.points(MARKET_ID) == queued
    assert store.rows == 3
    assert await unpublished_ids(container.uow_factory) == []


GOOD: Final[dict[str, object]] = quote_updated_payload(point(9))

UNDELIVERABLE: Final[dict[str, str]] = {
    "an-empty-object": "{}",
    "a-missing-field": json.dumps({key: value for key, value in GOOD.items() if key != "ts"}),
    "a-missing-trade-id": json.dumps(
        {key: value for key, value in GOOD.items() if key != "trade_id"}
    ),
    "a-number-as-text": json.dumps(GOOD | {"version": "9"}),
    "a-fraction": json.dumps(GOOD | {"p_yes_micro": 0.5}),
    "a-number-no-bigint-holds": json.dumps(GOOD | {"q_yes_micro": 2**63}),
    "a-market-id-that-is-none": json.dumps(GOOD | {"market_id": "not-a-uuid"}),
    "a-trade-id-that-is-a-number": json.dumps(GOOD | {"trade_id": 7}),
    "an-instant-that-is-none": json.dumps(GOOD | {"ts": "yesterday"}),
    "an-instant-without-a-timezone": json.dumps(GOOD | {"ts": "2026-10-09T12:00:00"}),
    "an-instant-before-the-first-day": json.dumps(GOOD | {"ts": "0001-01-01T00:00:00+14:00"}),
    "an-instant-after-the-last-day": json.dumps(GOOD | {"ts": "9999-12-31T23:59:59-14:00"}),
    "a-list": "[1, 2]",
    "a-list-of-points": json.dumps([GOOD]),
    "a-text": '"quote.updated"',
    "a-number": "7",
    "true": "true",
    "null": "null",
}
"""Payloads no trade and no approval queues, as JSON text. None is ever a point."""


@pytest.mark.parametrize("payload", list(UNDELIVERABLE.values()), ids=list(UNDELIVERABLE))
async def test_a_row_that_can_never_be_delivered_is_logged_and_does_not_block_the_outbox(
    container: Container,
    store: MemoryHistory,
    relay: OutboxRelay,
    caplog: pytest.LogCaptureFixture,
    payload: str,
) -> None:
    """Whatever shape it has: it is counted as delivered, its neighbours are written,
    and an error names it. Waiting would not mend it, and behind it every later quote
    of every market would wait too."""
    (before,) = await queue(container, 0)
    poisoned = await queue_payload(container.uow_factory, payload, TEST_NOW)
    (after,) = await queue(container, 1)

    delivered = await relay.drain_once()

    assert delivered == 3
    assert store.written == [before, after]
    assert await unpublished_ids(container.uow_factory) == []
    (complaint,) = logged(caplog, logging.ERROR)
    assert f"Outbox row {poisoned} can never be delivered" in complaint.getMessage()
    assert await relay.drain_once() == 0


async def test_a_batch_of_nothing_but_undeliverable_rows_is_marked_and_writes_nothing(
    container: Container,
    store: MemoryHistory,
    relay: OutboxRelay,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for payload in ("{}", "[]", "null"):
        await queue_payload(container.uow_factory, payload, TEST_NOW)

    assert await relay.drain_once() == 3

    assert "write" not in store.calls
    assert await unpublished_ids(container.uow_factory) == []
    assert len(logged(caplog, logging.ERROR)) == 3


async def test_two_relays_take_disjoint_rows_and_neither_waits_for_the_other(
    container: Container,
) -> None:
    """FOR UPDATE SKIP LOCKED: while one relay is busy with a batch, a second one
    passes over those rows and takes the next ones."""
    queued = await queue(container, *range(6))
    slow, fast = MemoryHistory(), MemoryHistory()
    await slow.ensure_schema()
    await fast.ensure_schema()
    busy, may_finish = asyncio.Event(), asyncio.Event()

    async def hold(_: QuotePoint) -> None:
        busy.set()
        await may_finish.wait()

    slow.on_write = hold
    first = asyncio.create_task(relay_on(container, slow).drain_once())
    try:
        await asyncio.wait_for(busy.wait(), PATIENCE_SECONDS)
        second = relay_on(container, fast)

        assert await asyncio.wait_for(second.drain_once(), PATIENCE_SECONDS) == 3
        assert fast.written == queued[3:]
        # What is left is the first relay's, and the second one takes none of it.
        assert await unpublished_ids(container.uow_factory) == [1, 2, 3]
        assert await asyncio.wait_for(second.drain_once(), PATIENCE_SECONDS) == 0
    finally:
        may_finish.set()
        delivered_by_first = await asyncio.wait_for(first, PATIENCE_SECONDS)

    assert delivered_by_first == 3
    assert slow.written == queued[:3]
    assert fast.written == queued[3:]
    assert await unpublished_ids(container.uow_factory) == []


async def test_with_the_suites_unreachable_scylla_the_outbox_backs_up(
    container: Container, caplog: pytest.LogCaptureFixture
) -> None:
    """The application's own store, on the closed port the suite gives it."""
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    await queue(container, 0, 1)
    relay = relay_on(container, container.quote_history)

    with pytest.raises(ScyllaUnavailableError):
        await relay.drain_once()
    async with running(relay):
        await asyncio.sleep(20 * POLL_MS / 1000)

    assert await unpublished_ids(container.uow_factory) == [1, 2]
    assert len(logged(caplog, logging.WARNING)) == 1
    assert logged(caplog, logging.ERROR) == []
    assert logged(caplog, logging.INFO) == []


ROWS = st.lists(st.one_of(st.none(), st.sampled_from(sorted(UNDELIVERABLE.values()))), max_size=8)
"""The rows of an outbox, oldest first: None is a point, anything else a payload that
is none."""


@hypothesis_settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    rows=ROWS,
    batch_size=st.integers(min_value=1, max_value=4),
    failing_write=st.one_of(st.none(), st.integers(min_value=1, max_value=8)),
)
async def test_however_rows_mix_and_wherever_scylla_fails_draining_delivers_every_point_in_order(
    container: Container,
    rows: list[str | None],
    batch_size: int,
    failing_write: int | None,
) -> None:
    """For any outbox of deliverable and undeliverable rows, any batch size, and a
    ScyllaDB that fails one write of its choosing: once the relay has drained, every
    row is marked, ScyllaDB holds every point once and nothing else, and the points
    were first written in the order of their rows."""
    market_id = uuid7()
    store = MemoryHistory()
    await store.ensure_schema()
    relay = relay_on(container, store, batch_size=batch_size)
    points: list[QuotePoint] = []
    for version, payload in enumerate(rows):
        if payload is None:
            points.append(point(version, market_id))
            await queue_point(container.uow_factory, points[-1])
        else:
            await queue_payload(container.uow_factory, payload, TEST_NOW)
    writes = 0

    async def fail_once(_: QuotePoint) -> None:
        nonlocal writes
        writes += 1
        if writes == failing_write:
            raise ScyllaUnavailableError("one write that ScyllaDB did not take")

    store.on_write = fail_once

    failures = 0
    while await unpublished_ids(container.uow_factory):
        try:
            assert 1 <= await relay.drain_once() <= batch_size
        except ScyllaUnavailableError:
            failures += 1
    assert failures == (1 if failing_write is not None and failing_write <= writes else 0)

    assert store.points(market_id) == points
    first_writes = dict.fromkeys(owed for owed in store.written if owed.market_id == market_id)
    assert list(first_writes) == points
    assert await relay.drain_once() == 0


# --- The loop ---------------------------------------------------------------------------


async def test_run_forever_ensures_the_schema_before_the_first_batch(
    container: Container,
) -> None:
    """A node that has just been set up has no keyspace: a write before `ensure_schema`
    fails as it does during an outage (A.10). Once it is in place it is not asked for
    again, however often the loop goes round."""
    fresh = MemoryHistory()
    queued = await queue(container, 0, 1)
    with pytest.raises(ScyllaUnavailableError, match="no keyspace"):
        await fresh.write(queued[0])
    fresh.calls.clear()

    async with running(relay_on(container, fresh)):
        await until(drained(container))
        rounds = len(fresh.calls)
        await asyncio.sleep(20 * POLL_MS / 1000)

    assert fresh.calls == ["ensure_schema", "write", "write"]
    assert rounds == 3
    assert fresh.written == queued
    assert fresh.has_schema


async def test_run_forever_ensures_the_schema_again_after_an_outage_so_a_wiped_scylla_heals(
    container: Container, caplog: pytest.LogCaptureFixture
) -> None:
    """A.10: "The relay creates the keyspace and table if missing, so a wiped Scylla
    heals itself." What was delivered before the wipe is gone from ScyllaDB and stays
    in Postgres, where the chart finds it."""
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    memory = MemoryHistory()
    await queue(container, 0, 1)

    async with running(relay_on(container, memory)):
        await until(drained(container))
        memory.wipe()
        later = await queue(container, 2, 3)
        await until(drained(container))

    assert memory.has_schema
    assert memory.points(MARKET_ID) == later
    assert memory.calls == [
        "ensure_schema",
        "write",
        "write",
        "write",  # refused: the keyspace is gone
        "ensure_schema",
        "write",
        "write",
    ]
    assert len(logged(caplog, logging.WARNING)) == 1
    assert len(logged(caplog, logging.INFO)) == 1
    assert logged(caplog, logging.ERROR) == []


async def test_run_forever_logs_an_outage_once_however_long_it_lasts_and_its_end_once(
    container: Container, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    memory = MemoryHistory()
    memory.down = True
    queued = await queue(container, 0, 1)

    async with running(relay_on(container, memory)):
        await until(lambda: memory.calls.count("ensure_schema") >= 10)
        assert len(logged(caplog, logging.WARNING)) == 1
        assert logged(caplog, logging.INFO) == []
        assert await unpublished_ids(container.uow_factory) == [1, 2]

        memory.down = False
        await until(lambda: len(logged(caplog, logging.INFO)) == 1)
        assert await unpublished_ids(container.uow_factory) == []

        memory.down = True
        queued += await queue(container, 2)
        attempts = len(memory.calls)
        await until(lambda: len(memory.calls) >= attempts + 10)
        memory.down = False
        await until(drained(container))

    assert memory.written == queued
    warnings, infos = logged(caplog, logging.WARNING), logged(caplog, logging.INFO)
    assert (len(warnings), len(infos)) == (2, 2)
    assert all("ScyllaDB is unavailable" in record.getMessage() for record in warnings)
    assert all("ScyllaDB is back" in record.getMessage() for record in infos)
    assert logged(caplog, logging.ERROR) == []


async def test_a_full_batch_is_followed_by_the_next_at_once_and_a_short_one_by_a_wait(
    container: Container, store: MemoryHistory
) -> None:
    """With an idle wait of a minute: seven rows in batches of three are all delivered
    without it, the row that arrives after the short batch waits for it, and a stop
    ends it early."""
    queued = await queue(container, *range(7))
    relay = relay_on(container, store, poll_ms=LONG_POLL_MS)

    async with running(relay):
        await until(drained(container))
        assert store.written == queued

        await queue(container, 7)
        await asyncio.sleep(0.3)
        assert await unpublished_ids(container.uow_factory) == [8]

    assert store.written == queued
    assert await unpublished_ids(container.uow_factory) == [8]


async def test_run_forever_survives_any_other_exception_and_goes_on(
    container: Container, store: MemoryHistory, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug is logged with its traceback, every time, and is no outage of ScyllaDB:
    no warning, and the schema is not asked for again."""
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    queued = await queue(container, 0, 1)
    failures = 0

    async def fail_twice(_: QuotePoint) -> None:
        nonlocal failures
        if failures < 2:
            failures += 1
            raise LookupError("a bug in the store")

    store.on_write = fail_twice
    store.calls.clear()
    async with running(relay_on(container, store)):
        await until(drained(container))

    assert store.written == queued
    errors = logged(caplog, logging.ERROR)
    assert len(errors) == 2
    for record in errors:
        assert record.exc_info is not None
        assert record.exc_info[0] is LookupError
    assert logged(caplog, logging.WARNING) == []
    assert store.calls == ["ensure_schema", "write", "write", "write", "write"]


@pytest.mark.postgres_down
async def test_run_forever_keeps_running_while_postgres_is_unreachable_and_says_so_once(
    container: Container, store: MemoryHistory, caplog: pytest.LogCaptureFixture
) -> None:
    """SPEC §9: with Postgres down everything stops, and the relay stays up to go on
    when it returns. The real thing: the application's pool on a port nothing listens
    on. However many rounds fail, the log has one line about it and no traceback."""
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    rounds = 0

    def counted() -> UnitOfWork:
        nonlocal rounds
        rounds += 1
        return container.uow_factory()

    async with running(relay_on(container, store, uow_factory=counted)):
        await until(lambda: rounds >= 10)

    (warning,) = logged(caplog, logging.WARNING)
    assert "Postgres is unavailable" in warning.getMessage()
    assert warning.exc_info is None
    assert logged(caplog, logging.ERROR) == []
    assert logged(caplog, logging.INFO) == []
    assert store.written == []


async def test_run_forever_logs_a_postgres_outage_once_and_its_end_once(
    container: Container, store: MemoryHistory, caplog: pytest.LogCaptureFixture
) -> None:
    """Postgres refuses connections for a while, as it does while it restarts, and
    takes them again, twice over. Nothing was lost: the rows are delivered then. It
    was not ScyllaDB that was away, so its schema is not asked for a second time."""
    caplog.set_level(logging.INFO, logger=RELAY_LOGGER)
    queued = await queue(container, 0, 1)
    refusing = True
    rounds = 0

    def restarting() -> UnitOfWork:
        nonlocal rounds
        rounds += 1
        if refusing:
            raise ConnectionRefusedError("Postgres takes no connections")
        return container.uow_factory()

    async def some_rounds_later() -> None:
        seen = rounds
        await until(lambda: rounds >= seen + 10)

    store.calls.clear()
    async with running(relay_on(container, store, uow_factory=restarting)):
        await some_rounds_later()
        assert len(logged(caplog, logging.WARNING)) == 1
        assert await unpublished_ids(container.uow_factory) == [1, 2]

        refusing = False
        await until(lambda: len(logged(caplog, logging.INFO)) == 1)
        assert await unpublished_ids(container.uow_factory) == []
        await some_rounds_later()
        assert len(logged(caplog, logging.INFO)) == 1, "said once, however many batches follow"

        refusing = True
        await some_rounds_later()
        queued += await queue(container, 2)
        refusing = False
        await until(lambda: len(logged(caplog, logging.INFO)) == 2)

    assert store.written == queued
    warnings, infos = logged(caplog, logging.WARNING), logged(caplog, logging.INFO)
    assert (len(warnings), len(infos)) == (2, 2)
    assert all("Postgres is unavailable" in record.getMessage() for record in warnings)
    assert all("Postgres is back" in record.getMessage() for record in infos)
    assert logged(caplog, logging.ERROR) == []
    assert store.calls == ["ensure_schema", "write", "write", "write"]


async def test_run_forever_returns_at_once_when_the_stop_event_is_already_set(
    container: Container,
) -> None:
    untouched = MemoryHistory()
    await queue(container, 0)
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(
        relay_on(container, untouched, poll_ms=LONG_POLL_MS).run_forever(stop), PATIENCE_SECONDS
    )

    assert untouched.calls == []
    assert await unpublished_ids(container.uow_factory) == [1]


async def test_run_forever_finishes_the_batch_in_flight_and_starts_no_other_when_stopped(
    container: Container, store: MemoryHistory
) -> None:
    """The batch that is being written when the stop arrives is written to the end and
    marked; the full batch does not lead to the next one."""
    queued = await queue(container, *range(5))
    writing, may_finish = asyncio.Event(), asyncio.Event()

    async def hold(_: QuotePoint) -> None:
        writing.set()
        await may_finish.wait()

    store.on_write = hold
    async with running(relay_on(container, store, poll_ms=LONG_POLL_MS)) as stop:
        await asyncio.wait_for(writing.wait(), PATIENCE_SECONDS)
        stop.set()
        may_finish.set()

    assert store.written == queued[:BATCH_SIZE]
    assert await unpublished_ids(container.uow_factory) == [4, 5]


# --- The process ------------------------------------------------------------------------


def example_environment() -> dict[str, str]:
    """Every variable of `.example.env` with the value a process gets for it. Compose
    takes the quotes off a value that is written in quotes, and so does this."""
    written = assignments(EXAMPLE_ENV_FILE.read_text(encoding="utf-8"))
    return {
        name: value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value
        for name, value in written.items()
    }


def relay_environment(settings: Settings, scylla: ScyllaSettings) -> dict[str, str]:
    """The environment of a relay container: every variable of `.example.env`, with the
    stores of this test run in place of the stack's and a short idle wait.

    The process reads the environment before any `.env` file, and this names every
    variable, so whatever `.env` the machine has plays no part.
    """
    example = example_environment()
    return {
        **os.environ,
        **example,
        "APP_ENV": "test",
        "LOG_LEVEL": "INFO",
        "POSTGRES_HOST": settings.postgres.host,
        "POSTGRES_PORT": str(settings.postgres.port),
        "REDIS_HOST": settings.redis.host,
        "REDIS_PORT": str(settings.redis.port),
        "SCYLLA_HOSTS": ",".join(scylla.hosts),
        "SCYLLA_PORT": str(scylla.port),
        "OUTBOX_POLL_INTERVAL_MS": "50",
    }


class RelayProcess:
    """`python -m app.relay` as a child process, with what it logged so far."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self.log: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._process.returncode is None

    async def logs(self, line: str) -> None:
        """Read the log until `line` is in it; fail when the process ends first."""
        assert self._process.stderr is not None
        async with asyncio.timeout(PATIENCE_SECONDS * 3):
            while not any(line in logged_line for logged_line in self.log):
                raw = await self._process.stderr.readline()
                assert raw, f"the relay ended before it logged {line!r}:\n" + "\n".join(self.log)
                self.log.append(raw.decode().rstrip())

    async def stop(self, stop_signal: signal.Signals) -> int:
        """Send `stop_signal`, wait for the process to end and return its exit status."""
        assert self._process.stderr is not None
        self._process.send_signal(stop_signal)
        async with asyncio.timeout(PATIENCE_SECONDS * 3):
            rest = await self._process.stderr.read()
            status = await self._process.wait()
        self.log.extend(rest.decode().splitlines())
        return status

    async def kill(self) -> None:
        if self.is_running:
            self._process.kill()
            await self._process.wait()


@asynccontextmanager
async def relay_process(environment: dict[str, str]) -> AsyncIterator[RelayProcess]:
    """Start the relay the way its container does and make sure it is gone afterwards."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.relay",
        cwd=BACKEND_ROOT,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    relay = RelayProcess(process)
    try:
        yield relay
    finally:
        await relay.kill()


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
async def test_the_relay_process_starts_idles_and_stops_cleanly_on_a_signal(
    container: Container, settings: Settings, stop_signal: signal.Signals
) -> None:
    """`docker stop` sends SIGTERM. With ScyllaDB down, as in the suite, the process
    says so once, leaves the outbox alone, keeps running, and ends with status 0 on the
    signal, having released its connections."""
    await queue(container, 0, 1)

    async with relay_process(relay_environment(settings, settings.scylla)) as relay:
        await relay.logs(STARTED)
        await relay.logs("ScyllaDB is unavailable")
        # Thirty idle waits of the 50 ms it was given.
        await asyncio.sleep(1.5)
        assert relay.is_running
        status = await relay.stop(stop_signal)

    assert status == 0, "\n".join(relay.log)
    assert relay.log[-1].endswith(STOPPED)
    assert sum("ScyllaDB is unavailable" in line for line in relay.log) == 1
    assert not any("Traceback" in line or " ERROR " in line for line in relay.log), relay.log
    assert await unpublished_ids(container.uow_factory) == [1, 2]


# --- The real ScyllaDB node (slow to start: run with `-m scylla`) ----------------------


@pytest.mark.scylla
async def test_the_relay_process_delivers_to_the_real_scylla_and_stops_cleanly_on_sigterm(
    container: Container,
    settings: Settings,
    scylla_client: ScyllaClient,
    scylla_settings: ScyllaSettings,
) -> None:
    """The whole relay container, but for Docker: its command, its environment, the
    Postgres of the tests and a ScyllaDB node. It creates the schema, drains what was
    queued before and while it ran, idles, and ends with status 0."""
    market_id = uuid7()
    earlier = [point(version, market_id) for version in range(3)]
    for queued in earlier:
        await queue_point(container.uow_factory, queued)

    async with relay_process(relay_environment(settings, scylla_settings)) as relay:
        await relay.logs(STARTED)
        await until(drained(container), within_seconds=NODE_SECONDS)
        later = point(3, market_id)
        await queue_point(container.uow_factory, later)
        await until(drained(container), within_seconds=NODE_SECONDS)
        await asyncio.sleep(0.5)
        assert relay.is_running
        status = await relay.stop(signal.SIGTERM)

    assert status == 0, "\n".join(relay.log)
    assert relay.log[-1].endswith(STOPPED)
    assert not any("Traceback" in line for line in relay.log), relay.log
    real = QuoteHistoryStore(scylla_client, scylla_settings)
    assert await real.read(
        market_id,
        TEST_NOW,
        TEST_NOW + timedelta(hours=1),
        limit=scylla_settings.history_read_limit,
    ) == [*earlier, later]


@pytest.mark.scylla
async def test_run_forever_creates_a_missing_keyspace_and_heals_a_wiped_one_on_the_real_scylla(
    container: Container,
    scylla_client: ScyllaClient,
    scylla_settings: ScyllaSettings,
) -> None:
    """A.10 on the node itself, in a keyspace of this test alone, so that dropping it
    is nobody else's outage."""
    await ready(QuoteHistoryStore(scylla_client, scylla_settings))
    own = scylla_settings.model_copy(
        update={"keyspace": f"relay_{uuid7().hex}", "outbox_poll_interval_ms": 50}
    )
    store = QuoteHistoryStore(scylla_client, own)
    relay = OutboxRelay(container.uow_factory, store, own, container.clock)
    market_id = uuid7()
    before = [point(version, market_id) for version in range(2)]
    after = [point(version, market_id) for version in range(2, 4)]
    schema_timeout = scylla_settings.request_timeout_seconds * 10
    hour = (TEST_NOW, TEST_NOW + timedelta(hours=1))
    with pytest.raises(ScyllaUnavailableError):
        await store.write(before[0])

    try:
        for queued in before:
            await queue_point(container.uow_factory, queued)
        stop = asyncio.Event()
        task = asyncio.create_task(relay.run_forever(stop))
        try:
            await until(drained(container), within_seconds=NODE_SECONDS)
            assert await store.read(market_id, *hour, limit=own.history_read_limit) == before

            await scylla_client.execute(
                f"DROP KEYSPACE {own.keyspace}", timeout_seconds=schema_timeout
            )
            for queued in after:
                await queue_point(container.uow_factory, queued)
            await until(drained(container), within_seconds=NODE_SECONDS)
        finally:
            stop.set()
            await asyncio.wait_for(task, 60)

        assert await store.read(market_id, *hour, limit=own.history_read_limit) == after
    finally:
        with suppress(ScyllaUnavailableError):
            await scylla_client.execute(
                f"DROP KEYSPACE IF EXISTS {own.keyspace}", timeout_seconds=schema_timeout
            )


@pytest.mark.scylla
async def test_delivering_a_batch_twice_to_the_real_scylla_leaves_one_row_per_point(
    container: Container,
    clock: FrozenClock,
    scylla_client: ScyllaClient,
    scylla_settings: ScyllaSettings,
) -> None:
    """SPEC §9: "Writes are upserts keyed by `version`, so delivering a row twice is
    harmless." The instants carry microseconds, which the first delivery cuts and the
    second cuts the same way."""
    store = await ready(QuoteHistoryStore(scylla_client, scylla_settings))
    relay = OutboxRelay(container.uow_factory, store, scylla_settings, clock)
    market_id = uuid7()
    queued = [
        QuotePoint(
            market_id=market_id,
            ts=TEST_NOW + timedelta(seconds=version, microseconds=123_456),
            version=version,
            p_yes_micro=500_000 + version,
            q_yes_micro=version * MICRO,
            q_no_micro=0,
            trade_id=uuid7() if version else None,
        )
        for version in range(3)
    ]
    for owed in queued:
        await queue_point(container.uow_factory, owed)

    assert await relay.drain_once() == 3
    async with container.uow_factory() as uow:
        await uow.session.execute(text("UPDATE outbox SET published_at = NULL"))
    assert await relay.drain_once() == 3

    stored = await store.read(
        market_id,
        TEST_NOW,
        TEST_NOW + timedelta(hours=1),
        limit=scylla_settings.history_read_limit,
    )
    assert stored == [
        QuotePoint(
            market_id=market_id,
            ts=to_milliseconds(owed.ts),
            version=owed.version,
            p_yes_micro=owed.p_yes_micro,
            q_yes_micro=owed.q_yes_micro,
            q_no_micro=owed.q_no_micro,
            trade_id=owed.trade_id,
        )
        for owed in queued
    ]
    assert await unpublished_ids(container.uow_factory) == []
