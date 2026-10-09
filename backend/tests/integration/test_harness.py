"""The test harness the other integration tests stand on (backend/CLAUDE.md, "Tests").

What `tests/conftest.py` promises is proven here once, so a test that uses it can trust
it: factories that leave the books straight, and the same fixtures giving the
application with one dependency down (SPEC §9, and the "Failures" tests of §12).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.container import Container
from app.core.config import Settings
from app.core.enums import Outcome, UserRole
from app.core.security import sha256_hex
from app.core.units import MICRO
from app.integrations.redis.client import RedisClient, RedisUnavailableError
from app.integrations.redis.rate_limiter import RateLimit
from app.integrations.scylla.client import ScyllaClient
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory
from tests.support.network import SwitchableProxy

pytestmark = pytest.mark.integration

RULE = RateLimit("harness", limit=1, window_seconds=60)
SCYLLA_RECOVERY_SECONDS = 30.0
"""The driver retries a dead node every 5 seconds; this leaves it several attempts."""


async def health(client: httpx.AsyncClient) -> dict[str, str]:
    body: dict[str, str] = (await client.get("/api/health")).json()
    return body


# --- Factories -------------------------------------------------------------------


async def test_factory_session_writes_the_row_a_sign_in_writes(
    factory: Factory, container: Container, clock: FrozenClock, settings: Settings
) -> None:
    user = await factory.user(role=UserRole.BARISTA)

    token = await factory.session(user)
    another = await factory.session(user)

    assert token != another, "every sign-in gets its own token"
    async with container.uow_factory() as uow:
        session = await uow.sessions.get(sha256_hex(token))
        assert session is not None
        assert await uow.sessions.get(token) is None, "the token itself is never stored"
    assert session.user_id == user.id
    assert session.created_at == session.last_seen_at == clock.now()
    assert session.expires_at == clock.now() + timedelta(days=settings.auth.session_ttl_days)


async def test_factory_data_keeps_the_books_straight(
    factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()

    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)
    await factory.position(alice, market, Outcome.NO, shares_micro=3 * MICRO, cost_micro=2 * MICRO)

    assert await factory.balance_micro(alice) < 3 * MICRO
    assert await factory.accounts_total_micro() == 0
    assert (market.q_yes_micro, market.q_no_micro, market.version) == (MICRO, 3 * MICRO, 2)
    # The four invariants of the circuit breaker (A.4) hold for what the factory wrote.
    async with container.uow_factory() as uow:
        assert not await uow.accounts.has_negative_user_balance()
        assert await uow.positions.sum_shares_by_outcome(market.id) == {
            Outcome.YES: MICRO,
            Outcome.NO: 3 * MICRO,
        }
        account = await uow.accounts.get_market_account(market.id)
        assert account is not None
        exposure_micro = max(market.q_yes_micro, market.q_no_micro) - account.balance_micro
        assert exposure_micro <= market.budget_micro


async def test_factory_refuses_a_position_sold_below_its_cost(factory: Factory) -> None:
    """Shares given away would let a market lose more than its budget."""
    alice = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()

    with pytest.raises(ValueError, match="loss bound"):
        await factory.position(alice, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=1)

    assert await factory.balance_micro(alice) == 5 * MICRO


# --- One dependency down -----------------------------------------------------------


@pytest.mark.redis_down
async def test_redis_down_gives_the_same_fixtures_an_application_without_redis(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    redis_client: RedisClient,
) -> None:
    assert await health(client) == {
        "status": "degraded",
        "postgres": "up",
        "redis": "down",
        "scylla": "down",
    }
    with pytest.raises(RedisUnavailableError):
        await container.rate_limiter.hit(RULE, "someone")
    assert (await container.fallback_rate_limiter.hit(RULE, "someone")).allowed

    # Postgres is the shared, clean one, and the guards work as ever.
    user = await factory.user(balance_micro=MICRO)
    assert await factory.balance_micro(user) == MICRO
    as_user(user)
    assert (await client.get("/api/admin/risk")).status_code == 403

    # The session's real Redis is untouched and still there for other tests.
    assert await redis_client.ping()


async def test_without_the_marker_redis_is_up(client: httpx.AsyncClient) -> None:
    assert (await health(client))["redis"] == "up"


@pytest.mark.postgres_down
async def test_postgres_down_gives_an_application_whose_database_is_unreachable(
    client: httpx.AsyncClient, container: Container
) -> None:
    assert not await container.database.ping()
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["details"] == {"postgres": "down", "redis": "up", "scylla": "down"}


@pytest.mark.redis_down
@pytest.mark.postgres_down
async def test_the_outage_markers_combine(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["details"] == {"postgres": "down", "redis": "down", "scylla": "down"}


async def wait_for_scylla(client: httpx.AsyncClient, wanted: str) -> None:
    """Poll `/health` until it reports ScyllaDB as `wanted`; fail after the recovery time."""
    async with asyncio.timeout(SCYLLA_RECOVERY_SECONDS):
        while True:
            if (await health(client))["scylla"] == wanted:
                return
            await asyncio.sleep(0.5)


@pytest.mark.scylla
async def test_scylla_proxy_puts_the_application_on_a_scylla_that_can_die_and_return(
    client: httpx.AsyncClient,
    app: FastAPI,
    factory: Factory,
    scylla_proxy: SwitchableProxy,
    scylla_client: ScyllaClient,
    flush_redis: Callable[[], Awaitable[None]],
) -> None:
    """The shape of §12's "with Scylla stopped trades succeed and the chart catches up
    after restart": the node goes away, Postgres work goes on, the node comes back."""
    assert app.state.container.settings.scylla.port == scylla_proxy.port
    assert await health(client) == {
        "status": "ok",
        "postgres": "up",
        "redis": "up",
        "scylla": "up",
    }

    scylla_proxy.cut()
    await wait_for_scylla(client, "down")
    user = await factory.user(balance_micro=MICRO)
    assert await factory.balance_micro(user) == MICRO

    scylla_proxy.restore()
    await wait_for_scylla(client, "up")
    # The node itself never stopped: a client that bypasses the proxy saw no outage.
    assert await scylla_client.execute("SELECT now() FROM system.local")
    await flush_redis()
