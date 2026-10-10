"""What the market list route takes from a request and hands to its service (SPEC §6,
§10, Appendix A.5).

`GET /markets` takes the `status` parameter any number of times, so one request can ask
for the markets a filter chip stands for: "Open" is OPEN and HALTED, "Resolved" is
RESOLVED and VOID. A recorder stands where `MarketService` is, so each test shows the
route alone.
"""

from collections.abc import Collection
from datetime import timedelta
from uuid import uuid7

import httpx
import pytest

from app.container import Container
from app.core.enums import MarketStatus, UserRole, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.schemas.markets import MarketListResponse, MarketSummary
from tests.support.clock import TEST_NOW
from tests.support.factories import ActAs

pytestmark = pytest.mark.integration

MARKETS = "/api/markets"


def someone(role: UserRole = UserRole.USER) -> AuthenticatedUser:
    name = role.value.lower()
    return AuthenticatedUser(
        id=uuid7(),
        email=f"{name}@ethz.ch",
        name=name.capitalize(),
        username=name,
        role=role,
        status=UserStatus.ACTIVE,
    )


class RecordedMarkets:
    """Stands in for `MarketService.list_markets`: remembers who asked for which
    statuses and answers with one halted market."""

    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedUser, Collection[MarketStatus] | None]] = []

    async def list_markets(
        self, viewer: AuthenticatedUser, statuses: Collection[MarketStatus] | None
    ) -> MarketListResponse:
        self.calls.append((viewer, statuses))
        halted = MarketSummary(
            id=uuid7(),
            question="Will the Polymensa serve Älplermagronen on Friday?",
            status=MarketStatus.HALTED,
            trading_open=False,
            p_yes=0.548,
            p_no=0.452,
            volume_cr=to_cr(125_000_000),
            close_at=TEST_NOW + timedelta(days=7),
            resolve_by=TEST_NOW + timedelta(days=14),
            version=3,
            outcome=None,
        )
        return MarketListResponse(items=[halted])


@pytest.fixture
def markets(container: Container, monkeypatch: pytest.MonkeyPatch) -> RecordedMarkets:
    recorded = RecordedMarkets()
    monkeypatch.setattr(container, "markets", recorded)
    return recorded


async def test_without_a_status_the_service_is_asked_for_no_status_in_particular(
    client: httpx.AsyncClient, as_user: ActAs, markets: RecordedMarkets
) -> None:
    viewer = someone()
    as_user(viewer)

    response = await client.get(MARKETS)

    assert response.status_code == 200
    assert markets.calls == [(viewer, None)]
    (item,) = response.json()["items"]
    assert (item["status"], item["trading_open"], item["volume_cr"]) == ("HALTED", False, "125")


async def test_one_status_reaches_the_service_as_a_list_of_one(
    client: httpx.AsyncClient, as_user: ActAs, markets: RecordedMarkets
) -> None:
    viewer = someone()
    as_user(viewer)

    response = await client.get(MARKETS, params={"status": "CLOSED"})

    assert response.status_code == 200
    assert markets.calls == [(viewer, [MarketStatus.CLOSED])]


@pytest.mark.parametrize(
    "asked_for",
    [
        [MarketStatus.OPEN, MarketStatus.HALTED],
        [MarketStatus.RESOLVED, MarketStatus.VOID],
        [MarketStatus.HALTED, MarketStatus.OPEN, MarketStatus.HALTED],
        list(MarketStatus),
    ],
    ids=["the-open-chip", "the-resolved-chip", "one-named-twice", "every-status"],
)
async def test_a_repeated_status_reaches_the_service_as_every_status_named(
    client: httpx.AsyncClient,
    as_user: ActAs,
    markets: RecordedMarkets,
    asked_for: list[MarketStatus],
) -> None:
    """In the order and as often as the request names them: what a set of statuses
    means, and who may ask for PROPOSED, is the service's to say."""
    viewer = someone()
    as_user(viewer)

    response = await client.get(MARKETS, params=[("status", status.value) for status in asked_for])

    assert response.status_code == 200
    assert markets.calls == [(viewer, asked_for)]


@pytest.mark.parametrize(
    ("query", "where"),
    [
        ("status=LIVE", ["query", "status", 0]),
        ("status=open", ["query", "status", 0]),
        ("status=", ["query", "status", 0]),
        ("status=OPEN&status=PAUSED", ["query", "status", 1]),
        ("status=OPEN,HALTED", ["query", "status", 0]),
    ],
    ids=[
        "a-status-that-does-not-exist",
        "a-status-in-lower-case",
        "an-empty-status",
        "the-second-of-two",
        "two-statuses-in-one-value",
    ],
)
async def test_a_status_that_is_none_is_422_and_never_reaches_the_service(
    client: httpx.AsyncClient,
    as_user: ActAs,
    markets: RecordedMarkets,
    query: str,
    where: list[str | int],
) -> None:
    as_user(someone())

    response = await client.get(f"{MARKETS}?{query}")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    errors = response.json()["details"]["errors"]
    assert [(error["loc"], error["type"]) for error in errors] == [(where, "enum")]
    assert markets.calls == []
