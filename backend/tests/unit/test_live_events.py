"""The events a transaction announces (`app/services/live_events.py`, SPEC §9).

`LiveEvents` builds every event about a market, with the figures `MarketView` reports
for the row and with the audience of that row: `about` admits exactly whom
`MarketAccess.is_inside` admits. `me_changed` is for the listed users alone.
"""

import json
from datetime import timedelta
from itertools import product
from typing import Any, Final
from uuid import UUID, uuid7

import pytest

from app.core.config import Settings
from app.core.enums import STORED_MARKET_STATUSES, MarketStatus, MarketTier, UserRole, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.core.units import MICRO, to_cr
from app.models import Market
from app.pricing import lmsr
from app.schemas.common import ResponseModel
from app.schemas.live import (
    LIVE_EVENTS,
    MarketQuoteEvent,
    MarketStatusEvent,
    MeChangedEvent,
)
from app.services.live_events import MARKET_TOPIC_KINDS, LiveEvents, me_changed
from app.services.market_access import MarketAccess
from app.services.market_view import HIDDEN_STATUSES, MarketView
from tests.support.clock import TEST_NOW
from tests.support.economy import DEFAULT_B

DAY: Final[timedelta] = timedelta(days=1)
MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e")
OTHER_MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5f")
PROPOSER_ID: Final[UUID] = UUID("0198c2f0-0000-7000-8000-000000000001")

PROGRAMME: Final[str] = "BSc Computer Science"
OTHER_PROGRAMME: Final[str] = "MSc Data Science"

MARKETS: Final[Topic] = Topic(TopicKind.MARKETS)
MARKET: Final[Topic] = Topic(TopicKind.MARKET, MARKET_ID)
BROADCAST: Final[Topic] = Topic(TopicKind.BROADCAST, MARKET_ID)

OPENED_STATUSES: Final[tuple[MarketStatus, ...]] = tuple(
    status for status in STORED_MARKET_STATUSES if status not in HIDDEN_STATUSES
)


def viewer(
    number: int,
    programme: str,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.ACTIVE,
    *,
    user_id: UUID | None = None,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id or UUID(int=number),
        email=f"viewer{number}@ethz.ch",
        name=f"Viewer {number}",
        username=f"viewer{number}",
        degree_programme=programme,
        role=role,
        status=status,
    )


VIEWERS: Final[tuple[AuthenticatedUser, ...]] = (
    viewer(1, PROGRAMME),
    viewer(2, OTHER_PROGRAMME),
    viewer(3, OTHER_PROGRAMME, UserRole.ADMIN),
    viewer(4, OTHER_PROGRAMME, UserRole.BARISTA),
    viewer(5, OTHER_PROGRAMME, UserRole.ADMIN, UserStatus.SUSPENDED),
    viewer(6, OTHER_PROGRAMME, user_id=PROPOSER_ID),
)
"""A member, an outsider, an admin, a barista, a suspended admin and the proposer."""


def market(**overrides: Any) -> Market:
    """A market of the DEFAULT tier that traded: YES stands above even odds."""
    values: dict[str, Any] = {
        "id": MARKET_ID,
        "question": "Will it snow on the Polyterrasse before December?",
        "yes_def": "Snow is lying.",
        "no_def": "No snow is lying.",
        "resolution_criteria": "The webcam decides.",
        "resolution_source": "ethz.ch/webcam",
        "status": MarketStatus.OPEN,
        "tier": MarketTier.DEFAULT,
        "budget_micro": lmsr.max_loss_micro(DEFAULT_B),
        "b_micro": DEFAULT_B,
        "q_yes_micro": 191 * MICRO,
        "q_no_micro": 40 * MICRO,
        "version": 8,
        "volume_micro": 1_250_500_000,
        "close_at": TEST_NOW + 7 * DAY,
        "resolve_by": TEST_NOW + 14 * DAY,
        "created_by": PROPOSER_ID,
        "audience_programme": None,
        "created_at": TEST_NOW,
    }
    return Market(**(values | overrides))


@pytest.fixture
def view(base_settings: Settings) -> MarketView:
    return MarketView(base_settings)


