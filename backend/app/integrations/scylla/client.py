"""ScyllaDB access that never blocks the event loop (SPEC §9).

ScyllaDB holds the price history behind the charts and nothing else. The API starts and
trades without it and the relay retries until it is back, so this module promises one
thing: `execute` returns rows or raises `ScyllaUnavailableError` within its timeout.

`scylla-driver` (import name `cassandra`) is built on threads, and three of its calls
can block: opening a cluster, shutting one down, and handing over a statement, which
waits up to two seconds for a usable connection when the pooled one has just died.
All three run in a small thread pool of their own. The response futures are bridged to
asyncio futures, so a statement in flight holds no thread.
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any, Final

from cassandra import ProtocolVersion
from cassandra.cluster import (
    EXEC_PROFILE_DEFAULT,
    Cluster,
    ExecutionProfile,
    ResponseFuture,
    Session,
)
from cassandra.policies import ConstantReconnectionPolicy, RoundRobinPolicy
from cassandra.query import dict_factory

from app.core.config import ScyllaSettings

logger = logging.getLogger(__name__)

CONNECT_RETRY_SECONDS: Final[float] = 5.0
"""Pause between connection attempts while ScyllaDB is down, here and inside the driver."""

_DRIVER_THREADS: Final[int] = 4
"""Threads for the driver calls that can block. Their own pool, so a dying ScyllaDB
cannot use up the loop's default executor, which also resolves host names."""

type Row = dict[str, Any]


class ScyllaUnavailableError(Exception):
    """ScyllaDB did not run a statement: unreachable, timed out, or it reported an error."""


