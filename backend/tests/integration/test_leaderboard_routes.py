"""What the leaderboard route takes from a request and hands to its service (SPEC §10,
Appendix A.2).

The route parses the query, calls `LeaderboardService.ranking` for the caller and returns
what that answers. A recorder stands where the service is, so each test shows the route
alone: which order and which filters reach the service, on whose behalf, and how the
answer leaves.
"""

from decimal import Decimal
from uuid import uuid7

import httpx
import pytest

from app.container import Container
from app.core.degrees import CATALOGUE
from app.core.enums import DegreeLevel, LeaderboardSort, SortOrder, UserRole, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr
from app.schemas.leaderboard import LeaderboardEntry, LeaderboardQuery, LeaderboardResponse
from tests.support.factories import ActAs

pytestmark = pytest.mark.integration

LEADERBOARD = "/api/leaderboard"

BACHELOR = CATALOGUE[0]
PROGRAMME_WITH_A_DASH = next(label for label in BACHELOR.programmes if not label.isascii())
"""A label ETH writes with an en dash: it has to survive the trip through a query string."""


def someone() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uuid7(),
        email="trinity@ethz.ch",
        name="Trinity",
        username="trinity",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
    )


def entry(rank: int, username: str, **overrides: object) -> LeaderboardEntry:
    values: dict[str, object] = {
        "rank": rank,
        "username": username,
        "degree_level": DegreeLevel.MASTER,
        "degree_programme": "MSc Computer Science",
        "credits_cr": to_cr(612_500_000),
        "balance_cr": to_cr(600_000_000),
        "positions_value_cr": to_cr(12_500_000),
        "trades": 14,
        "settled_markets": 3,
        "win_rate": 0.667,
        "avg_payout_ratio": 1.25,
        "is_viewer": False,
    }
    return LeaderboardEntry.model_validate(values | overrides)


class RecordedLeaderboard:
    """Stands in for `LeaderboardService`: remembers each call and answers with a page
    of two rows and the viewer's row from further down."""

    def __init__(self) -> None:
        self.calls: list[tuple[AuthenticatedUser, LeaderboardQuery]] = []

    async def ranking(
        self, viewer: AuthenticatedUser, query: LeaderboardQuery
    ) -> LeaderboardResponse:
        self.calls.append((viewer, query))
        newcomer = entry(
            2,
            "morpheus",
            degree_level=DegreeLevel.OTHER,
            degree_programme="ETH staff",
            credits_cr=to_cr(500_000_000),
            balance_cr=to_cr(500_000_000),
            positions_value_cr=to_cr(0),
            trades=0,
            settled_markets=0,
            win_rate=None,
            avg_payout_ratio=None,
        )
        return LeaderboardResponse(
            items=[entry(1, "neo"), newcomer],
            next_cursor="2",
            viewer=entry(57, viewer.username, is_viewer=True),
        )


@pytest.fixture
def leaderboard(container: Container, monkeypatch: pytest.MonkeyPatch) -> RecordedLeaderboard:
    recorded = RecordedLeaderboard()
    monkeypatch.setattr(container, "leaderboard", recorded)
    return recorded


