"""Who reaches a market (`app.services.market_access`).

The two rules, `may_see` and `is_inside`, are held to their truth tables for every
stored status and every kind of caller. The loaders are held to them with a unit of work
that answers from memory and counts what it is asked: which read of the market ran, and
whether the positions were asked at all.
"""

from collections.abc import Collection
from contextlib import suppress
from itertools import product
from typing import Final, cast
from uuid import UUID

import pytest

from app.core.enums import STORED_MARKET_STATUSES, MarketStatus, UserRole, UserStatus
from app.core.errors import ErrorCode, ForbiddenError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.models import Market
from app.repositories.unit_of_work import UnitOfWork
from app.services.market_access import NOT_IN_AUDIENCE_MESSAGE, MarketAccess
from app.services.market_view import HIDDEN_STATUSES

PROGRAMME: Final[str] = "BSc Computer Science"
OTHER_PROGRAMME: Final[str] = "MSc Data Science"

PROPOSER_ID: Final[UUID] = UUID(int=100)

PROPOSALS: Final[tuple[MarketStatus, ...]] = (MarketStatus.PROPOSED, MarketStatus.REJECTED)
OPENED: Final[tuple[MarketStatus, ...]] = tuple(
    status for status in STORED_MARKET_STATUSES if status not in PROPOSALS
)


def caller(
    number: int,
    programme: str,
    role: UserRole = UserRole.USER,
    status: UserStatus = UserStatus.ACTIVE,
) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=UUID(int=number),
        email=f"caller{number}@ethz.ch",
        name=f"Caller {number}",
        username=f"caller{number}",
        degree_programme=programme,
        role=role,
        status=status,
    )


ADMIN: Final[AuthenticatedUser] = caller(1, OTHER_PROGRAMME, UserRole.ADMIN)
MEMBER: Final[AuthenticatedUser] = caller(2, PROGRAMME)
PROPOSER: Final[AuthenticatedUser] = caller(100, OTHER_PROGRAMME)
"""The proposer of every market of this file, from another programme than its audience."""
HOLDER: Final[AuthenticatedUser] = caller(4, OTHER_PROGRAMME)
"""Outside the audience, with a position row in every market of `Positions.held`."""
OUTSIDER: Final[AuthenticatedUser] = caller(5, OTHER_PROGRAMME, UserRole.BARISTA)
SUSPENDED_ADMIN: Final[AuthenticatedUser] = caller(
    6, OTHER_PROGRAMME, UserRole.ADMIN, UserStatus.SUSPENDED
)
"""A suspended account keeps its role and none of its rights: here it is an outsider."""

EVERYBODY: Final[frozenset[AuthenticatedUser]] = frozenset(
    {ADMIN, MEMBER, PROPOSER, HOLDER, OUTSIDER, SUSPENDED_ADMIN}
)
NOBODY: Final[frozenset[AuthenticatedUser]] = frozenset()
ONE_BY_ONE: Final[list[AuthenticatedUser]] = sorted(EVERYBODY, key=lambda who: who.id)
"""Every kind of caller, as the cases of a test."""

SEES: Final[dict[tuple[str, bool], frozenset[AuthenticatedUser]]] = {
    ("proposal", False): frozenset({ADMIN, PROPOSER}),
    ("proposal", True): frozenset({ADMIN, PROPOSER}),
    ("opened", False): EVERYBODY,
    ("opened", True): frozenset({ADMIN, MEMBER, PROPOSER, HOLDER}),
}
"""Who may see a market, by what it is and whether it is private. A holder is asked
with `took_part=True`."""

INSIDE: Final[dict[tuple[str, bool], frozenset[AuthenticatedUser]]] = {
    ("proposal", False): NOBODY,
    ("proposal", True): NOBODY,
    ("opened", False): EVERYBODY,
    ("opened", True): frozenset({ADMIN, MEMBER, PROPOSER}),
}
"""Who is inside a market: who reaches what other people put there."""


def market(status: MarketStatus, *, private: bool, number: int = 1) -> Market:
    return Market(
        id=UUID(int=1000 + number),
        status=status,
        created_by=PROPOSER_ID,
        audience_programme=PROGRAMME if private else None,
    )


def kind_of(status: MarketStatus) -> str:
    return "proposal" if status in PROPOSALS else "opened"


EVERY_MARKET: Final[list[tuple[MarketStatus, bool]]] = list(
    product(STORED_MARKET_STATUSES, [False, True])
)