class ScyllaClient:
    """One lazily opened ScyllaDB session per process.

    When ScyllaDB is unreachable: `execute` raises `ScyllaUnavailableError` after at
    most its timeout, and the event loop keeps running throughout. The first call starts
    a connection attempt in a worker thread and waits for it for one request timeout;
    the attempt keeps running, so a slow connect still serves later calls. After a
    failed attempt every call fails at once for `CONNECT_RETRY_SECONDS`, then the next
    call starts a new attempt. Once a session exists the driver reconnects on its own at
    that same pace, and statements fail without waiting while the node is down.
    """

    def __init__(self, settings: ScyllaSettings) -> None:
        self._settings = settings
        self._request_timeout = settings.request_timeout_seconds
        self._threads = ThreadPoolExecutor(_DRIVER_THREADS, thread_name_prefix="scylla")
        self._cluster: Cluster | None = None
        self._session: Session | None = None
        self._connecting: asyncio.Task[None] | None = None
        self._last_failure: Exception | None = None
        self._retry_at = 0.0
        self._closed = False

    @property
    def is_connected(self) -> bool:
        """Whether a session is open and the driver sees a live node. Never connects.

        This is the driver's view, which trails a node's death by a second or two.
        """
        cluster = self._cluster
        if cluster is None:
            return False
        return any(host.is_up for host in cluster.metadata.all_hosts())

    async def execute(
        self,
        cql: str,
        parameters: Sequence[Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> list[Row]:
        """Run one statement (`%s` placeholders) and return all of its rows as dicts.

        The call ends within `SCYLLA_REQUEST_TIMEOUT_MS`, every result page included.
        `timeout_seconds` replaces that limit for statements known to be slow, such as
        schema changes.
        """
        session = await self._connected_session()
        timeout = timeout_seconds or self._request_timeout
        loop = asyncio.get_running_loop()
        # A failure travels as the future's result, not its exception: a caller that
        # has already timed out leaves nobody to retrieve an exception that arrives late.
        outcome: asyncio.Future[list[Row] | BaseException] = loop.create_future()
        rows: list[Row] = []

        # Both callbacks run on the driver's I/O thread, which swallows what they raise.
        def on_page(page: object, response: ResponseFuture) -> None:
            try:
                if isinstance(page, list):
                    rows.extend(page)
                if response.has_more_pages:
                    response.start_fetching_next_page()
                    return
            except Exception as exc:
                on_error(exc)
                return
            _settle(loop, outcome, rows)

        def on_error(error: BaseException) -> None:
            _settle(loop, outcome, error)

        def submit() -> None:
            response = session.execute_async(cql, parameters, timeout=timeout)
            response.add_callbacks(on_page, on_error, callback_args=(response,))

        # The driver's failures share no base class: any exception from it means the
        # statement did not run, and the caller's answer to that is always the same.
        try:
            async with asyncio.timeout(timeout):
                await loop.run_in_executor(self._threads, submit)
                result = await outcome
        except TimeoutError as exc:
            raise ScyllaUnavailableError(f"ScyllaDB did not answer within {timeout:g} s") from exc
        except Exception as exc:
            raise _statement_failed(exc) from exc
        if isinstance(result, BaseException):
            raise _statement_failed(result) from result
        return result

    async def close(self) -> None:
        """Shut the driver down. Call once, on shutdown; `execute` fails afterwards."""
        self._closed = True
        if self._connecting is not None:
            # The attempt cannot be interrupted; wait so its cluster is not leaked.
            await self._connecting
        cluster, self._cluster, self._session = self._cluster, None, None
        if cluster is not None:
            await asyncio.get_running_loop().run_in_executor(self._threads, cluster.shutdown)
        self._threads.shutdown(wait=False)

    async def _connected_session(self) -> Session:
        if self._closed:
            raise ScyllaUnavailableError("the ScyllaDB client is closed")
        if self._session is None:
            await self._join_connection_attempt()
        if self._session is None:
            raise ScyllaUnavailableError("ScyllaDB is not connected") from self._last_failure
        return self._session

    async def _join_connection_attempt(self) -> None:
        """Wait, for one request timeout at most, for the running attempt or a new one."""
        if self._connecting is None:
            if time.monotonic() < self._retry_at:
                return
            self._connecting = asyncio.create_task(self._connect(), name="scylla-connect")
        with suppress(TimeoutError):
            async with asyncio.timeout(self._request_timeout):
                await asyncio.shield(self._connecting)

    async def _connect(self) -> None:
        """One connection attempt. It records its outcome instead of raising, because
        several callers may be waiting for it and none of them may be left."""
        try:
            self._cluster, self._session = await asyncio.get_running_loop().run_in_executor(
                self._threads, self._open
            )
        except Exception as exc:
            if self._last_failure is None:
                logger.warning("ScyllaDB is unreachable: %r", exc)
            self._last_failure = exc
            self._retry_at = time.monotonic() + CONNECT_RETRY_SECONDS
        else:
            self._last_failure = None
            logger.info("Connected to ScyllaDB")
        finally:
            self._connecting = None

    def _open(self) -> tuple[Cluster, Session]:
        """Blocking: open a cluster and a session. Runs in a worker thread."""
        hosts = self._settings.hosts
        cluster = Cluster(
            contact_points=hosts,
            port=self._settings.port,
            protocol_version=ProtocolVersion.V4,
            connect_timeout=self._request_timeout,
            reconnection_policy=ConstantReconnectionPolicy(CONNECT_RETRY_SECONDS),
            address_translator=_ContactPointTranslator(hosts[0]) if len(hosts) == 1 else None,
            execution_profiles={
                EXEC_PROFILE_DEFAULT: ExecutionProfile(
                    load_balancing_policy=RoundRobinPolicy(),
                    request_timeout=self._request_timeout,
                    row_factory=dict_factory,
                )
            },
        )
        try:
            return cluster, cluster.connect()
        except BaseException:
            cluster.shutdown()
            raise


class _ContactPointTranslator:
    """Sends every connection to the one configured host instead of the node's own address.

    A node advertises the address it has inside its own network. A client outside that
    network cannot reach it: a process on the developer's machine talking to the compose
    stack, or a test going through a published port. With a single configured host, that
    host is the whole cluster as far as this client can tell. The driver only calls
    `translate`, so this does not need its `AddressTranslator` base class.
    """

    def __init__(self, host: str) -> None:
        self._host = host

    def translate(self, addr: str) -> str:
        return self._host


def _statement_failed(cause: BaseException) -> ScyllaUnavailableError:
    return ScyllaUnavailableError(f"ScyllaDB did not run the statement: {cause!r}")


def _settle[T](loop: asyncio.AbstractEventLoop, outcome: asyncio.Future[T], value: T) -> None:
    """Complete `outcome` on its own loop; callable from any thread."""

    def apply() -> None:
        # Done already means the caller stopped waiting.
        if not outcome.done():
            outcome.set_result(value)

    # A late answer after the loop has closed has nobody left to tell.
    with suppress(RuntimeError):
        loop.call_soon_threadsafe(apply)
