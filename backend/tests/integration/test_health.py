"""`GET /api/health` and `GET /api/config` on the running application (A.2, SPEC §9).

The suite's ScyllaDB address is a closed port and nothing listens on the socket of the
media box, so these also prove what SPEC §9 asks: the API starts and answers with
ScyllaDB and the media box down.
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import ValidationError

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.text import CHAT_BACKLOG_MAX
from app.core.units import MICRO, format_cr
from app.integrations.scylla.client import ScyllaUnavailableError
from app.pricing import lmsr
from app.schemas.admin.code_batches import CreateCodeBatchRequest
from app.schemas.broadcasts import CAPTURE_MIME_TYPES, BroadcastConfig
from app.schemas.chat import PostChatMessageRequest
from app.schemas.config import PublicConfigResponse
from app.schemas.health import HealthResponse
from app.schemas.markets import ProposeMarketRequest

pytestmark = pytest.mark.integration


def answering(up: bool) -> Callable[[], Awaitable[bool]]:
    """What stands where a client's `ping` is: a probe that says `up`."""

    async def ping() -> bool:
        return up

    return ping


def scylla_answering(up: bool) -> Callable[..., Awaitable[list[dict[str, object]]]]:
    """What stands where `ScyllaClient.execute` is: a statement that runs, or a node
    that cannot be reached."""

    async def execute(cql: str, *arguments: object, **named: object) -> list[dict[str, object]]:
        if not up:
            raise ScyllaUnavailableError("no node answers in this test")
        return []

    return execute


def word(up: bool) -> str:
    return "up" if up else "down"


async def test_health_reports_postgres_and_redis_up_and_scylla_and_the_media_box_down_with_200(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "postgres": "up",
        "redis": "up",
        "scylla": "down",
        "media": "down",
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
        "details": {"postgres": "down", "redis": "up", "scylla": "down", "media": "down"},
    }


@pytest.mark.parametrize("media", [True, False], ids=["box-up", "box-down"])
@pytest.mark.parametrize("scylla", [True, False], ids=["scylla-up", "scylla-down"])
@pytest.mark.parametrize("redis", [True, False], ids=["redis-up", "redis-down"])
async def test_health_is_ok_exactly_when_redis_scylla_and_the_media_box_all_answer(
    client: httpx.AsyncClient,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
    redis: bool,
    scylla: bool,
    media: bool,
) -> None:
    """With Postgres up the answer is 200 whichever of the three others is down: the
    application is healthy without the media box, as it is without Redis and ScyllaDB.
    Each is reported as it answers, and one that does not makes the status `degraded`."""
    monkeypatch.setattr(container.redis, "ping", answering(redis))
    monkeypatch.setattr(container.scylla, "execute", scylla_answering(scylla))
    monkeypatch.setattr(container.media, "ping", answering(media))

    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok" if redis and scylla and media else "degraded",
        "postgres": "up",
        "redis": word(redis),
        "scylla": word(scylla),
        "media": word(media),
    }
    HealthResponse.model_validate(response.json())


@pytest.mark.parametrize("media", [True, False], ids=["box-up", "box-down"])
async def test_the_media_box_never_decides_between_200_and_503(
    client: httpx.AsyncClient,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
    media: bool,
) -> None:
    """Only Postgres decides the verdict. A box that answers does not make an
    application without its database healthy, and the 503 says how the box is."""
    monkeypatch.setattr(container.database, "ping", answering(False))
    monkeypatch.setattr(container.media, "ping", answering(media))

    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["details"] == {
        "postgres": "down",
        "redis": "up",
        "scylla": "down",
        "media": word(media),
    }


