"""What a broadcast is at an instant (`app.services.broadcast_view`, SPEC §6).

`BroadcastView` reads rows and computes: when a LIVE broadcast is over by itself, what
status is reported, which market can have a live stream, what a caller is told about a
broadcast, and what a caller may do about it. Nothing here needs a data store: the rows
are built in memory.
"""

from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.enums import (
    STORED_MARKET_STATUSES,
    BroadcastEndReason,
    BroadcastStatus,
    DegreeLevel,
    MarketStatus,
    UserRole,
    UserStatus,
)
from app.core.identity import AuthenticatedUser
from app.models import Market, User
from app.models.broadcast import Broadcast
from app.schemas.broadcasts import BroadcastPerson, BroadcastViewer
from app.services.broadcast_view import BroadcastView
from app.services.market_view import MarketView
from tests.support.clock import TEST_NOW

NOW: Final[datetime] = TEST_NOW
SECOND: Final[timedelta] = timedelta(seconds=1)
PROGRAMME: Final[str] = "BSc Computer Science"


def caller(number: int, role: UserRole = UserRole.USER) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=UUID(int=number),
        email=f"caller{number}@ethz.ch",
        name=f"Caller {number}",
        username=f"caller{number}",
        degree_programme=PROGRAMME,
        role=role,
        status=UserStatus.ACTIVE,
    )


ADMIN: Final[AuthenticatedUser] = caller(1, UserRole.ADMIN)
OTHER_ADMIN: Final[AuthenticatedUser] = caller(2, UserRole.ADMIN)
PROPOSER: Final[AuthenticatedUser] = caller(100)
MEMBER: Final[AuthenticatedUser] = caller(5)


def user_row(of: AuthenticatedUser) -> User:
    return User(
        id=of.id,
        email=of.email,
        name=of.name,
        username=of.username,
        degree_level=DegreeLevel.BACHELOR,
        degree_programme=of.degree_programme,
        role=of.role,
        status=of.status,
        policy_accepted_at=NOW,
        created_at=NOW,
    )


def market(
    status: MarketStatus = MarketStatus.OPEN, *, closes_in: timedelta = timedelta(days=1)
) -> Market:
    return Market(
        id=UUID(int=1000),
        question="Will it rain on the Polyterrasse on Friday?",
        status=status,
        close_at=NOW + closes_in,
        resolve_by=NOW + closes_in + timedelta(days=1),
        created_by=PROPOSER.id,
        created_at=NOW - timedelta(days=1),
    )


def row(
    status: BroadcastStatus,
    broadcaster: AuthenticatedUser = PROPOSER,
    *,
    decided_by: AuthenticatedUser | None = ADMIN,
    started: timedelta = timedelta(0),
    last_piece: timedelta | None = None,
    on_air: bool = False,
    end_reason: BroadcastEndReason = BroadcastEndReason.BROADCASTER,
) -> Broadcast:
    """A broadcast of the market of this file by `broadcaster`. A started one started
    `started` before now and took its last piece `last_piece` before now; left out,
    it took none since its start."""
    made = Broadcast(
        id=UUID(int=2000),
        market_id=UUID(int=1000),
        broadcaster_id=broadcaster.id,
        status=status,
        requested_at=NOW - started - timedelta(minutes=5),
    )
    if status is not BroadcastStatus.REQUESTED:
        assert decided_by is not None
        made.decided_by = decided_by.id
        made.decided_at = NOW - started - timedelta(minutes=1)
    if status is BroadcastStatus.REFUSED:
        made.refusal_reason = "Not this week."
    if status in (BroadcastStatus.LIVE, BroadcastStatus.ENDED):
        made.started_at = NOW - started
        made.last_chunk_at = NOW - (started if last_piece is None else last_piece)
        made.on_air_at = made.started_at + 8 * SECOND if on_air else None
    if status is BroadcastStatus.ENDED:
        made.ended_at = NOW
        made.end_reason = end_reason
    return made


@pytest.fixture
def view(base_settings: Settings) -> BroadcastView:
    return BroadcastView(base_settings)


@pytest.fixture
def stall(base_settings: Settings) -> timedelta:
    return timedelta(seconds=base_settings.broadcast.stall_seconds)


@pytest.fixture
def limit(base_settings: Settings) -> timedelta:
    return timedelta(minutes=base_settings.broadcast.max_minutes)


# --- An end the clock brings ----------------------------------------------------------


