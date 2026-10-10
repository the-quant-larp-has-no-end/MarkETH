"""Fixtures shared by all backend tests.

Unit tests need nothing external: Docker starts only when a test asks for a container
fixture, directly, through another fixture, or by carrying the `integration` marker.
Containers and the connection pool are session-scoped and run on pytest-asyncio's
session event loop (see `[tool.pytest.ini_options]`), so one Postgres and one Redis
serve the run.

What a test of the API or of a service uses (mark the module `integration`):

    settings    `.example.env` with two bootstrap admins, pointing at the Postgres
                and Redis containers. ScyllaDB points at a closed port: the
                default suite proves the API works without it.
    clock       a `FrozenClock`; move it with `advance()` or `set()`
    container   the application's `Container`, built with that clock on the shared
                database, started, and stopped after the test
    factory     writes users, markets and positions directly; `factory.admin()` is
                a bootstrap admin and `factory.barista()` a user whose row has
                that role (tests/support/factories.py)
    app         the FastAPI application around that container
    client      an `httpx.AsyncClient` on it; paths start with `/api`, and the Origin
                header is already set. It sends no portal header: nobody is calling
                until `as_user` says who
    as_user     `as_user(user)` makes every request come from that user, with the
                role the application gives them: the one of their row, or ADMIN
                for a bootstrap admin. `as_user(user, UserRole.ADMIN)` names the
                role instead, for a test of a guard.
                `as_user(PortalIdentity(...))` is somebody the portal named who has no
                profile yet; `as_user(None)` is nobody again. It stands in for the
                headers and `IdentityService`.
    portal_headers
                `portal_headers(user)` is the two headers the portal would send for
                that user. Pass them to a request to go through the real
                `authenticate_request` and `IdentityService` instead of `as_user`.

Every `integration` test starts with an empty database (system accounts at zero), and
every test that uses `container` with an empty Redis. Redis runs in real time: a cached
market list outlives a `clock.advance()`, so call `await flush_redis()` after moving the
clock when a cache or a rate-limit window matters.

A test of anything that moves credits ends with
`await assert_books_balance(container.uow_factory)` (tests/support/books.py): all
accounts sum to zero, every journal entry sums to zero, every market's q is the sum of
its positions, no floor is broken and no market can lose more than its budget.

A dependency that fails (SPEC §9 and the "Failures" tests of §12). The same fixtures,
`container`, `factory`, `app`, `client` and `as_user`, then give the application as it
runs through the outage, on the same clean database:

    @pytest.mark.redis_down      Redis is a closed port: every call to it fails at once
    @pytest.mark.postgres_down   Postgres is a closed port (`factory` cannot write then)
    scylla_proxy                 asking for this fixture (in a test marked `scylla`) puts
                                 the application's ScyllaDB behind a proxy on the real
                                 node: `scylla_proxy.cut()` is the node dying,
                                 `.restore()` is it coming back with its data

For an adapter on its own: `redis_client`, `unreachable_redis`, `scylla_client`.

Two Docker Desktop quirks are absorbed here, once, for every container fixture in the
suite: where the reaper finds the Docker socket, and a race while it starts.
"""

import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
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
from testcontainers.core.container import DockerContainer, Reaper
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.api.deps import authenticate_request
from app.container import Container
from app.core.config import RedisSettings, ScyllaSettings, Settings
from app.core.enums import UserRole
from app.db.database import Database
from app.integrations.redis.client import RedisClient
from app.integrations.scylla.client import ScyllaClient
from app.main import create_app
from app.repositories.unit_of_work import UnitOfWork
from tests.support.clock import FrozenClock
from tests.support.containers import (
    LOOPBACK,
    POSTGRES_COMMAND,
    POSTGRES_IMAGE,
    POSTGRES_PORT,
    REDIS_IMAGE,
    REDIS_PORT,
    SCYLLA_COMMAND,
    SCYLLA_IMAGE,
    SCYLLA_PORT,
    SCYLLA_STARTUP_TIMEOUT_SECONDS,
    closed_port,
    published_port,
)
from tests.support.database import reset_database, upgrade_database
from tests.support.factories import (
    ActAs,
    Actor,
    Factory,
    Named,
    PortalHeaders,
    caller_of,
    portal_headers_of,
)
from tests.support.network import SwitchableProxy
from tests.support.settings import load_base_settings

if sys.platform == "darwin":
    # On macOS Docker runs in a VM. The socket the client talks to on the host (under the
    # home directory with Docker Desktop) cannot be mounted into Testcontainers' reaper;
    # inside the VM the daemon listens on the standard path. A developer's own setting wins.
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

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
    """The committed `.example.env` with APP_ENV=test and the bootstrap admins of the
    tests, of which there are two. Nothing runs behind its hosts.

    For tests that need tunables but no services; it never starts Docker.
    """
    return load_base_settings()


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


