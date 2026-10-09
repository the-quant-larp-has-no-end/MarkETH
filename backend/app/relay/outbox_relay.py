"""Copies committed quotes from the Postgres outbox into ScyllaDB (SPEC §9, A.4, A.10).

Every trade and every market approval queues a `quote.updated` row in its own
transaction. The relay delivers those rows to the price history at least once: a row is
marked published only after ScyllaDB took it, and writing a point twice leaves one row.
With ScyllaDB down nothing is marked; the outbox backs up and drains once it is back.
"""

import asyncio

from app.core.clock import Clock
from app.core.config import ScyllaSettings
from app.integrations.scylla.quote_history import QuoteHistoryStore
from app.services import UowFactory


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
        2. Write each row to ScyllaDB in id order:
           `QuoteHistoryStore.write(quote_point_from_payload(row.payload))`. A payload
           that does not parse (`KeyError`, `ValueError`) is logged as an error with
           the row's id and counted as delivered: it can never succeed, and one bad
           row must not block the outbox.
        3. `outbox.mark_published(<ids>, clock.now())` and commit.

        `ScyllaUnavailableError` from any write leaves the block, so the transaction
        rolls back, no row of the batch is marked and all of them are retried. Points
        already written are written again then, which is harmless.
        """
        raise NotImplementedError

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
        * Any other exception (Postgres unreachable, a bug): log it with its
          traceback, wait one poll interval, go on. The process stays up.

        Returns once `stop_event` is set and the batch in flight has finished.
        """
        raise NotImplementedError