def test_a_live_broadcast_is_over_from_the_instant_no_piece_arrived_for_too_long(
    view: BroadcastView, stall: timedelta
) -> None:
    live = row(BroadcastStatus.LIVE, started=timedelta(minutes=10), last_piece=timedelta(0))
    stalled_at = NOW + stall

    assert view.over(live, stalled_at - SECOND) is None
    assert view.effective_status(live, stalled_at - SECOND) is BroadcastStatus.LIVE
    assert view.over(live, stalled_at) == (BroadcastEndReason.STALLED, stalled_at)
    assert view.effective_status(live, stalled_at) is BroadcastStatus.ENDED
    assert view.over(live, stalled_at + timedelta(days=3)) == (
        BroadcastEndReason.STALLED,
        stalled_at,
    ), "the end is the instant the silence was too long, whenever somebody looks"


def test_a_live_broadcast_is_over_from_the_instant_it_ran_its_time(
    view: BroadcastView, limit: timedelta
) -> None:
    """Pieces keep arriving: only the time limit ends it."""
    began = NOW - limit + 10 * SECOND
    live = row(BroadcastStatus.LIVE, started=NOW - began, last_piece=timedelta(0))
    limit_at = began + limit

    assert view.over(live, limit_at - SECOND) is None
    assert view.effective_status(live, limit_at - SECOND) is BroadcastStatus.LIVE
    assert view.over(live, limit_at) == (BroadcastEndReason.TIME_LIMIT, limit_at)
    assert view.effective_status(live, limit_at) is BroadcastStatus.ENDED


def test_the_earlier_of_the_two_instants_is_the_end_and_gives_the_reason(
    view: BroadcastView, stall: timedelta, limit: timedelta
) -> None:
    long_after = NOW + timedelta(days=1)
    silent_early = row(BroadcastStatus.LIVE, started=timedelta(minutes=1))
    silent_at_the_limit = row(
        BroadcastStatus.LIVE, started=limit - 10 * SECOND, last_piece=timedelta(0)
    )
    both_at_once = row(BroadcastStatus.LIVE, started=limit, last_piece=stall)

    assert view.over(silent_early, long_after) == (
        BroadcastEndReason.STALLED,
        NOW - timedelta(minutes=1) + stall,
    )
    assert view.over(silent_at_the_limit, long_after) == (
        BroadcastEndReason.TIME_LIMIT,
        NOW + 10 * SECOND,
    )
    assert view.over(both_at_once, NOW) == (BroadcastEndReason.TIME_LIMIT, NOW)


def test_a_broadcast_that_took_no_piece_is_measured_from_its_start(
    view: BroadcastView, stall: timedelta
) -> None:
    just_started = row(BroadcastStatus.LIVE)

    assert just_started.last_chunk_at == just_started.started_at == NOW
    assert view.over(just_started, NOW + stall - SECOND) is None
    assert view.over(just_started, NOW + stall) == (BroadcastEndReason.STALLED, NOW + stall)


@pytest.mark.parametrize(
    "status",
    [
        BroadcastStatus.REQUESTED,
        BroadcastStatus.APPROVED,
        BroadcastStatus.REFUSED,
        BroadcastStatus.ENDED,
    ],
)
def test_every_other_status_is_reported_as_stored_whatever_the_clock_says(
    view: BroadcastView, status: BroadcastStatus
) -> None:
    stored = row(status, started=timedelta(days=30))

    for at in (NOW, NOW + timedelta(days=365)):
        assert view.over(stored, at) is None
        assert view.effective_status(stored, at) is status


def test_open_and_on_air_follow_the_effective_status(view: BroadcastView, stall: timedelta) -> None:
    live = row(BroadcastStatus.LIVE, on_air=True)
    not_yet = row(BroadcastStatus.LIVE)

    assert (view.is_open(live, NOW), view.is_on_air(live, NOW)) == (True, True)
    assert (view.is_open(not_yet, NOW), view.is_on_air(not_yet, NOW)) == (True, False)
    assert (view.is_open(live, NOW + stall), view.is_on_air(live, NOW + stall)) == (False, False)
    assert view.is_open(None, NOW) is False
    assert [view.is_open(row(status), NOW) for status in BroadcastStatus] == [
        True,
        True,
        False,
        True,
        False,
    ]


# --- A market that can have a live stream ---------------------------------------------


@pytest.mark.parametrize("stored", STORED_MARKET_STATUSES)
@pytest.mark.parametrize(
    "closes_in", [timedelta(hours=1), -timedelta(hours=1)], ids=["before", "past"]
)
def test_a_market_is_streamable_while_it_is_open_paused_or_waiting_for_its_result(
    view: BroadcastView, base_settings: Settings, stored: MarketStatus, closes_in: timedelta
) -> None:
    """Asked with the effective status: an OPEN market past its `close_at` is CLOSED
    and streamable, a proposal past it is EXPIRED and is not."""
    effective = MarketView(base_settings).effective_status(market(stored, closes_in=closes_in), NOW)

    assert view.streamable(effective) is (
        stored in (MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED)
    )


