"""The leaderboard's query and answer as the API types them (SPEC §10, Appendix A.2).

A query string carries text, so the query is validated here from strings, the way a
request delivers it. Credits are decimal strings in both directions; the two rates are
JSON numbers, or null for a user without a resolved market.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.enums import DegreeLevel, LeaderboardSort, SortOrder
from app.core.units import MICRO, to_cr
from app.schemas.leaderboard import (
    MEASURES_WITH_BOUNDS,
    PAYOUT_RATIO_FILTER_MAX,
    TRADES_FILTER_MAX,
    LeaderboardEntry,
    LeaderboardQuery,
    LeaderboardResponse,
)

FILTERS = (
    "min_credits_cr",
    "max_credits_cr",
    "min_trades",
    "max_trades",
    "min_win_rate",
    "max_win_rate",
    "min_avg_payout_ratio",
    "max_avg_payout_ratio",
    "degree_level",
    "degree_programme",
)


def entry(**overrides: Any) -> LeaderboardEntry:
    values: dict[str, Any] = {
        "rank": 1,
        "username": "neo",
        "degree_level": DegreeLevel.MASTER,
        "degree_programme": "MSc Computer Science",
        "credits_cr": to_cr(612_500_000),
        "trades": 14,
        "settled_markets": 3,
        "win_rate": 0.667,
        "avg_payout_ratio": 1.25,
        "is_viewer": False,
    }
    return LeaderboardEntry(**(values | overrides))


# --- The query ---------------------------------------------------------------------


def test_the_sorts_and_orders_are_spelled_as_the_query_sends_them() -> None:
    assert [sort.value for sort in LeaderboardSort] == [
        "credits",
        "win_rate",
        "trades",
        "avg_payout_ratio",
    ]
    assert [order.value for order in SortOrder] == ["asc", "desc"]


def test_without_parameters_the_board_is_by_credits_highest_first_and_unfiltered() -> None:
    query = LeaderboardQuery()

    assert (query.sort, query.order) == (LeaderboardSort.CREDITS, SortOrder.DESC)
    assert (query.limit, query.cursor) == (50, None)
    assert all(getattr(query, name) is None for name in FILTERS)


def test_the_query_has_exactly_the_parameters_of_the_contract() -> None:
    assert set(LeaderboardQuery.model_fields) == {"sort", "order", "limit", "cursor", *FILTERS}
    assert {
        f"{bound}_{measure}" for measure in MEASURES_WITH_BOUNDS for bound in ("min", "max")
    } == set(FILTERS) - {"degree_level", "degree_programme"}


def test_every_parameter_is_read_from_the_text_of_a_query_string() -> None:
    query = LeaderboardQuery.model_validate(
        {
            "sort": "win_rate",
            "order": "asc",
            "min_credits_cr": "100",
            "max_credits_cr": "612.5",
            "min_trades": "3",
            "max_trades": "40",
            "min_win_rate": "0.5",
            "max_win_rate": "1",
            "min_avg_payout_ratio": "0.8",
            "max_avg_payout_ratio": "2.5",
            "degree_level": "MASTER",
            "degree_programme": "  MSc Computer Science ",
            "limit": "20",
            "cursor": "40",
        }
    )

    assert (query.sort, query.order) == (LeaderboardSort.WIN_RATE, SortOrder.ASC)
    assert (query.min_credits_cr, query.max_credits_cr) == (Decimal(100), Decimal("612.5"))
    assert (query.min_trades, query.max_trades) == (3, 40)
    assert (query.min_win_rate, query.max_win_rate) == (0.5, 1.0)
    assert (query.min_avg_payout_ratio, query.max_avg_payout_ratio) == (0.8, 2.5)
    assert query.degree_level is DegreeLevel.MASTER
    assert query.degree_programme == "MSc Computer Science"
    assert (query.limit, query.cursor) == (20, "40")


@pytest.mark.parametrize("sort", list(LeaderboardSort))
@pytest.mark.parametrize("order", list(SortOrder))
def test_every_measure_sorts_in_both_directions(sort: LeaderboardSort, order: SortOrder) -> None:
    query = LeaderboardQuery.model_validate({"sort": sort.value, "order": order.value})

    assert (query.sort, query.order) == (sort, order)


@pytest.mark.parametrize(
    ("parameters", "field", "rule"),
    [
        ({"sort": "score"}, "sort", "enum"),
        ({"sort": "CREDITS"}, "sort", "enum"),
        ({"order": "down"}, "order", "enum"),
        ({"direction": "desc"}, "direction", "extra_forbidden"),
        ({"total": "true"}, "total", "extra_forbidden"),
        ({"min_credits_cr": "0.1234567"}, "min_credits_cr", "decimal_places"),
        ({"min_credits_cr": "-1"}, "min_credits_cr", "decimal_negative"),
        ({"max_credits_cr": "1e3"}, "max_credits_cr", "decimal_string"),
        ({"max_credits_cr": "lots"}, "max_credits_cr", "decimal_string"),
        ({"max_credits_cr": "1000000000000"}, "max_credits_cr", "decimal_too_large"),
        ({"min_trades": "-1"}, "min_trades", "greater_than_equal"),
        ({"max_trades": "2.5"}, "max_trades", "int_parsing"),
        ({"max_trades": str(TRADES_FILTER_MAX + 1)}, "max_trades", "less_than_equal"),
        ({"max_trades": "9" * 30}, "max_trades", "less_than_equal"),
        ({"min_win_rate": "-0.1"}, "min_win_rate", "greater_than_equal"),
        ({"max_win_rate": "1.01"}, "max_win_rate", "less_than_equal"),
        ({"max_win_rate": "66.7"}, "max_win_rate", "less_than_equal"),
        ({"min_win_rate": "nan"}, "min_win_rate", "finite_number"),
        ({"min_win_rate": "half"}, "min_win_rate", "float_parsing"),
        ({"min_avg_payout_ratio": "-0.01"}, "min_avg_payout_ratio", "greater_than_equal"),
        ({"max_avg_payout_ratio": "inf"}, "max_avg_payout_ratio", "finite_number"),
        (
            {"max_avg_payout_ratio": str(PAYOUT_RATIO_FILTER_MAX + 1)},
            "max_avg_payout_ratio",
            "less_than_equal",
        ),
        ({"degree_level": "KINDERGARTEN"}, "degree_level", "enum"),
        ({"degree_programme": "   "}, "degree_programme", "string_too_short"),
        ({"degree_programme": "x" * 101}, "degree_programme", "string_too_long"),
        ({"limit": "0"}, "limit", "greater_than_equal"),
        ({"limit": "201"}, "limit", "less_than_equal"),
        ({"cursor": ""}, "cursor", "string_too_short"),
    ],
)
def test_a_parameter_that_is_not_what_the_contract_allows_is_rejected(
    parameters: dict[str, str], field: str, rule: str
) -> None:
    with pytest.raises(ValidationError) as rejected:
        LeaderboardQuery.model_validate(parameters)

    assert [(error["loc"], error["type"]) for error in rejected.value.errors()] == [
        ((field,), rule)
    ]


def test_a_json_float_is_no_bound_on_credits() -> None:
    """Credits are decimal strings in a filter too: a float cannot hold 0.1."""
    with pytest.raises(ValidationError) as rejected:
        LeaderboardQuery.model_validate({"min_credits_cr": 0.1})

    assert rejected.value.errors()[0]["type"] == "decimal_string"


@pytest.mark.parametrize(
    ("low", "high"),
    [
        ({"min_credits_cr": "100.000001"}, {"max_credits_cr": "100"}),
        ({"min_trades": "11"}, {"max_trades": "10"}),
        ({"min_win_rate": "0.51"}, {"max_win_rate": "0.5"}),
        ({"min_avg_payout_ratio": "2"}, {"max_avg_payout_ratio": "1.99"}),
    ],
    ids=MEASURES_WITH_BOUNDS,
)
def test_a_minimum_above_its_maximum_is_rejected(low: dict[str, str], high: dict[str, str]) -> None:
    (minimum,), (maximum,) = low, high

    with pytest.raises(ValidationError) as rejected:
        LeaderboardQuery.model_validate(low | high)

    (error,) = rejected.value.errors()
    assert error["type"] == "value_error"
    assert error["loc"] == (), "it is about two parameters, so it names the query as a whole"
    assert f"{minimum} must not be above {maximum}" in error["msg"]
    # Each bound alone is fine.
    assert LeaderboardQuery.model_validate(low)
    assert LeaderboardQuery.model_validate(high)


@pytest.mark.parametrize(
    "bounds",
    [
        {"min_credits_cr": "100", "max_credits_cr": "100"},
        {"min_trades": "0", "max_trades": "0"},
        {"min_win_rate": "1", "max_win_rate": "1"},
        {"min_avg_payout_ratio": "0", "max_avg_payout_ratio": "0"},
    ],
    ids=MEASURES_WITH_BOUNDS,
)
def test_a_minimum_equal_to_its_maximum_is_a_filter_for_exactly_that(
    bounds: dict[str, str],
) -> None:
    assert LeaderboardQuery.model_validate(bounds)


def test_bounds_of_different_measures_do_not_constrain_each_other() -> None:
    query = LeaderboardQuery.model_validate({"min_credits_cr": "900", "max_trades": "1"})

    assert (query.min_credits_cr, query.max_trades) == (Decimal(900), 1)


def test_the_largest_bounds_the_query_accepts_fit_a_bigint_in_micro_units() -> None:
    """The repository compares them with BIGINTs; a bound the database cannot hold
    would be an error of the server, not an answer."""
    query = LeaderboardQuery.model_validate(
        {
            "max_credits_cr": "999999999999.999999",
            "max_trades": str(TRADES_FILTER_MAX),
            "max_avg_payout_ratio": str(PAYOUT_RATIO_FILTER_MAX),
        }
    )
    bigint_max = 2**63 - 1

    assert query.max_credits_cr is not None
    assert query.max_avg_payout_ratio is not None
    assert query.max_credits_cr * MICRO <= bigint_max
    assert query.max_trades is not None
    assert query.max_trades <= bigint_max
    assert query.max_avg_payout_ratio * MICRO <= bigint_max


# --- The answer --------------------------------------------------------------------


def test_an_entry_has_exactly_the_fields_of_the_contract() -> None:
    assert list(LeaderboardEntry.model_fields) == [
        "rank",
        "username",
        "degree_level",
        "degree_programme",
        "credits_cr",
        "trades",
        "settled_markets",
        "win_rate",
        "avg_payout_ratio",
        "is_viewer",
    ]
    assert set(LeaderboardResponse.model_fields) == {"items", "next_cursor", "viewer"}


def test_credits_leave_as_a_decimal_string_and_the_rates_as_numbers() -> None:
    sent = json.loads(entry().model_dump_json())

    assert sent == {
        "rank": 1,
        "username": "neo",
        "degree_level": "MASTER",
        "degree_programme": "MSc Computer Science",
        "credits_cr": "612.5",
        "trades": 14,
        "settled_markets": 3,
        "win_rate": 0.667,
        "avg_payout_ratio": 1.25,
        "is_viewer": False,
    }
    assert isinstance(sent["credits_cr"], str)
    assert isinstance(sent["win_rate"], float)
    assert isinstance(sent["avg_payout_ratio"], float)


def test_a_user_without_a_resolved_market_has_null_rates_that_are_still_sent() -> None:
    newcomer = entry(trades=0, settled_markets=0, win_rate=None, avg_payout_ratio=None)

    sent = json.loads(newcomer.model_dump_json())

    assert sent["win_rate"] is None
    assert sent["avg_payout_ratio"] is None
    assert set(sent) == set(LeaderboardEntry.model_fields), "null is sent, never left out"


def test_a_win_rate_is_a_share_and_a_payout_ratio_is_never_negative() -> None:
    assert entry(win_rate=0.0, avg_payout_ratio=0.0)
    assert entry(win_rate=1.0, avg_payout_ratio=37.5), "a ratio has no upper end"
    for broken in ({"win_rate": 1.001}, {"win_rate": -0.1}, {"avg_payout_ratio": -0.01}):
        with pytest.raises(ValidationError):
            entry(**broken)


def test_an_entry_names_nobody_by_address_or_real_name() -> None:
    assert not {"email", "name", "id", "user_id"} & set(LeaderboardEntry.model_fields)


def test_the_answer_is_a_page_and_the_viewers_own_row() -> None:
    me = entry(rank=57, username="trinity", is_viewer=True)
    page = LeaderboardResponse(items=[entry(), entry(rank=2)], next_cursor="2", viewer=me)

    sent = json.loads(page.model_dump_json())

    assert set(sent) == {"items", "next_cursor", "viewer"}
    assert [row["rank"] for row in sent["items"]] == [1, 2]
    assert sent["next_cursor"] == "2"
    assert (sent["viewer"]["rank"], sent["viewer"]["is_viewer"]) == (57, True)


def test_the_viewer_is_null_when_the_filters_leave_them_out() -> None:
    last_page = LeaderboardResponse(items=[], next_cursor=None, viewer=None)

    assert json.loads(last_page.model_dump_json()) == {
        "items": [],
        "next_cursor": None,
        "viewer": None,
    }