@pytest.fixture
def events(view: MarketView) -> LiveEvents:
    return LiveEvents(view)


def body_of(event: LiveEvent) -> dict[str, Any]:
    """The body of an event as a tab reads it."""
    written = event.payload.model_dump_json()
    assert "\n" not in written
    body = json.loads(written)
    assert isinstance(body, dict)
    return body


# --- Events about a market -------------------------------------------------------------


def test_a_quote_carries_what_the_market_view_reports_for_the_row(
    events: LiveEvents, view: MarketView
) -> None:
    row = market()
    p_yes, p_no = view.probabilities(view.p_yes_micro(row))

    event = events.market_quote(row, TEST_NOW)

    assert event.payload == MarketQuoteEvent(
        type="market.quote",
        market_id=MARKET_ID,
        version=8,
        p_yes=p_yes,
        p_no=p_no,
        volume_cr=to_cr(1_250_500_000),
        ts=TEST_NOW,
    )
    assert 0.5 < p_yes < 1
    assert event.topics == (MARKETS, MARKET)
    assert event.ends_streams_of is None
    assert body_of(event) == {
        "type": "market.quote",
        "market_id": str(MARKET_ID),
        "version": 8,
        "p_yes": p_yes,
        "p_no": p_no,
        "volume_cr": "1250.5",
        "ts": "2026-10-09T12:00:00Z",
    }


def test_a_quote_of_a_market_nobody_traded_is_even_odds_and_no_volume(
    events: LiveEvents, view: MarketView
) -> None:
    row = market(q_yes_micro=0, q_no_micro=0, version=0, volume_micro=0)

    body = body_of(events.market_quote(row, TEST_NOW))

    assert (body["p_yes"], body["p_no"]) == view.probabilities(view.p_yes_micro(row))
    assert (body["p_yes"], body["version"], body["volume_cr"]) == (0.5, 0, "0")


@pytest.mark.parametrize("status", OPENED_STATUSES)
def test_a_status_event_reports_the_effective_status_at_its_instant(
    events: LiveEvents, view: MarketView, status: MarketStatus
) -> None:
    row = market(status=status)

    for now in (TEST_NOW, row.close_at, row.close_at + DAY):
        event = events.market_status(row, now)

        assert event.payload == MarketStatusEvent(
            type="market.status",
            market_id=MARKET_ID,
            status=view.effective_status(row, now),
            trading_open=view.is_trading_open(row, now),
            ts=now,
        )
        assert event.topics == (MARKETS, MARKET)


def test_a_status_event_reports_an_open_market_past_its_close_as_closed(
    events: LiveEvents,
) -> None:
    row = market(status=MarketStatus.OPEN)

    before = body_of(events.market_status(row, row.close_at - timedelta(microseconds=1)))
    after = body_of(events.market_status(row, row.close_at))

    assert (before["status"], before["trading_open"]) == ("OPEN", True)
    assert (after["status"], after["trading_open"]) == ("CLOSED", False)
    assert set(after) == {"type", "market_id", "status", "trading_open", "ts"}


@pytest.mark.parametrize(
    ("status", "audience_programme"),
    list(product(OPENED_STATUSES, [None, PROGRAMME, OTHER_PROGRAMME])),
)
def test_an_event_about_a_market_is_for_exactly_who_is_inside_the_market(
    events: LiveEvents, status: MarketStatus, audience_programme: str | None
) -> None:
    """The audience comes from the row and from nowhere else: the three ways an event
    about a market is built agree with `MarketAccess.is_inside` on every viewer."""
    row = market(status=status, audience_programme=audience_programme)
    built = (
        events.market_quote(row, TEST_NOW),
        events.market_status(row, TEST_NOW),
        events.about(row, [BROADCAST, MARKETS], MeChangedEvent(type="me.changed", ts=TEST_NOW)),
    )

    for event in built:
        assert event.audience == Audience.market(row.id, audience_programme, PROPOSER_ID)
        for who in VIEWERS:
            assert event.audience.admits(who) == MarketAccess.is_inside(who, row), who.username