class Markets:
    """Stands for `uow.markets`: the rows it was given, and which read ran."""

    def __init__(self, rows: Collection[Market]) -> None:
        self._rows = {row.id: row for row in rows}
        self.reads: list[str] = []

    async def get(self, market_id: UUID) -> Market | None:
        self.reads.append("get")
        return self._rows.get(market_id)

    async def get_for_update(self, market_id: UUID) -> Market | None:
        self.reads.append("get_for_update")
        return self._rows.get(market_id)


class Positions:
    """Stands for `uow.positions`: `HOLDER` holds a row in every market of `held`, and
    every question is counted."""

    def __init__(self, held: Collection[UUID]) -> None:
        self.held = set(held)
        self.asked: list[tuple[UUID, tuple[UUID, ...]]] = []

    async def exists_for_user_market(self, user_id: UUID, market_id: UUID) -> bool:
        self.asked.append((user_id, (market_id,)))
        return user_id == HOLDER.id and market_id in self.held

    async def market_ids_of_user(self, user_id: UUID, *, among: Collection[UUID]) -> set[UUID]:
        self.asked.append((user_id, tuple(among)))
        return {market_id for market_id in among if user_id == HOLDER.id and market_id in self.held}


class Store:
    """Stands for a `UnitOfWork`, as far as `MarketAccess` reads one."""

    def __init__(self, rows: Collection[Market], *, held: Collection[UUID] | None = None) -> None:
        self.markets = Markets(rows)
        self.positions = Positions([row.id for row in rows] if held is None else held)

    @property
    def uow(self) -> UnitOfWork:
        return cast(UnitOfWork, self)


def not_found() -> pytest.RaisesExc[NotFoundError]:
    """The refusal of a loader: the default `NotFoundError`, which is the answer an id
    no market has gets."""
    return pytest.raises(NotFoundError)


def assert_plain_404(error: NotFoundError) -> None:
    assert (error.code, error.status_code) == (ErrorCode.NOT_FOUND, 404)
    assert (error.message, error.details) == ("Not found.", {})


# --- The two rules --------------------------------------------------------------------


def test_the_statuses_of_a_proposal_are_the_hidden_ones_and_the_rest_were_opened() -> None:
    assert frozenset(PROPOSALS) == HIDDEN_STATUSES
    assert OPENED == (
        MarketStatus.OPEN,
        MarketStatus.HALTED,
        MarketStatus.CLOSED,
        MarketStatus.RESOLVED,
        MarketStatus.VOID,
    )


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
def test_may_see_follows_its_truth_table(status: MarketStatus, private: bool) -> None:
    row = market(status, private=private)

    seen_by = {who for who in EVERYBODY if MarketAccess.may_see(who, row, took_part=who is HOLDER)}

    assert seen_by == SEES[kind_of(status), private]


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
def test_is_inside_follows_its_truth_table(status: MarketStatus, private: bool) -> None:
    row = market(status, private=private)

    inside = {who for who in EVERYBODY if MarketAccess.is_inside(who, row)}

    assert inside == INSIDE[kind_of(status), private]


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
def test_a_position_shows_a_private_opened_market_and_nothing_else(
    status: MarketStatus, private: bool
) -> None:
    """Having taken part changes the answer for one kind of market and one kind of
    caller: a private market that was opened, and a caller outside its audience."""
    row = market(status, private=private)

    for who in EVERYBODY:
        without = MarketAccess.may_see(who, row, took_part=False)
        with_position = MarketAccess.may_see(who, row, took_part=True)
        outside = who in {HOLDER, OUTSIDER, SUSPENDED_ADMIN}
        changes = private and status in OPENED and outside
        assert (with_position, with_position != without) == (without or changes, changes), who


@pytest.mark.parametrize("status", OPENED)
def test_inside_an_opened_market_is_who_may_see_it_without_having_taken_part(
    status: MarketStatus,
) -> None:
    for private, who in product([False, True], EVERYBODY):
        row = market(status, private=private)
        assert MarketAccess.is_inside(who, row) == MarketAccess.may_see(who, row, took_part=False)


def test_in_the_audience_is_everybody_for_a_public_market_and_admins_and_members_otherwise() -> (
    None
):
    assert all(MarketAccess.in_audience(who, None) for who in EVERYBODY)
    assert {who for who in EVERYBODY if MarketAccess.in_audience(who, PROGRAMME)} == {ADMIN, MEMBER}


