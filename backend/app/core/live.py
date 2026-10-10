"""What the API pushes, as plain values: topics, audiences and events (SPEC §9).

A stream follows topics. An event is published on one or more topics for one audience,
and reaches a stream exactly if the stream follows one of its topics and the audience
admits the stream's viewer. Naming a topic grants nothing: whom an event is for is
decided where the event is built, never by what a stream asks for.

These are values and two pure functions. Nothing here holds a stream, a queue or a
connection, and the module imports nothing of the app but `app.core`.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from app.core.identity import AuthenticatedUser

TOPIC_MAX_LENGTH: Final[int] = 64
"""The longest topic a request can name: a kind, a colon and a UUID fit it."""

TOPICS_HARD_MAX: Final[int] = 32
"""The most topics a request model takes, and so the upper bound of
`LIVE_TOPICS_PER_STREAM`."""


class TopicKind(StrEnum):
    """What a stream can follow."""

    MARKETS = "markets"
    """Every market the viewer may see: what the Markets list shows. No key."""

    MARKET = "market"
    """One market: what Market detail shows. Its key is the market's id."""

    LEADERBOARD = "leaderboard"
    """The ranking. No key."""

    BROADCAST = "broadcast"
    """The live stream of one market. Its key is the market's id."""

    ME = "me"
    """The caller's own events. Its key is the user's id. No request names it: every
    stream follows the one of its viewer."""


KEYED_KINDS: Final[frozenset[TopicKind]] = frozenset(
    {TopicKind.MARKET, TopicKind.BROADCAST, TopicKind.ME}
)
"""The kinds whose topic carries a key. Every other kind is a topic by itself."""

_UUID: Final[str] = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"

TOPIC_PATTERN: Final[str] = rf"^(?:markets|leaderboard|(?:market|broadcast):{_UUID})$"
"""A topic a request can name, as a regular expression: a kind, or a kind and the id of
a market. `me` is not among them."""

_TOPIC: Final[re.Pattern[str]] = re.compile(TOPIC_PATTERN)


@dataclass(frozen=True, slots=True)
class Topic:
    """One thing a stream follows: `markets`, or `market:<uuid>`."""

    kind: TopicKind
    key: UUID | None = None

    def __post_init__(self) -> None:
        if (self.key is not None) != (self.kind in KEYED_KINDS):
            needs = "needs a key" if self.kind in KEYED_KINDS else "takes no key"
            raise ValueError(f"a topic of kind {self.kind.value!r} {needs}")

    def __str__(self) -> str:
        return self.kind.value if self.key is None else f"{self.kind.value}:{self.key}"


def parse_topic(text: str) -> Topic:
    """The topic a request names with `text`.

    `ValueError` for anything that is no topic of `TOPIC_PATTERN`: an unknown kind, a
    key that is no UUID, a kind with a key it does not take or without one it needs,
    padding, and `me`, which no request names.
    """
    if len(text) > TOPIC_MAX_LENGTH or _TOPIC.fullmatch(text) is None:
        raise ValueError(f"not a topic: {text[:TOPIC_MAX_LENGTH]!r}")
    kind, _, key = text.partition(":")
    return Topic(TopicKind(kind), UUID(key) if key else None)


class _Scope(StrEnum):
    EVERYONE = "everyone"
    MARKET = "market"
    USERS = "users"
    ADMINS = "admins"


@dataclass(frozen=True, slots=True)
class Audience:
    """Whom an event is for. Built with one of the four class methods."""

    _scope: _Scope
    _market_id: UUID | None = None
    _programme: str | None = None
    _user_ids: frozenset[UUID] = frozenset()

    @classmethod
    def everyone(cls) -> Audience:
        """Every viewer."""
        return cls(_Scope.EVERYONE)

    @classmethod
    def market(cls, market_id: UUID, audience_programme: str | None, proposer_id: UUID) -> Audience:
        """Whoever is inside a market that was opened (`MarketAccess.is_inside`).

        For a public market (`audience_programme` is None) that is every viewer. For a
        private one: the admins, the viewers whose `degree_programme` is its
        `audience_programme`, and its proposer. A holder outside the audience is not
        among them. `market_id` says which market the audience is of; who is admitted
        follows from the other two.
        """
        return cls(_Scope.MARKET, market_id, audience_programme, frozenset({proposer_id}))

    @classmethod
    def users(cls, user_ids: Collection[UUID]) -> Audience:
        """The viewers with these user ids."""
        return cls(_Scope.USERS, _user_ids=frozenset(user_ids))

    @classmethod
    def admins(cls) -> Audience:
        """The viewers who are admins (`AuthenticatedUser.is_admin`)."""
        return cls(_Scope.ADMINS)

    def admits(self, viewer: AuthenticatedUser) -> bool:
        """Whether an event for this audience reaches a stream of `viewer`."""
        if self._scope is _Scope.EVERYONE:
            return True
        if self._scope is _Scope.ADMINS:
            return viewer.is_admin
        if self._scope is _Scope.USERS:
            return viewer.id in self._user_ids
        return (
            self._programme is None
            or viewer.is_admin
            or viewer.id in self._user_ids
            or viewer.degree_programme == self._programme
        )


class LivePayload(Protocol):
    """The body of an event. Every event model fits: it has a `type`, which is the
    event's name, and writes itself as JSON."""

    @property
    def type(self) -> str: ...

    def model_dump_json(self) -> str: ...


@dataclass(frozen=True, slots=True)
class LiveEvent:
    """One event on its way to the streams that follow one of `topics` and whose viewer
    `audience` admits."""

    topics: tuple[Topic, ...]
    audience: Audience
    payload: LivePayload
    ends_streams_of: UUID | None = None
    """After the event is delivered, every stream of this user is ended: what the user
    may see has changed, and a stream that opens again is judged again."""
