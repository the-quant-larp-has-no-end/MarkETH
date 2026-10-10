"""`GET /api/health` and `GET /api/config` on the running application (A.2, SPEC §9).

The suite's ScyllaDB address is a closed port, so these also prove what SPEC §9 asks:
the API starts and answers with ScyllaDB down.
"""

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.units import MICRO, format_cr
from app.pricing import lmsr
from app.schemas.config import PublicConfigResponse
from app.schemas.health import HealthResponse
from app.schemas.markets import ProposeMarketRequest

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


async def test_health_needs_no_identity_and_no_origin(client: httpx.AsyncClient) -> None:
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


def loss_bound_cr(b_cr: int) -> str:
    """b · ln 2 rounded up, as the API writes it: computed, not typed in."""
    return format_cr(lmsr.max_loss_micro(b_cr * MICRO))


async def test_public_config_returns_the_values_of_the_example_env(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    degrees = body.pop("degrees")
    assert body == {
        "timezone": "Europe/Zurich",
        "signup_grant_cr": "500",
        "monthly_grant_cr": "200",
        "username_min_length": 3,
        "username_max_length": 20,
        "max_stake_per_market_cr": "100",
        "stake_presets_cr": ["25", "50", "100"],
        "min_trade_cr": "1",
        "min_trade_shares": "1",
        "price_display_decimals": 1,
        "quote_debounce_ms": 300,
        "live_poll_fallback_seconds": 10,
        "tiers": [
            {"tier": "TIGHT", "b_cr": "500", "max_loss_cr": loss_bound_cr(500)},
            {"tier": "DEFAULT", "b_cr": "1000", "max_loss_cr": loss_bound_cr(1000)},
            {"tier": "MEDIUM", "b_cr": "2000", "max_loss_cr": loss_bound_cr(2000)},
        ],
        "max_market_budget_cr": "5000",
        "max_market_lifetime_days": 90,
        "max_resolution_delay_days": 14,
        "max_proposals_per_day": 3,
        "question_min_length": 10,
        "question_max_length": 200,
        "short_text_max_length": 500,
        "long_text_max_length": 2000,
        "code_length": 12,
        "code_min_value_cr": "5",
        "code_max_value_cr": "500",
        "code_default_value_cr": "35",
        "max_code_issuance_per_month_cr": "7000",
        "voucher_code_length": 8,
        "voucher_expiry_days": 30,
    }
    assert degrees == [
        {"level": entry.level.value, "name": entry.name, "programmes": list(entry.programmes)}
        for entry in CATALOGUE
    ]


async def test_public_config_offers_every_degree_level_with_its_programmes(
    client: httpx.AsyncClient,
) -> None:
    """What the set-up screen builds its two selects from: six levels, each with the
    labels a profile may name, written the way ETH writes them."""
    degrees = (await client.get("/api/config")).json()["degrees"]

    assert [(level["level"], level["name"]) for level in degrees] == [
        ("BACHELOR", "Bachelor"),
        ("MASTER", "Master"),
        ("DOCTORATE", "Doctorate"),
        ("CONTINUING_EDUCATION", "Continuing education"),
        ("TEACHER_EDUCATION", "Teacher education"),
        ("OTHER", "Other"),
    ]
    assert sum(len(level["programmes"]) for level in degrees) == 192
    assert "BSc Computer Science" in degrees[0]["programmes"]
    assert degrees[5]["programmes"] == ["Exchange student", "ETH staff", "Other"]


async def test_public_config_carries_the_text_limits_a_proposal_is_held_to(
    client: httpx.AsyncClient,
) -> None:
    """The propose form shows how long each text may be. What it shows is what the
    request model enforces: the numbers are read from one and compared with the other."""
    config = (await client.get("/api/config")).json()
    fields = ProposeMarketRequest.model_json_schema()["properties"]

    def lengths(field: str) -> tuple[int, int]:
        return fields[field]["minLength"], fields[field]["maxLength"]

    assert lengths("question") == (config["question_min_length"], config["question_max_length"])
    for short in ("yes_definition", "no_definition", "resolution_source"):
        assert lengths(short) == (1, config["short_text_max_length"]), short
    assert lengths("resolution_criteria") == (1, config["long_text_max_length"])
    assert config["question_min_length"] < config["question_max_length"]
    assert config["short_text_max_length"] < config["long_text_max_length"]


async def test_public_config_needs_no_identity_and_is_the_same_for_everybody(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    names = settings.identity
    anonymous = await client.get("/api/config")
    named = await client.get("/api/config", headers={names.header_user_id: names.admin_emails[0]})

    assert anonymous.status_code == named.status_code == 200
    assert anonymous.json() == named.json()


async def test_public_config_exposes_nothing_about_people_or_infrastructure(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    text = (await client.get("/api/config")).text
    for secret in (
        *settings.identity.admin_emails,
        *settings.identity.barista_emails,
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