def test_no_status_a_market_is_reported_in_is_left_out(view: BroadcastView) -> None:
    assert {status for status in MarketStatus if view.streamable(status)} == {
        MarketStatus.OPEN,
        MarketStatus.HALTED,
        MarketStatus.CLOSED,
    }


# --- What a caller is told about a broadcast ------------------------------------------


def test_the_latest_broadcast_is_shown_to_admins_its_broadcaster_and_while_live_to_everybody(
    view: BroadcastView, stall: timedelta
) -> None:
    shown_to = {
        status: [
            who.username
            for who in (ADMIN, PROPOSER, MEMBER)
            if view.shown_to(who, row(status), NOW)
        ]
        for status in BroadcastStatus
    }

    assert shown_to == {
        BroadcastStatus.REQUESTED: ["caller1", "caller100"],
        BroadcastStatus.APPROVED: ["caller1", "caller100"],
        BroadcastStatus.REFUSED: ["caller1", "caller100"],
        BroadcastStatus.LIVE: ["caller1", "caller100", "caller5"],
        BroadcastStatus.ENDED: ["caller1", "caller100"],
    }
    assert view.shown_to(MEMBER, None, NOW) is False
    assert view.shown_to(MEMBER, row(BroadcastStatus.LIVE), NOW + stall) is False, (
        "a broadcast the clock ended is not LIVE to anybody"
    )


def test_the_reason_of_a_refusal_goes_to_the_broadcaster_and_to_admins(view: BroadcastView) -> None:
    refused = row(BroadcastStatus.REFUSED)

    assert view.item(refused, NOW, ADMIN, user_row(PROPOSER)).refusal_reason == "Not this week."
    assert view.item(refused, NOW, PROPOSER, None).refusal_reason == "Not this week."
    assert view.item(refused, NOW, MEMBER, None).refusal_reason is None


def test_who_broadcasts_is_told_to_admins_alone(view: BroadcastView) -> None:
    live = row(BroadcastStatus.LIVE, on_air=True)

    assert view.item(live, NOW, ADMIN, user_row(PROPOSER)).requested_by == BroadcastPerson(
        username="caller100", email="caller100@ethz.ch"
    )
    assert view.item(live, NOW, PROPOSER, user_row(PROPOSER)).requested_by is None
    assert view.item(live, NOW, MEMBER, None).requested_by is None
    with pytest.raises(ValueError, match="needs the broadcaster"):
        view.item(live, NOW, ADMIN, None)
    with pytest.raises(ValueError, match="needs the broadcaster"):
        view.item(live, NOW, ADMIN, user_row(MEMBER))


def test_a_playlist_is_named_while_the_broadcast_is_on_air_and_at_no_other_time(
    view: BroadcastView, stall: timedelta
) -> None:
    on_air = row(BroadcastStatus.LIVE, on_air=True)
    not_yet = row(BroadcastStatus.LIVE)
    ended = row(BroadcastStatus.ENDED, on_air=True)
    address = "/api/broadcasts/00000000-0000-0000-0000-0000000007d0/hls/index.m3u8"

    playing = view.item(on_air, NOW, MEMBER, None)
    assert (playing.on_air, playing.playlist_url) == (True, address)
    assert view.playlist_url(on_air) == address
    for silent, at in ((not_yet, NOW), (ended, NOW), (on_air, NOW + stall)):
        item = view.item(silent, at, MEMBER, None)
        assert (item.on_air, item.playlist_url) == (False, None)


def test_an_item_reports_the_end_the_clock_brought_before_it_is_stored(
    view: BroadcastView, stall: timedelta
) -> None:
    live = row(BroadcastStatus.LIVE, on_air=True)

    before = view.item(live, NOW + stall - SECOND, MEMBER, None)
    after = view.item(live, NOW + stall + timedelta(hours=1), MEMBER, None)

    assert (before.status, before.ended_at, before.end_reason) == (BroadcastStatus.LIVE, None, None)
    assert (after.status, after.ended_at, after.end_reason) == (
        BroadcastStatus.ENDED,
        NOW + stall,
        BroadcastEndReason.STALLED,
    )
    assert (after.id, after.market_id) == (live.id, live.market_id)
    assert (after.requested_at, after.started_at) == (live.requested_at, live.started_at)