def test_a_member_states_the_label_of_the_market_character_for_character() -> None:
    for label in (PROGRAMME.upper(), f"{PROGRAMME} ", "BSc Computer", ""):
        assert not MarketAccess.in_audience(caller(9, label), PROGRAMME), label
    assert MarketAccess.in_audience(caller(9, PROGRAMME), PROGRAMME)


# --- The loaders ----------------------------------------------------------------------


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
@pytest.mark.parametrize("for_update", [False, True])
async def test_get_visible_admits_whoever_may_see_the_market(
    status: MarketStatus, private: bool, for_update: bool
) -> None:
    access = MarketAccess()
    row = market(status, private=private)

    for who in EVERYBODY:
        store = Store([row])
        if who in SEES[kind_of(status), private]:
            assert await access.get_visible(store.uow, who, row.id, for_update=for_update) is row
        else:
            with not_found() as refused:
                await access.get_visible(store.uow, who, row.id, for_update=for_update)
            assert_plain_404(refused.value)
        # One read of the market, and with `for_update` it is the one that locks.
        assert store.markets.reads == ["get_for_update" if for_update else "get"]


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
@pytest.mark.parametrize("for_update", [False, True])
async def test_get_opened_admits_whoever_may_see_a_market_that_was_opened(
    status: MarketStatus, private: bool, for_update: bool
) -> None:
    """A proposal has no price, chart or trade: it is not found by anybody, its
    proposer and the admins included, and nothing else is asked for it."""
    access = MarketAccess()
    row = market(status, private=private)
    admitted = SEES[kind_of(status), private] if status in OPENED else NOBODY

    for who in EVERYBODY:
        store = Store([row])
        if who in admitted:
            assert await access.get_opened(store.uow, who, row.id, for_update=for_update) is row
        else:
            with not_found() as refused:
                await access.get_opened(store.uow, who, row.id, for_update=for_update)
            assert_plain_404(refused.value)
        assert store.markets.reads == ["get_for_update" if for_update else "get"]
        if status in PROPOSALS:
            assert store.positions.asked == []


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
async def test_get_inside_admits_whoever_is_inside_and_asks_no_position(
    status: MarketStatus, private: bool
) -> None:
    access = MarketAccess()
    row = market(status, private=private)

    for who in EVERYBODY:
        store = Store([row])
        if who in INSIDE[kind_of(status), private]:
            assert await access.get_inside(store.uow, who, row.id) is row
        else:
            with not_found() as refused:
                await access.get_inside(store.uow, who, row.id)
            assert_plain_404(refused.value)
        assert store.markets.reads == ["get"]
        # A holder outside the audience is refused like anybody else, unasked.
        assert store.positions.asked == []


@pytest.mark.parametrize(("status", "private"), EVERY_MARKET)
async def test_the_positions_are_asked_only_where_the_answer_depends_on_them(
    status: MarketStatus, private: bool
) -> None:
    """That is a private market that was opened, read by a caller who is neither an
    admin, its proposer nor a member."""
    access = MarketAccess()
    row = market(status, private=private)

    for who, load in product(EVERYBODY, [access.get_visible, access.get_opened]):
        store = Store([row])
        with suppress(NotFoundError):
            await load(store.uow, who, row.id)
        depends = private and status in OPENED and who in {HOLDER, OUTSIDER, SUSPENDED_ADMIN}
        assert store.positions.asked == ([(who.id, (row.id,))] if depends else []), who


@pytest.mark.parametrize("who", ONE_BY_ONE, ids=lambda who: who.username)
async def test_an_id_no_market_has_is_not_found_by_every_loader_for_everybody(
    who: AuthenticatedUser,
) -> None:
    access = MarketAccess()
    unknown = UUID(int=424242)

    for load in (access.get_visible, access.get_opened, access.get_inside):
        store = Store([market(MarketStatus.OPEN, private=False)])
        with not_found() as refused:
            await load(store.uow, who, unknown)
        assert_plain_404(refused.value)
        assert store.positions.asked == []


async def test_a_holder_who_holds_nothing_in_a_private_market_is_an_outsider_there() -> None:
    access = MarketAccess()
    held = market(MarketStatus.OPEN, private=True, number=1)
    other = market(MarketStatus.OPEN, private=True, number=2)
    store = Store([held, other], held=[held.id])

    assert await access.get_visible(store.uow, HOLDER, held.id) is held
    with not_found():
        await access.get_visible(store.uow, HOLDER, other.id)
    with not_found():
        await access.get_opened(store.uow, HOLDER, other.id, for_update=True)


