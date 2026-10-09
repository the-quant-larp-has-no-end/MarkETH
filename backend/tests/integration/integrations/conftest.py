"""Fixtures for the adapter tests alone: a throwaway Mailpit and a look into Redis.

Everything a test of the application can use as well lives one level up, in
`tests/conftest.py`: the Redis and ScyllaDB containers, their settings and clients, a
Redis that is down and a ScyllaDB behind a proxy that can be cut.

No adapter test touches Postgres, so the database reset is switched off here. A test
that needs both an adapter and a clean database belongs in `tests/integration/`.
"""

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
from redis.asyncio import Redis
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.core.config import RedisSettings
from tests.support import network
from tests.support.containers import (
    MAILPIT_HTTP_PORT,
    MAILPIT_IMAGE,
    MAILPIT_SMTP_PORT,
    closed_port,
    published_port,
)
from tests.support.network import SilentServer


@pytest.fixture(autouse=True)
def _clean_database() -> None:
    """Replaces the shared database reset: no adapter test touches Postgres or starts it."""


@pytest.fixture
async def raw_redis(redis_settings: RedisSettings) -> AsyncIterator[Redis]:
    """A plain redis-py connection, for looking at keys and for breaking things on purpose."""
    raw = Redis(host=redis_settings.host, port=redis_settings.port, decode_responses=True)
    yield raw
    await raw.aclose()


@pytest.fixture(name="closed_port")
def closed_port_fixture() -> int:
    """A local port nothing listens on: connecting to it is refused at once."""
    return closed_port()


@pytest.fixture
async def silent_server() -> AsyncIterator[SilentServer]:
    """A backend that hangs: clients connect, then wait until their own timeout."""
    async with network.silent_server() as server:
        yield server


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