async def test_health_asks_the_media_box_while_it_asks_the_stores(
    client: httpx.AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probes run at once, so the answer takes as long as the slowest of them and
    not as long as all of them together: a box that takes its whole timeout adds
    nothing to a ScyllaDB that takes its own. Here the probe of ScyllaDB and the probe
    of the box each wait until the other has begun. Asked one after the other, the
    first would wait for ever."""
    scylla_asked, box_asked = asyncio.Event(), asyncio.Event()

    async def execute(cql: str, *arguments: object, **named: object) -> list[dict[str, object]]:
        scylla_asked.set()
        await box_asked.wait()
        return []

    async def ping() -> bool:
        box_asked.set()
        await scylla_asked.wait()
        return True

    monkeypatch.setattr(container.scylla, "execute", execute)
    monkeypatch.setattr(container.media, "ping", ping)

    async with asyncio.timeout(5):
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert (response.json()["scylla"], response.json()["media"]) == ("up", "up")


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
        "share_presets": ["10", "50", "100"],
        "share_step": "1",
        "stake_presets_cr": ["25", "50", "100"],
        "stake_step_cr": "1",
        "min_trade_cr": "1",
        "min_trade_shares": "1",
        "price_display_decimals": 1,
        "quote_debounce_ms": 300,
        "live_poll_fallback_seconds": 10,
        "live_heartbeat_seconds": 15,
        "live_announce_spread_ms": 1000,
        "live_viewer_refresh_seconds": 5,
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
        "chat_message_max_length": 200,
        "chat_backlog_size": 100,
        "broadcast": {
            "chunk_ms": 1000,
            "chunk_max_bytes": 1_048_576,
            # Fifteen seconds of what a studio records: 1,800 + 64 kbit/s.
            "upload_backlog_bytes": 15 * (1800 + 64) * 125,
            "capture_height": 720,
            "capture_fps": 24,
            "capture_max_width": 1920,
            "capture_max_height": 1080,
            "capture_video_bits_per_second": 1_800_000,
            "capture_audio_bits_per_second": 64_000,
            "capture_mime_types": list(CAPTURE_MIME_TYPES),
            "stall_seconds": 120,
            "max_minutes": 120,
            "upload_timeout_ms": 10_000,
            "upload_retry_ms": 1000,
            "restart_window_ms": 30_000,
            "wait_notice_ms": 4000,
            "player_retry_ms": 2000,
        },
        "code_length": 12,
        "code_min_value_cr": "5",
        "code_max_value_cr": "500",
        "code_default_value_cr": "35",
        "max_code_issuance_per_month_cr": "7000",
        "code_batch_max_count": 10000,
        "code_label_max_length": 100,
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


async def test_public_config_carries_the_bounds_a_batch_of_codes_is_held_to(
    client: httpx.AsyncClient,
) -> None:
    """The form for a batch shows how many codes it may have and how long its label may
    be. What it shows is what the request model enforces: a batch at each bound passes
    and one beyond it does not."""
    config = (await client.get("/api/config")).json()
    most, longest = config["code_batch_max_count"], config["code_label_max_length"]
    fields = CreateCodeBatchRequest.model_json_schema()["properties"]
    batch = {"label": "x", "count": 1, "expires_at": "2026-12-01T00:00:00Z", "one_per_user": False}

    assert (fields["count"]["minimum"], fields["count"]["maximum"]) == (1, most)
    assert (fields["label"]["minLength"], fields["label"]["maxLength"]) == (1, longest)
    assert CreateCodeBatchRequest.model_validate(batch | {"count": most, "label": "x" * longest})
    for beyond in ({"count": most + 1}, {"label": "x" * (longest + 1)}):
        with pytest.raises(ValidationError):
            CreateCodeBatchRequest.model_validate(batch | beyond)


async def test_public_config_carries_what_a_chat_message_and_a_read_of_a_chat_are_held_to(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """The length a person may use is a setting inside what the request model and the
    table take, and a read returns no more than a read may."""
    config = (await client.get("/api/config")).json()
    written = PostChatMessageRequest.model_json_schema()["properties"]["text"]

    assert (config["chat_message_max_length"], config["chat_backlog_size"]) == (
        settings.chat.message_max_length,
        settings.chat.backlog_size,
    )
    assert written["minLength"] <= config["chat_message_max_length"] <= written["maxLength"]
    assert 1 <= config["chat_backlog_size"] <= CHAT_BACKLOG_MAX


async def test_public_config_tells_a_studio_what_the_settings_of_live_video_make_of_it(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """A recorder is told its rates in bits per second and the camera the picture the
    viewers get. A piece of the recording fits one upload twice over, and what a studio
    queues is a whole number of such pieces and more than one."""
    told = (await client.get("/api/config")).json()["broadcast"]
    live_video = settings.broadcast

    assert told["capture_video_bits_per_second"] == live_video.capture_video_kbps * 1000
    assert told["capture_audio_bits_per_second"] == live_video.audio_kbps * 1000
    assert told["capture_height"] == live_video.video_height
    assert told["capture_fps"] == live_video.video_fps
    assert (told["capture_max_width"], told["capture_max_height"]) == (
        live_video.input_max_width,
        live_video.input_max_height,
    )
    assert told["upload_backlog_bytes"] == live_video.upload_backlog_bytes
    bytes_per_piece = (
        (told["capture_video_bits_per_second"] + told["capture_audio_bits_per_second"])
        * told["chunk_ms"]
        // 8000
    )
    assert 2 * bytes_per_piece <= told["chunk_max_bytes"]
    assert told["upload_backlog_bytes"] > told["chunk_max_bytes"]
    assert told["stall_seconds"] * 1000 > told["upload_timeout_ms"] + told["upload_retry_ms"]
    # Nothing of where the media box is, or of how many may watch.
    assert set(told) == set(BroadcastConfig.model_fields)
    assert not [field for field in told if "socket" in field or "viewers" in field]


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
        settings.postgres.app_password,
        settings.postgres.owner_password,
        settings.postgres.host,
        settings.broadcast.media_socket,
    ):
        assert secret not in text


async def test_public_config_is_what_the_service_builds_from_settings(
    client: httpx.AsyncClient, container: Container
) -> None:
    served = PublicConfigResponse.model_validate((await client.get("/api/config")).json())
    assert served == container.public_config.get()