# --- Collections ----------------------------------------------------------------------

EVERY_ROW: Final[list[Market]] = [
    market(status, private=private, number=number)
    for number, (status, private) in enumerate(EVERY_MARKET)
]


@pytest.mark.parametrize("who", ONE_BY_ONE, ids=lambda who: who.username)
async def test_visible_among_keeps_what_the_viewer_may_see_in_the_order_given(
    who: AuthenticatedUser,
) -> None:
    access = MarketAccess()
    store = Store(EVERY_ROW)

    kept = await access.visible_among(store.uow, who, EVERY_ROW)

    assert kept == [
        row
        for row in EVERY_ROW
        if who in SEES[kind_of(row.status), row.audience_programme is not None]
    ]
    assert store.markets.reads == []


async def test_visible_among_asks_the_positions_once_and_only_for_markets_in_doubt() -> None:
    access = MarketAccess()
    in_doubt = [
        row.id for row in EVERY_ROW if row.audience_programme is not None and row.status in OPENED
    ]

    for who in (HOLDER, OUTSIDER, SUSPENDED_ADMIN):
        store = Store(EVERY_ROW)
        await access.visible_among(store.uow, who, EVERY_ROW)
        assert store.positions.asked == [(who.id, tuple(in_doubt))]

    # Whoever sees them all otherwise, and a collection without a market in doubt, cost
    # no query.
    for who in (ADMIN, MEMBER, PROPOSER):
        store = Store(EVERY_ROW)
        await access.visible_among(store.uow, who, EVERY_ROW)
        assert store.positions.asked == []
    public_and_proposals = [row for row in EVERY_ROW if row.id not in in_doubt]
    store = Store(EVERY_ROW)
    kept = await access.visible_among(store.uow, OUTSIDER, public_and_proposals)
    assert store.positions.asked == []
    assert kept == [row for row in public_and_proposals if row.status in OPENED]
    assert await access.visible_among(store.uow, OUTSIDER, []) == []


async def test_visible_among_takes_the_markets_its_caller_knows_the_viewer_took_part_in() -> None:
    access = MarketAccess()
    private = [
        market(MarketStatus.OPEN, private=True, number=1),
        market(MarketStatus.RESOLVED, private=True, number=2),
        market(MarketStatus.PROPOSED, private=True, number=3),
    ]
    store = Store(private, held=[])

    kept = await access.visible_among(
        store.uow, OUTSIDER, private, took_part_in={private[1].id, private[2].id}
    )
    none = await access.visible_among(store.uow, OUTSIDER, private, took_part_in=[])

    # A position shows a private market that was opened, never somebody else's proposal.
    assert kept == [private[1]]
    assert none == []
    assert store.positions.asked == []


@pytest.mark.parametrize("who", ONE_BY_ONE, ids=lambda who: who.username)
def test_inside_among_keeps_the_markets_the_viewer_is_inside_of(who: AuthenticatedUser) -> None:
    kept = MarketAccess().inside_among(who, EVERY_ROW)

    assert kept == [
        row
        for row in EVERY_ROW
        if who in INSIDE[kind_of(row.status), row.audience_programme is not None]
    ]


# --- Trading --------------------------------------------------------------------------


def test_whoever_is_outside_the_audience_does_not_trade_and_is_told_whom_the_market_is_for() -> (
    None
):
    access = MarketAccess()
    private = market(MarketStatus.OPEN, private=True)
    public = market(MarketStatus.OPEN, private=False)

    for who in EVERYBODY:
        access.require_audience(who, public)
    for who in (ADMIN, MEMBER):
        access.require_audience(who, private)
    for who in (PROPOSER, HOLDER, OUTSIDER, SUSPENDED_ADMIN):
        with pytest.raises(ForbiddenError) as refused:
            access.require_audience(who, private)
        assert (refused.value.code, refused.value.status_code) == (ErrorCode.FORBIDDEN, 403)
        assert refused.value.message == (
            "This market is for BSc Computer Science only, so you cannot trade it."
        )
        assert refused.value.details == {}
    assert NOT_IN_AUDIENCE_MESSAGE == "This market is for {programme} only, so you cannot trade it."
