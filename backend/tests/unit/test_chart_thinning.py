"""`thinned`: how a chart with more points than an answer may hold loses some (SPEC §9).

The rule is about positions in a series and nothing else, so the series of these tests
are plain numbers: a point is its own position.
"""

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.config import Settings
from app.services.history import thinned


def test_a_series_within_the_maximum_is_returned_whole() -> None:
    series = list(range(7))

    assert thinned(series, 7) == series
    assert thinned(series, 8) == series
    assert thinned(series, 500) == series
    assert thinned([], 2) == []
    assert thinned(["only"], 2) == ["only"]


def test_the_result_is_a_list_of_its_own() -> None:
    series = [1, 2, 3]

    assert thinned(series, 3) is not series
    assert thinned(tuple(series), 3) == series


def test_one_point_more_than_the_maximum_loses_one_point_near_the_end() -> None:
    assert thinned(list(range(8)), 7) == [0, 1, 2, 3, 4, 5, 7]


def test_the_smallest_maximum_keeps_the_first_and_the_last() -> None:
    assert thinned(list(range(43_200)), 2) == [0, 43_199]
    assert thinned(["opening", "between", "latest"], 2) == ["opening", "latest"]


def test_what_stays_is_spread_evenly_over_the_series() -> None:
    kept = thinned(list(range(5_001)), 500)

    assert len(kept) == 500
    assert (kept[0], kept[-1]) == (0, 5_000)
    assert {after - before for before, after in itertools.pairwise(kept)} == {10, 11}
    assert thinned(list(range(101)), 11) == [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_the_maximum_of_the_settings_always_has_room_for_the_first_and_the_last(
    base_settings: Settings,
) -> None:
    """`thinned` divides by one less than the maximum: startup refuses a maximum
    below 2 (tests/unit/test_settings.py), and the one of `.example.env` is far above."""
    most = base_settings.scylla.history_max_points
    series = list(range(base_settings.scylla.history_read_limit + 1))

    assert most >= 2
    assert len(thinned(series, most)) == most


@given(
    length=st.integers(min_value=0, max_value=12_000),
    most=st.integers(min_value=2, max_value=600),
)
def test_whatever_the_series_and_the_maximum(length: int, most: int) -> None:
    """At most the maximum of points; the first and the last among them; no point
    twice and none out of order; whole when it fits; and evenly spread: two steps
    between neighbours never differ by more than one position."""
    series = list(range(length))

    kept = thinned(series, most)

    assert len(kept) == min(length, most)
    if series:
        assert (kept[0], kept[-1]) == (series[0], series[-1])
    assert all(before < after for before, after in itertools.pairwise(kept))
    if length <= most:
        assert kept == series
    steps = [after - before for before, after in itertools.pairwise(kept)]
    if steps:
        assert max(steps) - min(steps) <= 1


@pytest.mark.parametrize("length", [3, 10, 501, 5_001, 43_200])
def test_thinning_what_was_thinned_changes_nothing(length: int) -> None:
    once = thinned(list(range(length)), 500)

    assert thinned(once, 500) == once
