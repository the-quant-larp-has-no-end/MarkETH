"""Copies committed quotes from the Postgres outbox into ScyllaDB (SPEC §9, A.4, A.10).

Every trade and every market approval queues a `quote.updated` row in its own
transaction. The relay delivers those rows to the price history at least once: a row is
marked published only after ScyllaDB took it, and writing a point twice leaves one row.
With ScyllaDB down nothing is marked; the outbox backs up and drains once it is back.
"""

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC

from app.core.clock import Clock
from app.core.config import ScyllaSettings
from app.db import database
from app.integrations.scylla.client import ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint
from app.models.ops import OutboxEvent
from app.services import UowFactory
from app.services.outbox_events import quote_point_from_payload

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        uow_factory: UowFactory,
        history: QuoteHistoryStore,
        settings: ScyllaSettings,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._history = history
        self._batch_size = settings.outbox_batch_size
        self._poll_interval_seconds = settings.outbox_poll_interval_seconds
        self._clock = clock

    async def drain_once(self) -> int:
        """Deliver one batch and return how many rows it held.

        One transaction:
        1. `outbox.claim_unpublished(OUTBOX_BATCH_SIZE)`: the oldest unpublished rows,
           locked with FOR UPDATE SKIP LOCKED, so a second relay takes other rows.
           None: return 0.
        2. Deliver each row in id order, in two separate steps:
           * Read the point the row carries (`_point_of`). A row whose payload is no
             point can never be delivered, whatever it holds instead: log an error
             with the row's id and count the row as delivered, so that one bad row
             does not block the outbox.
           * `await QuoteHistoryStore.write(point)`, outside that guard: whatever a
             write raises says something about ScyllaDB, not about the row.
        3. `outbox.mark_published(<ids>, clock.now())`, the skipped rows included, and
           commit.

        `ScyllaUnavailableError` from any write leaves the block, so the transaction
        rolls back, no row of the batch is marked and all of them are retried. Points
        already written are written again then, which is harmless.
        """
        async with self._uow_factory() as uow:
            events = await uow.outbox.claim_unpublished(self._batch_size)
            if not events:
                return 0
            for event in events:
                point = _point_of(event)
                if point is not None:
                    await self._history.write(point)
            await uow.outbox.mark_published([event.id for event in events], self._clock.now())
            return len(events)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Drain the outbox until `stop_event` is set. Never raises.

        * Before the first batch, and again after any `ScyllaUnavailableError`, call
          `QuoteHistoryStore.ensure_schema()`: it creates the keyspace and the table
          if they are missing, so a wiped ScyllaDB heals itself (A.10). A missing
          keyspace and an outage raise the same error, hence "again".
        * Loop: `drain_once()`. A full batch means more is waiting: go on at once.
          Otherwise wait `OUTBOX_POLL_INTERVAL_MS`, or less if `stop_event` is set
          meanwhile (`asyncio.wait_for(stop_event.wait(), timeout)`).
        * `ScyllaUnavailableError`: log one warning when the outage starts and one
          info when a batch succeeds again, wait one poll interval, retry.
        * Postgres unreachable (`database.is_unavailable`: what the API answers with
          503): the same, one warning when the outage starts and one info when a
          batch succeeds again. A traceback for every round of an outage would push
          everything else out of the relay's log.
        * Any other exception is a bug: log it with its traceback, every time, wait
          one poll interval, go on. The process stays up.

        Returns once `stop_event` is set and the batch in flight has finished.
        """
        schema_ready = False
        scylla_is_down = False
        postgres_is_down = False
        while not stop_event.is_set():
            delivered = 0
            try:
                if not schema_ready:
                    await self._history.ensure_schema()
                    schema_ready = True
                delivered = await self.drain_once()
            except ScyllaUnavailableError as exc:
                schema_ready = False
                if not scylla_is_down:
                    scylla_is_down = True
                    logger.warning(
                        "ScyllaDB is unavailable; the outbox backs up until it returns: %s", exc
                    )
            except Exception as exc:
                if not database.is_unavailable(exc):
                    logger.exception(
                        "The outbox relay could not deliver a batch; it tries again in %g s",
                        self._poll_interval_seconds,
                    )
                elif not postgres_is_down:
                    postgres_is_down = True
                    logger.warning(
                        "Postgres is unavailable; the outbox waits until it returns: %r", exc
                    )
            else:
                if scylla_is_down:
                    scylla_is_down = False
                    logger.info("ScyllaDB is back; the outbox drains")
                if postgres_is_down:
                    postgres_is_down = False
                    logger.info("Postgres is back; the outbox drains")
            if delivered < self._batch_size:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), self._poll_interval_seconds)


def _point_of(event: OutboxEvent) -> QuotePoint | None:
    """The quote point an outbox row carries, or None for a row that can never be
    delivered; that one is logged as an error with its id.

    Three things make a row undeliverable, and waiting mends none of them:

    * its payload is not a JSON object. The column is JSONB and takes a list, a text,
      a number or `null` as readily as an object, whatever the model's type says;
    * a field is missing or holds something a point does not: `KeyError` and
      `ValueError`, the two errors `quote_point_from_payload` raises;
    * its instant has no day in UTC (`OverflowError`): one within hours of the first
      or the last day of the calendar, written with an offset that puts it beyond.
      The history keys a point by that day and could not store it.
    """
    payload: object = event.payload
    if not isinstance(payload, Mapping):
        logger.error(
            "Outbox row %d can never be delivered and is skipped: its payload is not a JSON object",
            event.id,
        )
        return None
    try:
        point = quote_point_from_payload(payload)
        point.ts.astimezone(UTC)
    except (KeyError, ValueError, OverflowError) as exc:
        logger.error("Outbox row %d can never be delivered and is skipped: %r", event.id, exc)
        return None
    return point
