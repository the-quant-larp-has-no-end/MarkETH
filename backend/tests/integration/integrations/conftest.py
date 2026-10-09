"""Fixtures for the adapter tests: throwaway Mailpit and ScyllaDB, and dead backends.

Redis is the session's shared container: `redis_container`, `redis_settings` and
`redis_client` come from the top-level conftest. Every container starts on first use and
lives for the test session, so the default run never starts ScyllaDB. Settings come from
the shared `base_settings` with only the address replaced, which keeps the timeouts and
TTLs under test the ones in `.example.env`.
"""

import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

import pytest
from redis.asyncio import Redis
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.core.config import RedisSettings, ScyllaSettings, Settings
from app.integrations.redis.client import RedisClient
from app.integrations.scylla.client import ScyllaClient
from tests.support.containers import published_port

# The images compose.yaml runs.
MAILPIT_IMAGE: Final[str] = "axllent/mailpit:v1.31"
MAILPIT_SMTP_PORT: Final[int] = 1025
MAILPIT_HTTP_PORT: Final[int] = 8025

SCYLLA_IMAGE: Final[str] = "scylladb/scylla:2026.1"
SCYLLA_PORT: Final[int] = 9042
# The single development node of SPEC §9.
SCYLLA_COMMAND: Final[str] = "--smp 1 --memory 750M --overprovisioned 1 --developer-mode 1"
SCYLLA_STARTUP_TIMEOUT_SECONDS: Final[int] = 300

LOOPBACK: Final[str] = "127.0.0.1"


@pytest.fixture(autouse=True)
def _clean_database() -> None:
    """Replaces the shared database reset: no adapter test touches Postgres or starts it."""


@pytest.fixture
async def raw_redis(redis_settings: RedisSettings) -> AsyncIterator[Redis]:
    """A plain redis-py connection, for looking at keys and for breaking things on purpose."""
    raw = Redis(host=redis_settings.host, port=redis_settings.port, decode_responses=True)
    yield raw
    await raw.aclose()


@pytest.fixture
def closed_port() -> int:
    """A local port nothing listens on: connecting to it is refused at once."""
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        port: int = probe.getsockname()[1]
    return port


@pytest.fixture
async def unreachable_redis(
    base_settings: Settings, closed_port: int
) -> AsyncIterator[RedisClient]:
    """A client whose Redis is down. Needs no container."""
    settings = base_settings.redis.model_copy(update={"host": LOOPBACK, "port": closed_port})
    client = RedisClient(settings)
    yield client
    await client.close()


@dataclass
class SilentServer:
    """A TCP server that accepts every connection and never answers."""

    port: int
    connections: int = 0


@pytest.fixture
async def silent_server() -> AsyncIterator[SilentServer]:
    """A backend that hangs: clients connect, then wait until their own timeout."""

    state = SilentServer(port=0)

    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        state.connections += 1
        with suppress(ConnectionError):
            await reader.read()
        writer.close()

    server = await asyncio.start_server(swallow, LOOPBACK, 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()


class SwitchableProxy:
    """A TCP forwarder in front of a real backend that can be cut and restored.

    Cutting closes every open connection and leaves new ones unanswered, which is what
    a client sees when its backend's host goes away; restoring is the backend coming
    back. The backend itself keeps running, so a test needs no container restart and
    keeps its data.
    """

    def __init__(self, backend_host: str, backend_port: int) -> None:
        self._backend = (backend_host, backend_port)
        self._open: set[asyncio.StreamWriter] = set()
        self._is_cut = False
        self._server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._forward, LOOPBACK, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server.close_clients()
            await self._server.wait_closed()

    def cut(self) -> None:
        self._is_cut = True
        for writer in self._open:
            writer.close()

    def restore(self) -> None:
        self._is_cut = False

    async def _forward(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._is_cut:
            with suppress(ConnectionError):
                await reader.read()
            writer.close()
            return
        try:
            backend_reader, backend_writer = await asyncio.open_connection(*self._backend)
        except OSError:
            writer.transport.abort()
            return
        pair = (writer, backend_writer)
        self._open.update(pair)

        async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
            with suppress(ConnectionError):
                while chunk := await source.read(65536):
                    sink.write(chunk)
                    await sink.drain()
            # One direction ending ends the pair, which also stops the other pump.
            for end in pair:
                end.transport.abort()

        await asyncio.gather(pump(reader, backend_writer), pump(backend_reader, writer))
        self._open.difference_update(pair)


@dataclass(frozen=True)
class Mailpit:
    host: str
    smtp_port: int
    api_url: str


@pytest.fixture(scope="session")
def mailpit() -> Iterator[Mailpit]:
    container = (
        DockerContainer(MAILPIT_IMAGE)
        .with_exposed_ports(MAILPIT_SMTP_PORT, MAILPIT_HTTP_PORT)
        .waiting_for(LogMessageWaitStrategy(r"\[http\] accessible via"))
    )
    with container:
        host = container.get_container_host_ip()
        yield Mailpit(
            host=host,
            smtp_port=published_port(container, MAILPIT_SMTP_PORT),
            api_url=f"http://{host}:{published_port(container, MAILPIT_HTTP_PORT)}",
        )


@pytest.fixture(scope="session")
def scylla_settings(base_settings: Settings) -> Iterator[ScyllaSettings]:
    """The ScyllaDB section of `.example.env`, pointing at a single-node container."""
    ready = LogMessageWaitStrategy("Starting listening for CQL clients").with_startup_timeout(
        SCYLLA_STARTUP_TIMEOUT_SECONDS
    )
    container = (
        DockerContainer(SCYLLA_IMAGE)
        .with_command(SCYLLA_COMMAND)
        .with_exposed_ports(SCYLLA_PORT)
        .waiting_for(ready)
    )
    with container:
        yield base_settings.scylla.model_copy(
            update={
                "hosts": [container.get_container_host_ip()],
                "port": published_port(container, SCYLLA_PORT),
            }
        )


@pytest.fixture
async def scylla_client(scylla_settings: ScyllaSettings) -> AsyncIterator[ScyllaClient]:
    client = ScyllaClient(scylla_settings)
    yield client
    await client.close()


@pytest.fixture
async def scylla_proxy(scylla_settings: ScyllaSettings) -> AsyncIterator[SwitchableProxy]:
    """The ScyllaDB container behind a proxy a test can cut, to let the node "die"."""
    proxy = SwitchableProxy(scylla_settings.hosts[0], scylla_settings.port)
    await proxy.start()
    yield proxy
    await proxy.close()