def test_a_private_market_is_for_its_members_its_proposer_and_the_admins(
    events: LiveEvents,
) -> None:
    event = events.market_quote(market(audience_programme=PROGRAMME), TEST_NOW)

    assert [who.username for who in VIEWERS if event.audience.admits(who)] == [
        "viewer1",
        "viewer3",
        "viewer6",
    ]


@pytest.mark.parametrize("status", sorted(HIDDEN_STATUSES))
def test_a_proposal_has_no_events(events: LiveEvents, status: MarketStatus) -> None:
    row = market(status=status)
    body = MeChangedEvent(type="me.changed", ts=TEST_NOW)

    with pytest.raises(ValueError, match=f"stored {status.value} has no events"):
        events.about(row, [MARKET], body)
    with pytest.raises(ValueError, match="has no events"):
        events.market_quote(row, TEST_NOW)
    with pytest.raises(ValueError, match="has no events"):
        events.market_status(row, TEST_NOW)


def test_an_event_about_a_market_goes_out_on_the_topics_of_that_market(
    events: LiveEvents,
) -> None:
    row = market()
    body = MeChangedEvent(type="me.changed", ts=TEST_NOW)

    assert events.about(row, [MARKET], body).topics == (MARKET,)
    assert events.about(row, (BROADCAST, MARKETS), body).topics == (BROADCAST, MARKETS)
    assert events.about(row, [MARKET], body).payload is body
    assert (
        frozenset({TopicKind.MARKETS, TopicKind.MARKET, TopicKind.BROADCAST}) == MARKET_TOPIC_KINDS
    )

    for topics, refusal in (
        ([], "at least one topic"),
        ([Topic(TopicKind.MARKET, OTHER_MARKET_ID)], "is no topic of market"),
        ([MARKET, Topic(TopicKind.BROADCAST, OTHER_MARKET_ID)], "is no topic of market"),
        ([Topic(TopicKind.LEADERBOARD)], "is no topic of market"),
        ([Topic(TopicKind.ME, PROPOSER_ID)], "is no topic of market"),
        ([Topic(TopicKind.ME, MARKET_ID)], "is no topic of market"),
    ):
        with pytest.raises(ValueError, match=refusal):
            events.about(row, topics, body)


# --- Events of a user's own ------------------------------------------------------------


def test_me_changed_goes_to_the_listed_users_on_their_own_topics_and_carries_no_figure() -> None:
    first, second = UUID(int=1), UUID(int=2)

    event = me_changed([second, first, second], TEST_NOW)

    assert event.topics == (Topic(TopicKind.ME, first), Topic(TopicKind.ME, second))
    assert event.audience == Audience.users([first, second])
    assert event.payload == MeChangedEvent(type="me.changed", ts=TEST_NOW)
    assert event.ends_streams_of is None
    assert body_of(event) == {"type": "me.changed", "ts": "2026-10-09T12:00:00Z"}
    assert [who.username for who in VIEWERS if event.audience.admits(who)] == [
        "viewer1",
        "viewer2",
    ]


def test_me_changed_says_whose_streams_end_with_it() -> None:
    user_id = uuid7()

    event = me_changed([user_id], TEST_NOW, ends_streams_of=user_id)

    assert event.ends_streams_of == user_id
    assert event.topics == (Topic(TopicKind.ME, user_id),)


def test_me_changed_for_nobody_reaches_nobody() -> None:
    event = me_changed([], TEST_NOW)

    assert event.topics == ()
    assert not any(event.audience.admits(who) for who in VIEWERS)


# --- Every event is one of the stream's ------------------------------------------------


def test_every_event_built_here_is_a_model_of_the_event_stream_named_by_its_type(
    events: LiveEvents,
) -> None:
    row = market()
    built = (
        events.market_quote(row, TEST_NOW),
        events.market_status(row, TEST_NOW),
        me_changed([PROPOSER_ID], TEST_NOW),
    )

    for event in built:
        assert isinstance(event.payload, ResponseModel)
        assert type(event.payload) in LIVE_EVENTS
        assert body_of(event)["type"] == event.payload.type
    assert [event.payload.type for event in built] == [
        "market.quote",
        "market.status",
        "me.changed",
    ]
