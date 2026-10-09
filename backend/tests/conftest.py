"""Fixtures shared by all backend tests.

Unit tests need nothing external: Docker starts only when a test asks for a container
fixture, directly, through another fixture, or by carrying the `integration` marker.
Containers and the connection pool are session-scoped and run on pytest-asyncio's
session event loop (see `[tool.pytest.ini_options]`), so one Postgres and one Redis
serve the run.

What a test of the API or of a service uses (mark the module `integration`):

    settings    `.example.env`, pointing at the Postgres and Redis containers. ScyllaDB
                and SMTP point at closed ports: the default suite proves the API works
                without ScyllaDB, and no test can mail anyone.
    clock       a `FrozenClock`; move it with `advance()` or `set()`
    mailer      a `MemoryMailer`; `mailer.last_token` is the token of the last link
    container   the application's `Container`, built with that clock and mailer on the
                shared database, started, and stopped after the test
    factory     writes users, markets and positions directly (tests/support/factories.py)
    app         the FastAPI application around that container
    client      an `httpx.AsyncClient` on it; paths start with `/api`, and the Origin
                header is already set
    as_user     `as_user(user)` makes every request come from that user; `as_user(None)`
                makes it anonymous again

Every `integration` test starts with an empty database (system accounts at zero), and
every test that uses `container` with an empty Redis. Redis runs in real time: a cached
market list outlives a `clock.advance()`, so call `await flush_redis()` after moving the
clock when a cache or a rate-limit window matters.

Two Docker Desktop quirks are absorbed here, once, for every container fixture in the
suite: where the reaper finds the Docker socket, and a race while it starts.
"""

import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Final

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import Reaper

from app.api.deps import authenticate_request
from app.container import Container
from app.core.config import REPO_ROOT, RedisSettings, Settings
from app.core.identity import AuthenticatedUser
from app.db.database import Database
from app.integrations.redis.client import RedisClient
from app.main import create_app
from app.models import User
from app.repositories.unit_of_work import UnitOfWork
from tests.support.clock import FrozenClock
from tests.support.containers import (
    LOOPBACK,
    POSTGRES_COMMAND,
    POSTGRES_IMAGE,
    POSTGRES_PORT,
    REDIS_IMAGE,
    REDIS_PORT,
    closed_port,
    published_port,
)
from tests.support.database import reset_database, upgrade_database
from tests.support.factories import ActAs, Factory, identity_of
from tests.support.mail import MemoryMailer

if sys.platform == "darwin":
    # On macOS Docker runs in a VM. The socket the client talks to on the host (under the
    # home directory with Docker Desktop) cannot be mounted into Testcontainers' reaper;
    # inside the VM the daemon listens on the standard path. A developer's own setting wins.
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

EXAMPLE_ENV_FILE: Final[Path] = REPO_ROOT / ".example.env"

DOCKER_MARKERS: Final[tuple[str, ...]] = ("integration", "scylla")
REAPER_START_ATTEMPTS: Final[int] = 10


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Have Testcontainers' reaper running before a test that needs Docker sets up."""
    needs_docker = any(item.get_closest_marker(name) is not None for name in DOCKER_MARKERS)
    if needs_docker and not testcontainers_config.ryuk_disabled:
        _start_reaper()


def _start_reaper() -> None:
    """Start the container that removes ours if the run is killed, retrying a known race.

    Testcontainers looks up the reaper's port the moment its container runs. Docker
    Desktop often publishes the port a moment later, and the lookup raises
    `ConnectionError`: 5 of 12 starts on a busy machine. Left alone, that fails whichever
    container fixture happens to run first. Started here, no fixture has to start it; once
    it runs, this returns at once.
    """
    for _ in range(REAPER_START_ATTEMPTS - 1):
        try:
            Reaper.get_instance()
        except ConnectionError:
            # Stops the half-started container, which frees its name for the next attempt.
            Reaper.delete_instance()
        else:
            return
    Reaper.get_instance()


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    """The committed `.example.env` with APP_ENV=test. Nothing runs behind its hosts.

    For tests that need tunables but no services; it never starts Docker.
    """
    example = Settings.load(EXAMPLE_ENV_FILE)
    return example.model_copy(update={"app": example.app.model_copy(update={"env": "test"})})


@pytest.fixture(scope="session")
def postgres_container(base_settings: Settings) -> Iterator[PostgresContainer]:
    """A throwaway Postgres whose superuser is the owner role, as in the dev stack."""
    postgres = base_settings.postgres
    container = PostgresContainer(
        POSTGRES_IMAGE,
        port=POSTGRES_PORT,
        username=postgres.owner_user,
        password=postgres.owner_password,
        dbname=postgres.database,
        driver=None,
    ).with_command(POSTGRES_COMMAND)
    with container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    """A throwaway Redis, the image compose runs."""
    with RedisContainer(REDIS_IMAGE, port=REDIS_PORT) as container:
        yield container


