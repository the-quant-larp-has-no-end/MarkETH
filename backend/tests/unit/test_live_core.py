"""Topics, audiences and events as plain values (`app.core.live`).

An event reaches a stream exactly if the stream follows one of its topics and its
audience admits the stream's viewer. The audience of a market admits exactly who is
inside the market: the last test holds `Audience.market` to `MarketAccess.is_inside`.
"""

import dataclasses
from datetime import UTC, datetime
from itertools import product
from typing import Final
from uuid import UUID

import pytest

from app.core.enums import STORED_MARKET_STATUSES, MarketStatus, UserRole, UserStatus
from app.core.identity import AuthenticatedUser
from app.core.live import (
    KEYED_KINDS,
    TOPIC_MAX_LENGTH,
    TOPIC_PATTERN,
    TOPICS_HARD_MAX,
    Audience,
    LiveEvent,
    LivePayload,
    Topic,
    TopicKind,
    parse_topic,
)
from app.models import Market
from app.services.market_access import MarketAccess
from app.services.market_view import HIDDEN_STATUSES

MARKET_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e")
OTHER_ID: Final[UUID] = UUID("0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5f")
PROPOSER_ID: Final[UUID] = UUID("0198c2f0-0000-7000-8000-000000000001")
NOW: Final[datetime] = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

PROGRAMME: Final[str] = "BSc Computer Science"
OTHER_PROGRAMME: Final[str] = "MSc Data Science"


def viewer(
    number: int,
    programme: str = PROGRAMME,
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


MEMBER: Final[AuthenticatedUser] = viewer(1)
OUTSIDER: Final[AuthenticatedUser] = viewer(2, OTHER_PROGRAMME)
ADMIN: Final[AuthenticatedUser] = viewer(3, OTHER_PROGRAMME, UserRole.ADMIN)
BARISTA: Final[AuthenticatedUser] = viewer(4, OTHER_PROGRAMME, UserRole.BARISTA)
SUSPENDED_ADMIN: Final[AuthenticatedUser] = viewer(
    5, OTHER_PROGRAMME, UserRole.ADMIN, UserStatus.SUSPENDED
)
PROPOSER: Final[AuthenticatedUser] = viewer(6, OTHER_PROGRAMME, user_id=PROPOSER_ID)
VIEWERS: Final[tuple[AuthenticatedUser, ...]] = (
    MEMBER,
    OUTSIDER,
    ADMIN,
    BARISTA,
    SUSPENDED_ADMIN,
    PROPOSER,
)


# --- Topics ---------------------------------------------------------------------------


def test_the_five_kinds_and_which_of_them_carry_a_key() -> None:
    assert [kind.value for kind in TopicKind] == [
        "markets",
        "market",
        "leaderboard",
        "broadcast",
        "me",
    ]
    assert frozenset({TopicKind.MARKET, TopicKind.BROADCAST, TopicKind.ME}) == KEYED_KINDS
    assert (TOPIC_MAX_LENGTH, TOPICS_HARD_MAX) == (64, 32)


@pytest.mark.parametrize(
    ("topic", "text"),
    [
        (Topic(TopicKind.MARKETS), "markets"),
        (Topic(TopicKind.LEADERBOARD), "leaderboard"),
        (Topic(TopicKind.MARKET, MARKET_ID), f"market:{MARKET_ID}"),
        (Topic(TopicKind.BROADCAST, MARKET_ID), f"broadcast:{MARKET_ID}"),
    ],
)
def test_a_topic_a_request_can_name_is_written_and_read_the_same_way(
    topic: Topic, text: str
) -> None:
    assert str(topic) == text
    assert parse_topic(text) == topic
    assert len(text) <= TOPIC_MAX_LENGTH


def test_the_topic_of_a_caller_is_written_and_never_read() -> None:
    mine = Topic(TopicKind.ME, PROPOSER_ID)

    assert str(mine) == f"me:{PROPOSER_ID}"
    with pytest.raises(ValueError, match="not a topic"):
        parse_topic(str(mine))


def test_a_key_is_read_whatever_the_case_of_its_digits_and_topics_compare_by_value() -> None:
    upper = parse_topic(f"market:{str(MARKET_ID).upper()}")

    assert upper == Topic(TopicKind.MARKET, MARKET_ID)
    assert str(upper) == f"market:{MARKET_ID}"
    assert len({upper, parse_topic(f"market:{MARKET_ID}")}) == 1
    assert Topic(TopicKind.MARKET, MARKET_ID) != Topic(TopicKind.BROADCAST, MARKET_ID)
    assert Topic(TopicKind.MARKET, MARKET_ID) != Topic(TopicKind.MARKET, OTHER_ID)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "me",
        f"me:{MARKET_ID}",
        "market",
        "market:",
        "broadcast",
        f"markets:{MARKET_ID}",
        f"leaderboard:{MARKET_ID}",
        f"stream:{MARKET_ID}",
        f"MARKET:{MARKET_ID}",
        "Markets",
        " markets",
        "markets ",
        "markets\n",
        f"market:{MARKET_ID} ",
        f"market: {MARKET_ID}",
        f"market:{MARKET_ID.hex}",
        f"market:{{{MARKET_ID}}}",
        f"market:urn:uuid:{MARKET_ID}",
        f"market:{MARKET_ID}:{MARKET_ID}",
        f"market:{MARKET_ID},markets",
        "market:not-an-id",
        "market:0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5",
        "market:0198c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5g",
        "market:\uff10\uff11\uff19\uff18c2f0-7b1e-7c53-9d0a-3a1f2b6c4d5e",
        "x" * 65,
        "markets" + "\x00",
    ],
)
def test_anything_that_is_no_topic_of_a_request_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="not a topic"):
        parse_topic(text)