# --- Going live again without asking --------------------------------------------------


def test_a_broadcast_that_ended_by_itself_lets_its_broadcaster_go_live_again(
    view: BroadcastView, stall: timedelta
) -> None:
    resumable = {
        reason: view.resumable(ended, ended, NOW)
        for reason in BroadcastEndReason
        for ended in [row(BroadcastStatus.ENDED, end_reason=reason)]
    }
    over_by_the_clock = row(BroadcastStatus.LIVE)

    assert resumable == {
        BroadcastEndReason.BROADCASTER: False,
        BroadcastEndReason.ADMIN: False,
        BroadcastEndReason.STALLED: True,
        BroadcastEndReason.TIME_LIMIT: True,
    }
    assert view.resumable(over_by_the_clock, over_by_the_clock, NOW) is False
    assert view.resumable(over_by_the_clock, over_by_the_clock, NOW + stall) is True


def test_only_the_latest_broadcast_of_a_market_and_no_admins_own_one_goes_on(
    view: BroadcastView,
) -> None:
    stalled = row(BroadcastStatus.ENDED, end_reason=BroadcastEndReason.STALLED)
    newer = row(BroadcastStatus.REQUESTED)
    newer.id = UUID(int=2001)
    of_an_admin = row(
        BroadcastStatus.ENDED, ADMIN, decided_by=ADMIN, end_reason=BroadcastEndReason.STALLED
    )
    for status in (BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED, BroadcastStatus.REFUSED):
        assert view.resumable(row(status), row(status), NOW) is False

    assert view.resumable(stalled, stalled, NOW) is True
    assert view.resumable(stalled, newer, NOW) is False
    assert view.resumable(stalled, None, NOW) is False
    assert view.resumable(of_an_admin, of_an_admin, NOW) is False


# --- What a caller may do -------------------------------------------------------------

NEED_A_STREAMABLE_MARKET: Final[frozenset[str]] = frozenset(
    {"can_request", "can_start", "can_start_as_admin", "can_approve"}
)
"""Asking, approving and starting need a market that can have a live stream. Refusing,
ending and watching do not: a broadcast that runs goes on whatever becomes of its
market."""

STALLED: Final[BroadcastEndReason] = BroadcastEndReason.STALLED

LATEST: Final[dict[str, Broadcast | None]] = {
    "none": None,
    "requested": row(BroadcastStatus.REQUESTED),
    "approved": row(BroadcastStatus.APPROVED),
    "refused": row(BroadcastStatus.REFUSED),
    "live, nothing to play yet": row(BroadcastStatus.LIVE),
    "on air": row(BroadcastStatus.LIVE, on_air=True),
    "on air, an admin's own": row(BroadcastStatus.LIVE, ADMIN, decided_by=ADMIN, on_air=True),
    "ended by its broadcaster": row(BroadcastStatus.ENDED, on_air=True),
    "ended by an admin": row(
        BroadcastStatus.ENDED, on_air=True, end_reason=BroadcastEndReason.ADMIN
    ),
    "ended by itself": row(BroadcastStatus.ENDED, on_air=True, end_reason=STALLED),
    "an admin's own, ended by itself": row(
        BroadcastStatus.ENDED, ADMIN, decided_by=ADMIN, on_air=True, end_reason=STALLED
    ),
    "live by its row, over by the clock": row(
        BroadcastStatus.LIVE, started=timedelta(hours=1), on_air=True
    ),
}
"""The latest broadcast of the market, by what it is. The proposer asked for each one
and an admin decided, unless it is an admin's own."""