@pytest.fixture(scope="session")
def redis_settings(base_settings: Settings, redis_container: RedisContainer) -> RedisSettings:
    """The Redis section of `.example.env`, pointing at the container."""
    return base_settings.redis.model_copy(
        update={
            "host": redis_container.get_container_host_ip(),
            "port": published_port(redis_container, REDIS_PORT),
        }
    )


@pytest.fixture
async def redis_client(redis_settings: RedisSettings) -> AsyncIterator[RedisClient]:
    client = RedisClient(redis_settings)
    yield client
    await client.close()


@pytest.fixture(scope="session")
def settings(
    base_settings: Settings,
    postgres_container: PostgresContainer,
    redis_settings: RedisSettings,
) -> Settings:
    """`base_settings` pointing at the Postgres and Redis containers.

    ScyllaDB and SMTP point at a local port nothing listens on: every connection is
    refused at once, so the suite runs as the API does with ScyllaDB down (SPEC §9).
    """
    postgres = base_settings.postgres.model_copy(
        update={
            "host": postgres_container.get_container_host_ip(),
            "port": published_port(postgres_container, POSTGRES_PORT),
        }
    )
    nowhere = closed_port()
    scylla = base_settings.scylla.model_copy(update={"hosts": [LOOPBACK], "port": nowhere})
    mail = base_settings.mail.model_copy(update={"host": LOOPBACK, "port": nowhere})
    return base_settings.model_copy(
        update={"postgres": postgres, "redis": redis_settings, "scylla": scylla, "mail": mail}
    )


@pytest.fixture(scope="session")
def owner_engine(settings: Settings) -> AsyncEngine:
    """An engine on the owner role of the migrated database, for setup and inspection.

    Deliberately a plain fixture without a pool: `_clean_database` fetches it on demand
    from inside the running event loop, where an async fixture could not be set up, and
    with no pooled connections there is nothing to dispose of at the end.
    """
    upgrade_database(settings)
    return create_async_engine(settings.postgres.owner_url, poolclass=NullPool)


@pytest.fixture(scope="session")
async def database(settings: Settings, owner_engine: AsyncEngine) -> AsyncIterator[Database]:
    """The `Database` the application would build: the app role on the migrated schema."""
    database = Database(settings.postgres.app_url, settings.postgres)
    yield database
    await database.dispose()


@pytest.fixture(scope="session")
def uow_factory(database: Database) -> Callable[[], UnitOfWork]:
    """Returns a new `UnitOfWork` per call, like the one services are constructed with."""
    return lambda: UnitOfWork(database.session_factory)


@pytest.fixture(autouse=True)
async def _clean_database(request: pytest.FixtureRequest) -> None:
    """Give every test marked `integration` an empty database; do nothing for the rest."""
    if request.node.get_closest_marker("integration") is None:
        return
    owner_engine: AsyncEngine = request.getfixturevalue("owner_engine")
    await reset_database(owner_engine)


@pytest.fixture
def clock() -> FrozenClock:
    """The application's clock, frozen at `tests.support.clock.TEST_NOW`."""
    return FrozenClock()


@pytest.fixture
def mailer() -> MemoryMailer:
    return MemoryMailer()


@pytest.fixture
def flush_redis(redis_settings: RedisSettings) -> Callable[[], Awaitable[None]]:
    """`await flush_redis()` empties the test Redis: caches, sessions, rate-limit windows."""

    async def flush() -> None:
        raw = Redis(host=redis_settings.host, port=redis_settings.port, db=redis_settings.db)
        try:
            await raw.flushdb()
        finally:
            await raw.aclose()

    return flush


@pytest.fixture
async def container(
    settings: Settings,
    database: Database,
    clock: FrozenClock,
    mailer: MemoryMailer,
    flush_redis: Callable[[], Awaitable[None]],
) -> AsyncIterator[Container]:
    """The application's objects, wired as in production around the test doubles.

    Built per test on the session's connection pool, with an empty Redis, and started,
    so the live-quote listener runs. Stopping it leaves the shared pool open.
    """
    await flush_redis()
    container = Container.build(settings, clock=clock, mailer=mailer, database=database)
    await container.start()
    yield container
    await container.stop()


@pytest.fixture
def factory(container: Container) -> Factory:
    return Factory(container)


@pytest.fixture
def app(container: Container) -> FastAPI:
    return create_app(container)


@pytest.fixture
async def client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client on the application, in process. An error the application does not
    handle is raised into the test instead of becoming a 500."""
    origin = settings.app.public_base_url
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=origin, headers={"Origin": origin}
    ) as client:
        yield client


@pytest.fixture
def as_user(app: FastAPI) -> Iterator[ActAs]:
    """Decide who the application believes is calling, without a session or a cookie.

    Replaces the one dependency every guard rests on, so `CurrentUser`, `AdminUser` and
    `BaristaUser` still apply their own rules to the user given.
    """

    def act_as(user: AuthenticatedUser | User | None) -> None:
        identity = identity_of(user) if isinstance(user, User) else user
        app.dependency_overrides[authenticate_request] = lambda: identity

    yield act_as
    app.dependency_overrides.pop(authenticate_request, None)