@pytest.fixture
async def unreachable_redis(base_settings: Settings) -> AsyncIterator[RedisClient]:
    """A client whose Redis is down. Needs no container."""
    client = RedisClient(_without_redis(base_settings).redis)
    yield client
    await client.close()


@pytest.fixture(scope="session")
def scylla_settings(base_settings: Settings) -> Iterator[ScyllaSettings]:
    """The ScyllaDB section of `.example.env`, pointing at a single-node container.

    The node takes about a minute to start, so only tests marked `scylla` ask for it.
    """
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
    """The ScyllaDB container behind a proxy a test can cut, to let the node "die".

    A test that asks for this fixture also gets its `container` built on the proxy.
    """
    proxy = SwitchableProxy(scylla_settings.hosts[0], scylla_settings.port)
    await proxy.start()
    yield proxy
    await proxy.close()


@pytest.fixture(scope="session")
def settings(
    base_settings: Settings,
    postgres_container: PostgresContainer,
    redis_settings: RedisSettings,
) -> Settings:
    """`base_settings` pointing at the Postgres and Redis containers.

    ScyllaDB points at a local port nothing listens on: every connection is refused
    at once, so the suite runs as the API does with ScyllaDB down (SPEC §9).
    """
    postgres = base_settings.postgres.model_copy(
        update={
            "host": postgres_container.get_container_host_ip(),
            "port": published_port(postgres_container, POSTGRES_PORT),
        }
    )
    scylla = base_settings.scylla.model_copy(update={"hosts": [LOOPBACK], "port": closed_port()})
    return base_settings.model_copy(
        update={"postgres": postgres, "redis": redis_settings, "scylla": scylla}
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
def flush_redis(redis_settings: RedisSettings) -> Callable[[], Awaitable[None]]:
    """`await flush_redis()` empties the test Redis: caches and rate-limit windows."""

    async def flush() -> None:
        raw = Redis(host=redis_settings.host, port=redis_settings.port, db=redis_settings.db)
        try:
            await raw.flushdb()
        finally:
            await raw.aclose()

    return flush


def _without_redis(settings: Settings) -> Settings:
    dead = settings.redis.model_copy(update={"host": LOOPBACK, "port": closed_port()})
    return settings.model_copy(update={"redis": dead})


def _without_postgres(settings: Settings) -> Settings:
    dead = settings.postgres.model_copy(update={"host": LOOPBACK, "port": closed_port()})
    return settings.model_copy(update={"postgres": dead})


@pytest.fixture
def container_settings(request: pytest.FixtureRequest, settings: Settings) -> Settings:
    """What `container` is built from: `settings`, minus what the test declares down.

    See "A dependency that fails" in the module docstring.
    """
    chosen = settings
    if request.node.get_closest_marker("redis_down") is not None:
        chosen = _without_redis(chosen)
    if request.node.get_closest_marker("postgres_down") is not None:
        chosen = _without_postgres(chosen)
    if "scylla_proxy" in request.fixturenames:
        proxy: SwitchableProxy = request.getfixturevalue("scylla_proxy")
        scylla_settings: ScyllaSettings = request.getfixturevalue("scylla_settings")
        behind_proxy = scylla_settings.model_copy(update={"hosts": [LOOPBACK], "port": proxy.port})
        chosen = chosen.model_copy(update={"scylla": behind_proxy})
    return chosen


@pytest.fixture
async def container(
    settings: Settings,
    container_settings: Settings,
    database: Database,
    clock: FrozenClock,
    flush_redis: Callable[[], Awaitable[None]],
) -> AsyncIterator[Container]:
    """The application's objects, wired as in production around the frozen clock.

    Built per test on the session's connection pool, with an empty Redis, and started,
    so the live-quote listener runs. Stopping it leaves the shared pool open. With
    `postgres_down` it gets a pool of its own that reaches nothing.
    """
    await flush_redis()
    shared = database if container_settings.postgres == settings.postgres else None
    container = Container.build(container_settings, clock=clock, database=shared)
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
    """Decide who the application believes is calling, without the portal's headers.

    Replaces the one dependency every guard rests on, so `Identity`, `CurrentUser`,
    `AdminUser` and `BaristaUser` still apply their own rules to the caller given.
    """

    container: Container = app.state.container

    def act_as(who: Actor, role: UserRole | None = None) -> None:
        caller = caller_of(who, role, container.email_policy)
        app.dependency_overrides[authenticate_request] = lambda: caller

    yield act_as
    app.dependency_overrides.pop(authenticate_request, None)


@pytest.fixture
def portal_headers(base_settings: Settings) -> PortalHeaders:
    """`portal_headers(user)`: the two headers in which the portal would name `user`.

    A request that carries them is not acted for: it goes through the real
    `authenticate_request` and `IdentityService`, so the role it gets is the one the
    application gives. The header names are those of the settings.
    """

    def headers(who: Named) -> dict[str, str]:
        return portal_headers_of(who, base_settings.identity)

    return headers