async def test_without_a_query_the_service_ranks_by_credits_for_the_caller(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    viewer = someone()
    as_user(viewer)

    response = await client.get(LEADERBOARD)

    assert response.status_code == 200
    assert leaderboard.calls == [(viewer, LeaderboardQuery())]
    (_, query) = leaderboard.calls[0]
    assert (query.sort, query.order, query.limit) == (
        LeaderboardSort.CREDITS,
        SortOrder.DESC,
        50,
    )


async def test_the_answer_leaves_with_credits_as_strings_rates_as_numbers_and_nulls_kept(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    as_user(someone())

    body = (await client.get(LEADERBOARD)).json()

    assert body == {
        "items": [
            {
                "rank": 1,
                "username": "neo",
                "degree_level": "MASTER",
                "degree_programme": "MSc Computer Science",
                "credits_cr": "612.5",
                "balance_cr": "600",
                "positions_value_cr": "12.5",
                "trades": 14,
                "settled_markets": 3,
                "win_rate": 0.667,
                "avg_payout_ratio": 1.25,
                "is_viewer": False,
            },
            {
                "rank": 2,
                "username": "morpheus",
                "degree_level": "OTHER",
                "degree_programme": "ETH staff",
                "credits_cr": "500",
                "balance_cr": "500",
                "positions_value_cr": "0",
                "trades": 0,
                "settled_markets": 0,
                "win_rate": None,
                "avg_payout_ratio": None,
                "is_viewer": False,
            },
        ],
        "next_cursor": "2",
        "viewer": {
            "rank": 57,
            "username": "trinity",
            "degree_level": "MASTER",
            "degree_programme": "MSc Computer Science",
            "credits_cr": "612.5",
            "balance_cr": "600",
            "positions_value_cr": "12.5",
            "trades": 14,
            "settled_markets": 3,
            "win_rate": 0.667,
            "avg_payout_ratio": 1.25,
            "is_viewer": True,
        },
    }
    assert "total" not in body
    assert "email" not in str(body)


async def test_the_order_and_every_filter_reach_the_service_as_the_query_names_them(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    viewer = someone()
    as_user(viewer)

    response = await client.get(
        LEADERBOARD,
        params={
            "sort": "avg_payout_ratio",
            "order": "asc",
            "min_credits_cr": "100",
            "max_credits_cr": "612.5",
            "min_trades": "3",
            "max_trades": "40",
            "min_win_rate": "0.5",
            "max_win_rate": "1",
            "min_avg_payout_ratio": "0.8",
            "max_avg_payout_ratio": "2.5",
            "degree_level": BACHELOR.level.value,
            "degree_programme": PROGRAMME_WITH_A_DASH,
            "limit": "20",
            "cursor": "40",
        },
    )

    assert response.status_code == 200
    assert leaderboard.calls == [
        (
            viewer,
            LeaderboardQuery(
                sort=LeaderboardSort.AVG_PAYOUT_RATIO,
                order=SortOrder.ASC,
                min_credits_cr=Decimal(100),
                max_credits_cr=Decimal("612.5"),
                min_trades=3,
                max_trades=40,
                min_win_rate=0.5,
                max_win_rate=1.0,
                min_avg_payout_ratio=0.8,
                max_avg_payout_ratio=2.5,
                degree_level=DegreeLevel.BACHELOR,
                degree_programme=PROGRAMME_WITH_A_DASH,
                limit=20,
                cursor="40",
            ),
        )
    ]


@pytest.mark.parametrize("sort", [sort.value for sort in LeaderboardSort])
@pytest.mark.parametrize("order", [order.value for order in SortOrder])
async def test_each_measure_can_be_asked_for_in_both_directions(
    client: httpx.AsyncClient,
    as_user: ActAs,
    leaderboard: RecordedLeaderboard,
    sort: str,
    order: str,
) -> None:
    as_user(someone())

    response = await client.get(LEADERBOARD, params={"sort": sort, "order": order})

    assert response.status_code == 200
    (_, query) = leaderboard.calls[0]
    assert (query.sort.value, query.order.value) == (sort, order)


@pytest.mark.parametrize(
    ("parameters", "where", "rule"),
    [
        ({"sort": "score"}, ["query", "sort"], "enum"),
        ({"order": "down"}, ["query", "order"], "enum"),
        ({"total": "true"}, ["query", "total"], "extra_forbidden"),
        ({"min_credits_cr": "0.1234567"}, ["query", "min_credits_cr"], "decimal_places"),
        ({"max_credits_cr": "-5"}, ["query", "max_credits_cr"], "decimal_negative"),
        ({"min_trades": "-1"}, ["query", "min_trades"], "greater_than_equal"),
        ({"max_win_rate": "66.7"}, ["query", "max_win_rate"], "less_than_equal"),
        ({"min_avg_payout_ratio": "nan"}, ["query", "min_avg_payout_ratio"], "finite_number"),
        ({"degree_level": "KINDERGARTEN"}, ["query", "degree_level"], "enum"),
        ({"degree_programme": "x" * 101}, ["query", "degree_programme"], "string_too_long"),
        ({"limit": "201"}, ["query", "limit"], "less_than_equal"),
    ],
    ids=[
        "a-measure-that-does-not-exist",
        "an-order-that-does-not-exist",
        "a-parameter-nobody-defined",
        "a-seventh-decimal-place",
        "negative-credits",
        "a-negative-number-of-trades",
        "a-win-rate-in-percent",
        "a-ratio-that-is-no-number",
        "a-level-that-does-not-exist",
        "a-programme-of-101-characters",
        "a-page-of-201",
    ],
)
async def test_a_query_the_contract_does_not_allow_is_422_and_never_reaches_the_service(
    client: httpx.AsyncClient,
    as_user: ActAs,
    leaderboard: RecordedLeaderboard,
    parameters: dict[str, str],
    where: list[str],
    rule: str,
) -> None:
    as_user(someone())

    response = await client.get(LEADERBOARD, params=parameters)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    errors = response.json()["details"]["errors"]
    assert [(error["loc"], error["type"]) for error in errors] == [(where, rule)]
    assert leaderboard.calls == []


async def test_a_minimum_above_its_maximum_is_the_services_to_refuse(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    """Each of the two bounds is one the contract allows, so the route hands both on.
    The service answers the pair with 422 and the lower bound as `details.field`
    (tests/unit/test_leaderboard_schema.py, tests/integration/test_leaderboard.py)."""
    as_user(someone())

    response = await client.get(
        LEADERBOARD, params={"min_credits_cr": "500", "max_credits_cr": "499.999999"}
    )

    assert response.status_code == 200
    ((_, query),) = leaderboard.calls
    assert (query.min_credits_cr, query.max_credits_cr) == (Decimal(500), Decimal("499.999999"))


async def test_a_refused_parameter_is_told_in_a_sentence_for_the_person_who_typed_it(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    """The filter sheet shows `msg` under the field `loc` names. A win rate is typed in
    percent and sent as a fraction, so 150 % arrives as 1.5 and is answered in percent."""
    as_user(someone())

    answered = {
        field: (await client.get(LEADERBOARD, params={field: sent})).json()["details"]["errors"]
        for field, sent in {
            "max_win_rate": "1.5",
            "min_trades": "2.5",
            "min_avg_payout_ratio": "plenty",
            "min_credits_cr": "0.1234567",
        }.items()
    }

    assert {field: errors[0]["msg"] for field, errors in answered.items()} == {
        "max_win_rate": "Enter a percentage between 0 and 100.",
        "min_trades": "Enter a whole number.",
        "min_avg_payout_ratio": "Enter a number, for example 1.25.",
        "min_credits_cr": "Use at most 6 decimal places.",
    }
    assert {field: errors[0]["loc"] for field, errors in answered.items()} == {
        field: ["query", field] for field in answered
    }
    assert leaderboard.calls == []


async def test_the_leaderboard_is_read_only(
    client: httpx.AsyncClient, as_user: ActAs, leaderboard: RecordedLeaderboard
) -> None:
    as_user(someone())

    for method in ("POST", "PUT", "DELETE"):
        assert (await client.request(method, LEADERBOARD)).status_code == 405, method
    assert leaderboard.calls == []
