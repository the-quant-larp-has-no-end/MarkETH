"""The places of a broadcast's viewers (`app.services.broadcast_audience`, SPEC §6).

At most `BROADCAST_MAX_VIEWERS` people hold a place in one broadcast. A place is held
from an admission until `seat_seconds` pass without another one. Nothing here needs a
data store: the places are a dictionary of the process.
"""

from datetime import timedelta
from typing import Final
from uuid import UUID

import pytest

from app.core.config import Settings
from app.services.broadcast_audience import BroadcastAudience
from tests.support.clock import FrozenClock

PLACES: Final[int] = 3
"""`BROADCAST_MAX_VIEWERS` of these tests."""

BROADCAST: Final[UUID] = UUID(int=1)
OTHER_BROADCAST: Final[UUID] = UUID(int=2)

SECOND: Final[timedelta] = timedelta(seconds=1)


def viewer(number: int) -> UUID:
    return UUID(int=1000 + number)


@pytest.fixture
def settings(base_settings: Settings) -> Settings:
    section = base_settings.broadcast.model_copy(update={"max_viewers": PLACES})
    return base_settings.model_copy(update={"broadcast": section})


@pytest.fixture
def seat(settings: Settings) -> timedelta:
    return timedelta(seconds=settings.broadcast.seat_seconds)


@pytest.fixture
def audience(settings: Settings, clock: FrozenClock) -> BroadcastAudience:
    return BroadcastAudience(settings, clock)


def test_a_place_is_held_as_long_as_the_proxy_remembers_a_yes_and_a_playlist_plays(
    base_settings: Settings,
) -> None:
    live_video = base_settings.broadcast

    assert live_video.seat_seconds == (
        live_video.access_cache_seconds
        + live_video.hls_segment_seconds * live_video.hls_playlist_segments
    )


def test_the_person_at_the_limit_is_admitted_and_the_next_one_is_not(
    audience: BroadcastAudience,
) -> None:
    admitted = [audience.admit(BROADCAST, viewer(number)) for number in range(PLACES + 2)]

    assert admitted == [True] * PLACES + [False, False]
    assert audience.count(BROADCAST) == PLACES
    assert [audience.holds(BROADCAST, viewer(number)) for number in range(PLACES + 2)] == (
        [True] * PLACES + [False, False]
    )


def test_somebody_who_holds_a_place_is_admitted_again_while_the_broadcast_is_full(
    audience: BroadcastAudience,
) -> None:
    for number in range(PLACES):
        audience.admit(BROADCAST, viewer(number))

    assert all(audience.admit(BROADCAST, viewer(number)) for number in range(PLACES))
    assert audience.admit(BROADCAST, viewer(PLACES)) is False
    assert audience.count(BROADCAST) == PLACES


def test_a_place_is_given_back_once_nobody_asked_for_it_in_seat_seconds(
    audience: BroadcastAudience, clock: FrozenClock, seat: timedelta
) -> None:
    for number in range(PLACES):
        audience.admit(BROADCAST, viewer(number))

    clock.advance(seat - SECOND)
    assert audience.admit(BROADCAST, viewer(PLACES)) is False
    assert audience.count(BROADCAST) == PLACES

    clock.advance(SECOND)
    assert audience.count(BROADCAST) == 0
    assert audience.admit(BROADCAST, viewer(PLACES)) is True
    assert [audience.holds(BROADCAST, viewer(number)) for number in range(PLACES + 1)] == (
        [False] * PLACES + [True]
    )


def test_every_admission_holds_the_place_for_seat_seconds_more(
    audience: BroadcastAudience, clock: FrozenClock, seat: timedelta
) -> None:
    """A viewer who keeps watching is asked about again and again, and keeps the
    place; one who stopped loses theirs while the other still has it."""
    audience.admit(BROADCAST, viewer(0))
    audience.admit(BROADCAST, viewer(1))

    for _ in range(5):
        clock.advance(seat - SECOND)
        assert audience.admit(BROADCAST, viewer(0)) is True

    assert audience.holds(BROADCAST, viewer(0)) is True
    assert audience.holds(BROADCAST, viewer(1)) is False
    assert audience.count(BROADCAST) == 1


def test_asking_whether_there_is_a_place_takes_none(
    audience: BroadcastAudience, clock: FrozenClock, seat: timedelta
) -> None:
    for number in range(PLACES - 1):
        audience.admit(BROADCAST, viewer(number))

    for _ in range(3):
        assert audience.has_place_for(BROADCAST, viewer(7)) is True
        assert audience.holds(BROADCAST, viewer(7)) is False
    assert audience.count(BROADCAST) == PLACES - 1

    audience.admit(BROADCAST, viewer(8))
    assert audience.has_place_for(BROADCAST, viewer(7)) is False, "every place is held"
    assert audience.has_place_for(BROADCAST, viewer(8)) is True, "by this viewer among others"
    assert audience.has_place_for(BROADCAST, viewer(0)) is True

    clock.advance(seat)
    assert audience.has_place_for(BROADCAST, viewer(7)) is True


def test_forgetting_a_broadcast_gives_every_place_back(audience: BroadcastAudience) -> None:
    for number in range(PLACES):
        audience.admit(BROADCAST, viewer(number))

    audience.forget(BROADCAST)
    audience.forget(BROADCAST)
    audience.forget(OTHER_BROADCAST)

    assert audience.count(BROADCAST) == 0
    assert audience.holds(BROADCAST, viewer(0)) is False
    assert audience.admit(BROADCAST, viewer(PLACES)) is True


def test_two_broadcasts_do_not_share_their_places(audience: BroadcastAudience) -> None:
    for number in range(PLACES):
        audience.admit(BROADCAST, viewer(number))

    assert audience.admit(OTHER_BROADCAST, viewer(0)) is True
    assert audience.admit(OTHER_BROADCAST, viewer(PLACES)) is True
    assert audience.admit(BROADCAST, viewer(PLACES)) is False
    assert (audience.count(BROADCAST), audience.count(OTHER_BROADCAST)) == (PLACES, 2)

    audience.forget(BROADCAST)
    assert (audience.count(BROADCAST), audience.count(OTHER_BROADCAST)) == (0, 2)


def test_the_places_of_many_broadcasts_are_each_given_back_by_the_clock(
    audience: BroadcastAudience, clock: FrozenClock, seat: timedelta
) -> None:
    for number in range(50):
        audience.admit(UUID(int=number), viewer(number))
    assert [audience.count(UUID(int=number)) for number in range(50)] == [1] * 50

    clock.advance(seat)

    assert [audience.count(UUID(int=number)) for number in range(50)] == [0] * 50