def test_the_pattern_of_a_topic_names_the_four_kinds_a_request_can_name() -> None:
    assert TOPIC_PATTERN == (
        r"^(?:markets|leaderboard|(?:market|broadcast):"
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$"
    )


@pytest.mark.parametrize("kind", sorted(TopicKind))
def test_a_topic_has_a_key_exactly_if_its_kind_carries_one(kind: TopicKind) -> None:
    if kind in KEYED_KINDS:
        assert Topic(kind, MARKET_ID).key == MARKET_ID
        with pytest.raises(ValueError, match="needs a key"):
            Topic(kind)
    else:
        assert Topic(kind).key is None
        with pytest.raises(ValueError, match="takes no key"):
            Topic(kind, MARKET_ID)


# --- Audiences ------------------------------------------------------------------------


@pytest.mark.parametrize("who", VIEWERS, ids=lambda who: who.username)
def test_everyone_admits_every_viewer(who: AuthenticatedUser) -> None:
    assert Audience.everyone().admits(who)


def test_the_admins_are_the_viewers_whose_role_in_effect_is_admin() -> None:
    admitted = [who for who in VIEWERS if Audience.admins().admits(who)]

    assert admitted == [ADMIN]


def test_listed_users_are_admitted_by_their_id_and_nobody_else_is() -> None:
    listed = Audience.users([MEMBER.id, SUSPENDED_ADMIN.id])

    assert [who for who in VIEWERS if listed.admits(who)] == [MEMBER, SUSPENDED_ADMIN]
    assert not any(Audience.users([]).admits(who) for who in VIEWERS)
    assert Audience.users((MEMBER.id, MEMBER.id)) == Audience.users({MEMBER.id})


def test_a_public_market_is_for_every_viewer() -> None:
    public = Audience.market(MARKET_ID, None, PROPOSER_ID)

    assert all(public.admits(who) for who in VIEWERS)


def test_a_private_market_is_for_its_members_its_proposer_and_the_admins() -> None:
    private = Audience.market(MARKET_ID, PROGRAMME, PROPOSER_ID)

    assert [who for who in VIEWERS if private.admits(who)] == [MEMBER, ADMIN, PROPOSER]


