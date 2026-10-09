"""`GET /api/health` and `GET /api/config` on the running application (A.2, SPEC §9).

The suite's ScyllaDB address is a closed port, so these also prove what SPEC §9 asks:
the API starts and answers with ScyllaDB down.
"""

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.schemas.config import PublicConfigResponse
from app.schemas.health import HealthResponse

pytestmark = pytest.mark.integration


async def test_health_reports_postgres_and_redis_up_and_scylla_down_with_200(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "postgres": "up",
        "redis": "up",
        "scylla": "down",
    }
    HealthResponse.model_validate(response.json())


async def test_health_needs_no_session_and_no_origin(client: httpx.AsyncClient) -> None:
    del client.headers["Origin"]
    response = await client.get("/api/health")
    assert response.status_code == 200


async def test_health_stays_answerable_while_scylla_is_down(client: httpx.AsyncClient) -> None:
    """After the first failed connection attempt the probe fails fast, so a healthcheck
    every few seconds never waits on ScyllaDB."""
    first = await client.get("/api/health")
    second = await client.get("/api/health")
    assert first.json()["scylla"] == second.json()["scylla"] == "down"
    assert second.elapsed.total_seconds() < 1.0


async def test_health_is_503_with_the_error_shape_when_postgres_is_down(
    client: httpx.AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(container.database, "ping", unreachable)
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "code": "SERVICE_UNAVAILABLE",
        "message": "The database is unreachable.",
        "details": {"postgres": "down", "redis": "up", "scylla": "down"},
    }


async def test_public_config_returns_the_values_of_the_example_env(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {
        "timezone": "Europe/Zurich",
        "allowed_email_domains": ["student.ethz.ch", "ethz.ch"],
        "login_link_ttl_minutes": 15,
        "signup_grant_cr": "5",
        "monthly_grant_cr": "1",
        "max_stake_per_market_cr": "1",
        "stake_presets_cr": ["0.25", "0.5", "1"],
        "min_trade_cr": "0.01",
        "min_trade_shares": "0.01",
        "price_display_decimals": 1,
        "quote_debounce_ms": 300,
        "live_poll_fallback_seconds": 10,
        "tiers": [
            {"tier": "TIGHT", "b_cr": "5", "max_loss_cr": "3.465736"},
            {"tier": "DEFAULT", "b_cr": "10", "max_loss_cr": "6.931472"},
            {"tier": "MEDIUM", "b_cr": "20", "max_loss_cr": "13.862944"},
        ],
        "max_market_budget_cr": "1000",
        "max_market_lifetime_days": 90,
        "max_resolution_delay_days": 14,
        "max_proposals_per_day": 3,
        "voucher_code_length": 12,
        "voucher_code_min_value_cr": "1",
        "voucher_code_max_value_cr": "5",
        "voucher_code_default_value_cr": "1",
        "max_code_issuance_per_month_cr": "200",
        "coffee_ticket_value_cr": "1",
        "coffee_ticket_code_length": 8,
        "coffee_ticket_expiry_days": 30,
    }


async def test_public_config_exposes_nothing_about_people_or_infrastructure(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    text = (await client.get("/api/config")).text
    for secret in (
        *settings.auth.admin_emails,
        *settings.auth.barista_emails,
        settings.postgres.app_password,
        settings.postgres.owner_password,
        settings.postgres.host,
    ):
        assert secret not in text


async def test_public_config_is_what_the_service_builds_from_settings(
    client: httpx.AsyncClient, container: Container
) -> None:
    served = PublicConfigResponse.model_validate((await client.get("/api/config")).json())
    assert served == container.public_config.get()