FLAGS: Final[dict[str, dict[str, set[str]]]] = {
    "none": {
        "admin": {"can_start_as_admin"},
        "proposer": {"can_request"},
        "member": set(),
    },
    "requested": {
        "admin": {"can_approve", "can_refuse"},
        "proposer": {"is_broadcaster"},
        "member": set(),
    },
    "approved": {
        "admin": {"can_refuse"},
        "proposer": {"is_broadcaster", "can_start"},
        "member": set(),
    },
    "refused": {
        "admin": {"can_start_as_admin"},
        "proposer": {"is_broadcaster", "can_request"},
        "member": set(),
    },
    "live, nothing to play yet": {
        "admin": {"can_end"},
        "proposer": {"is_broadcaster", "can_end"},
        "member": set(),
    },
    "on air": {
        "admin": {"can_end", "can_watch"},
        "proposer": {"is_broadcaster", "can_end", "can_watch"},
        "member": {"can_watch"},
    },
    "on air, an admin's own": {
        "admin": {"is_broadcaster", "can_end", "can_watch"},
        "proposer": {"can_watch"},
        "member": {"can_watch"},
    },
    "ended by its broadcaster": {
        "admin": {"can_start_as_admin"},
        "proposer": {"is_broadcaster", "can_request"},
        "member": set(),
    },
    "ended by an admin": {
        "admin": {"can_start_as_admin"},
        "proposer": {"is_broadcaster", "can_request"},
        "member": set(),
    },
    "ended by itself": {
        "admin": {"can_start_as_admin"},
        "proposer": {"is_broadcaster", "can_request", "can_start"},
        "member": set(),
    },
    "an admin's own, ended by itself": {
        "admin": {"is_broadcaster", "can_start_as_admin"},
        "proposer": {"can_request"},
        "member": set(),
    },
    "live by its row, over by the clock": {
        "admin": {"can_start_as_admin"},
        "proposer": {"is_broadcaster", "can_request", "can_start"},
        "member": set(),
    },
}
"""The flags that are true, per latest broadcast and caller, in a market that can have a
live stream and with a place to watch free."""

CALLERS: Final[dict[str, AuthenticatedUser]] = {
    "admin": ADMIN,
    "proposer": PROPOSER,
    "member": MEMBER,
}


def true_flags(viewer: BroadcastViewer) -> set[str]:
    return {name for name, value in viewer if value}


@pytest.mark.parametrize("latest", LATEST)
@pytest.mark.parametrize("who", CALLERS)
def test_the_flags_are_what_the_operations_would_say(
    view: BroadcastView, latest: str, who: str
) -> None:
    expected = FLAGS[latest][who]
    streamable, settled = market(MarketStatus.OPEN), market(MarketStatus.RESOLVED)

    in_an_open_market = view.viewer(
        CALLERS[who], streamable, MarketStatus.OPEN, LATEST[latest], NOW, has_place=True
    )
    in_a_settled_market = view.viewer(
        CALLERS[who], settled, MarketStatus.RESOLVED, LATEST[latest], NOW, has_place=True
    )

    assert true_flags(in_an_open_market) == expected
    assert true_flags(in_a_settled_market) == expected - NEED_A_STREAMABLE_MARKET


@pytest.mark.parametrize("latest", LATEST)
@pytest.mark.parametrize("who", CALLERS)
def test_without_a_place_a_broadcast_on_air_is_full_and_nothing_else_changes(
    view: BroadcastView, latest: str, who: str
) -> None:
    expected = FLAGS[latest][who]
    without = view.viewer(
        CALLERS[who], market(), MarketStatus.OPEN, LATEST[latest], NOW, has_place=False
    )

    if "can_watch" in expected:
        assert true_flags(without) == expected - {"can_watch"} | {"full"}
    else:
        assert true_flags(without) == expected


def test_the_table_of_flags_names_every_flag_and_every_status() -> None:
    named = set().union(*(flags for per_caller in FLAGS.values() for flags in per_caller.values()))

    assert named | {"full"} == set(BroadcastViewer.model_fields)
    assert set(LATEST) == set(FLAGS)
    assert {latest.status for latest in LATEST.values() if latest is not None} == set(
        BroadcastStatus
    )


def test_an_admin_who_proposed_the_market_starts_and_does_not_ask(view: BroadcastView) -> None:
    proposing_admin = caller(PROPOSER.id.int, UserRole.ADMIN)

    flags = view.viewer(proposing_admin, market(), MarketStatus.OPEN, None, NOW, has_place=True)

    assert true_flags(flags) == {"can_start_as_admin"}


def test_somebody_who_did_not_propose_the_market_never_asks(view: BroadcastView) -> None:
    for latest in LATEST.values():
        flags = view.viewer(MEMBER, market(), MarketStatus.OPEN, latest, NOW, has_place=True)
        assert flags.can_request is False
        assert (flags.can_start, flags.can_end, flags.is_broadcaster) == (False, False, False)


def test_another_admin_ends_and_refuses_and_does_not_start_what_is_not_theirs(
    view: BroadcastView,
) -> None:
    approved = view.viewer(
        OTHER_ADMIN, market(), MarketStatus.OPEN, LATEST["approved"], NOW, has_place=True
    )
    own_of_another = view.viewer(
        OTHER_ADMIN,
        market(),
        MarketStatus.OPEN,
        LATEST["on air, an admin's own"],
        NOW,
        has_place=True,
    )

    assert true_flags(approved) == {"can_refuse"}
    assert true_flags(own_of_another) == {"can_end", "can_watch"}