def test_a_member_is_one_whose_programme_is_the_label_character_for_character() -> None:
    private = Audience.market(MARKET_ID, PROGRAMME, PROPOSER_ID)

    for label in (PROGRAMME.lower(), f"{PROGRAMME} ", f" {PROGRAMME}", "", "BSc Computer"):
        assert not private.admits(viewer(7, label)), label
    assert private.admits(viewer(7, PROGRAMME))


def test_an_audience_is_a_value() -> None:
    private = Audience.market(MARKET_ID, PROGRAMME, PROPOSER_ID)

    assert private == Audience.market(MARKET_ID, PROGRAMME, PROPOSER_ID)
    assert private != Audience.market(MARKET_ID, OTHER_PROGRAMME, PROPOSER_ID)
    assert private != Audience.market(OTHER_ID, PROGRAMME, PROPOSER_ID)
    assert Audience.everyone() == Audience.everyone()
    assert Audience.admins() != Audience.everyone()
    assert len({Audience.admins(), Audience.admins(), Audience.users([MEMBER.id])}) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        private._programme = None  # type: ignore[misc]


def _market(status: MarketStatus, audience_programme: str | None) -> Market:
    return Market(
        id=MARKET_ID,
        status=status,
        created_by=PROPOSER_ID,
        audience_programme=audience_programme,
    )


OPENED_STATUSES: Final[tuple[MarketStatus, ...]] = tuple(
    status for status in STORED_MARKET_STATUSES if status not in HIDDEN_STATUSES
)


@pytest.mark.parametrize(
    ("status", "audience_programme"),
    list(product(OPENED_STATUSES, [None, PROGRAMME, OTHER_PROGRAMME])),
)
def test_the_audience_of_a_market_admits_exactly_who_is_inside_the_market(
    status: MarketStatus, audience_programme: str | None
) -> None:
    """An event about a market reaches whoever reaches what people put on the market,
    and nobody else. A market has an audience once it was opened."""
    market = _market(status, audience_programme)
    audience = Audience.market(market.id, market.audience_programme, market.created_by)

    for who in VIEWERS:
        assert audience.admits(who) == MarketAccess.is_inside(who, market), who.username


def test_the_opened_statuses_are_the_five_a_market_has_after_its_approval() -> None:
    assert OPENED_STATUSES == (
        MarketStatus.OPEN,
        MarketStatus.HALTED,
        MarketStatus.CLOSED,
        MarketStatus.RESOLVED,
        MarketStatus.VOID,
    )


# --- Events ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Body:
    type: str
    ts: datetime

    def model_dump_json(self) -> str:
        return f'{{"type":"{self.type}","ts":"{self.ts.isoformat()}"}}'


def test_an_event_has_topics_an_audience_and_a_body_and_ends_no_stream_unless_it_says_so() -> None:
    body: LivePayload = _Body("market.status", NOW)
    topics = (Topic(TopicKind.MARKETS), Topic(TopicKind.MARKET, MARKET_ID))

    event = LiveEvent(topics, Audience.market(MARKET_ID, PROGRAMME, PROPOSER_ID), body)
    ending = LiveEvent(
        (Topic(TopicKind.ME, MEMBER.id),),
        Audience.users([MEMBER.id]),
        _Body("me.changed", NOW),
        ends_streams_of=MEMBER.id,
    )

    assert (event.topics, event.payload.type, event.ends_streams_of) == (
        topics,
        "market.status",
        None,
    )
    assert (
        event.payload.model_dump_json()
        == '{"type":"market.status","ts":"2026-10-10T12:00:00+00:00"}'
    )
    assert ending.ends_streams_of == MEMBER.id
    assert [field.name for field in dataclasses.fields(LiveEvent)] == [
        "topics",
        "audience",
        "payload",
        "ends_streams_of",
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.ends_streams_of = MEMBER.id  # type: ignore[misc]
