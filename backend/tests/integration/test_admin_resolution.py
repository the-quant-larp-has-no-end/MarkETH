"""Only an admin settles a market, and not one who traded it (SPEC §6, §12, Appendix A.4).

Every request here carries the two headers of the portal and nothing else. No test seam
says who is calling: the real `authenticate_request` reads the headers, the real
`IdentityService` finds the person, and the role is the one the application gives them:
the one of their row, or ADMIN for a bootstrap admin. The market is a real one too:
proposed, approved, traded and closed through the routes, so the admin who "traded the
market" did so through the trading service.

A refusal has to leave everything as it was. "Everything" is every row of every table,
read before and after.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import select

from app.container import Container
from app.core.enums import JournalKind, MarketStatus, Outcome, ResolutionOutcome, UserRole
from app.core.errors import ForbiddenError, UnauthenticatedError
from app.core.units import MICRO
from app.db.base import Base
from app.models import JournalEntry, Market, Position, Resolution, User
from app.schemas.markets import MarketDetailResponse
from app.schemas.trading import TradeResponse
from app.services.market_lifecycle import SELF_DEALING_MESSAGE
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.factories import Factory, PortalHeaders
from tests.support.settings import TEST_ADMIN_EMAILS

pytestmark = pytest.mark.integration

WALLET: Final[int] = 500 * MICRO
"""What somebody who trades here starts with."""

WEEK: Final[timedelta] = timedelta(days=7)

RESOLVE: Final[dict[str, str]] = {"outcome": "YES", "evidence": "The menu of that Friday shows it."}
VOID: Final[dict[str, str]] = {"reason": "The mensa was closed that day."}
SETTLEMENTS: Final[dict[str, dict[str, str]]] = {"resolve": RESOLVE, "void": VOID}
"""The two actions that settle a market, each with a body its route takes."""

both_settlements = pytest.mark.parametrize("action", SETTLEMENTS)

type Rows = dict[str, list[tuple[object, ...]]]


async def everything(container: Container) -> Rows:
    """Every row of every table. Two readings are equal exactly when nothing was
    written in between."""
    found: Rows = {}
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            rows = await uow.session.execute(select(table).order_by(*table.primary_key.columns))
            found[table.name] = [tuple(row) for row in rows]
    return found


class Portal:
    """The routes as the portal passes a signed-in person on to them: with the id and
    the name in two headers."""

    def __init__(self, client: httpx.AsyncClient, headers: PortalHeaders) -> None:
        self._client = client
        self._headers = headers

    async def post(
        self, who: User, path: str, body: dict[str, Any], *, key: UUID | None = None
    ) -> httpx.Response:
        headers = self._headers(who) | ({} if key is None else {"Idempotency-Key": str(key)})
        return await self._client.post(f"/api{path}", json=body, headers=headers)

    async def get(self, who: User, path: str) -> httpx.Response:
        return await self._client.get(f"/api{path}", headers=self._headers(who))

    async def settle(self, who: User, market_id: UUID, action: str) -> httpx.Response:
        return await self.post(who, f"/admin/markets/{market_id}/{action}", SETTLEMENTS[action])

    async def page(self, who: User, market_id: UUID) -> MarketDetailResponse:
        response = await self.get(who, f"/markets/{market_id}")
        assert response.status_code == 200, response.text
        return MarketDetailResponse.model_validate(response.json())


@pytest.fixture
def portal(client: httpx.AsyncClient, portal_headers: PortalHeaders) -> Portal:
    return Portal(client, portal_headers)


@dataclass(frozen=True, slots=True)
class Closed:
    """A CLOSED market that was made and traded through the routes."""

    market_id: UUID
    proposer: User
    trader: User
    """A plain user, who holds YES."""

    barista: User
    traded: User
    """The first admin of the settings, who bought NO."""

    untouched: User
    """The second admin, who approved and closed the market and never traded it."""

    everybody: Sequence[User]


@pytest.fixture
async def closed(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> Closed:
    """Whoever acts here is named to the API by the portal's headers alone."""
    proposer = await factory.user(username="proposer")
    trader = await factory.user(username="trader", balance_micro=WALLET)
    barista = await factory.barista()
    traded = await factory.admin(0, balance_micro=WALLET)
    untouched = await factory.admin(1)
    assert (traded.email, untouched.email) == TEST_ADMIN_EMAILS
    assert barista.role is UserRole.BARISTA

    close_at = clock.now() + WEEK
    proposed = await portal.post(
        proposer,
        "/markets",
        {
            "question": "Will the Polymensa serve Älplermagronen on Friday?",
            "yes_definition": "It is on the lunch menu that day.",
            "no_definition": "It is not on the lunch menu that day.",
            "resolution_criteria": "The menu published for that Friday decides.",
            "resolution_source": "The menu plan on ethz.ch",
            "close_at": close_at.isoformat(),
            "resolve_by": (close_at + WEEK).isoformat(),
            "policy_accepted": True,
        },
    )
    assert proposed.status_code == 201, proposed.text
    market_id = UUID(proposed.json()["id"])
    approved = await portal.post(
        untouched, f"/admin/markets/{market_id}/approve", {"tier": "DEFAULT"}
    )
    assert approved.status_code == 200, approved.text
    for who, outcome, amount in ((trader, "YES", "50"), (traded, "NO", "25")):
        bought = await portal.post(
            who,
            f"/markets/{market_id}/trades",
            {"side": "BUY", "outcome": outcome, "amount_cr": amount},
            key=uuid7(),
        )
        assert bought.status_code == 201, bought.text
        assert TradeResponse.model_validate(bought.json()).outcome == outcome
    clock.advance(timedelta(hours=1))
    shut = await portal.post(untouched, f"/admin/markets/{market_id}/close", {})
    assert shut.status_code == 200, shut.text
    assert shut.json()["status"] == MarketStatus.CLOSED
    await assert_books_balance(container.uow_factory)
    return Closed(
        market_id,
        proposer,
        trader,
        barista,
        traded,
        untouched,
        (proposer, trader, barista, traded, untouched),
    )


async def settlement_of(container: Container, market_id: UUID) -> Resolution | None:
    async with container.uow_factory() as uow:
        return await uow.resolutions.get_by_market(market_id)


async def status_of(container: Container, market_id: UUID) -> MarketStatus:
    async with container.uow_factory() as uow:
        market = await uow.markets.get(market_id)
    assert market is not None
    return market.status


# --- Who the application takes the callers for ---------------------------------------------------


async def test_the_roles_of_the_people_here_are_what_their_rows_and_addresses_make_them(
    closed: Closed, portal: Portal, container: Container
) -> None:
    """The barista is one by the row. The two admins are the bootstrap admins of the
    settings: their rows say USER, and `GET /me` under the real headers answers ADMIN
    for their addresses."""
    assert closed.traded.role is closed.untouched.role is UserRole.USER
    roles = {
        closed.proposer: UserRole.USER,
        closed.trader: UserRole.USER,
        closed.barista: UserRole.BARISTA,
        closed.traded: UserRole.ADMIN,
        closed.untouched: UserRole.ADMIN,
    }

    for who, role in roles.items():
        me = await portal.get(who, "/me")
        assert (me.status_code, me.json()["role"], me.json()["email"]) == (200, role, who.email)
    await assert_books_balance(container.uow_factory)


# --- Whoever is no admin --------------------------------------------------------------------------


@both_settlements
async def test_a_user_a_barista_and_the_proposer_get_403_and_nothing_is_written(
    closed: Closed, portal: Portal, container: Container, action: str
) -> None:
    """ONLY AN ADMIN SETTLES. Holding shares of the market gives no say in how it ends,
    having proposed it gives none, and scanning vouchers gives none."""
    before = await everything(container)

    for nobody in (closed.trader, closed.barista, closed.proposer):
        refused = await portal.settle(nobody, closed.market_id, action)
        assert refused.status_code == 403, f"{nobody.username}: {refused.text}"
        assert refused.json() == {
            "code": "FORBIDDEN",
            "message": ForbiddenError.default_message,
            "details": {},
        }

    assert await everything(container) == before
    assert await status_of(container, closed.market_id) is MarketStatus.CLOSED
    assert await settlement_of(container, closed.market_id) is None
    await assert_books_balance(container.uow_factory)


@both_settlements
async def test_whoever_no_header_names_gets_401_and_nothing_is_written(
    closed: Closed, client: httpx.AsyncClient, container: Container, action: str
) -> None:
    """Without the portal's id there is nobody to be an admin. An id that arrives twice
    names nobody either: the first line could be what a client put in front of the
    portal's own."""
    path = f"/api/admin/markets/{closed.market_id}/{action}"
    admin_id = container.settings.identity.header_user_id
    twice = [(admin_id, closed.untouched.email), (admin_id, closed.untouched.email)]
    before = await everything(container)

    unnamed = await client.post(path, json=SETTLEMENTS[action])
    named_twice = await client.post(path, json=SETTLEMENTS[action], headers=twice)

    for refused in (unnamed, named_twice):
        assert refused.status_code == 401, refused.text
        assert refused.json() == {
            "code": "UNAUTHENTICATED",
            "message": UnauthenticatedError.default_message,
            "details": {},
        }
    assert await everything(container) == before
    await assert_books_balance(container.uow_factory)


async def test_the_role_an_admin_gives_opens_the_settlement_and_taking_it_away_closes_it(
    closed: Closed, portal: Portal, container: Container, factory: Factory
) -> None:
    """An admin by the row alone, made in the panel by another admin, through the
    routes: without the role 403 and nothing written, with it the market page says
    so and the resolution goes through under their name."""
    bystander = await factory.user(username="bystander")
    role_of_the_bystander = f"/admin/users/{bystander.id}/role"
    before = await everything(container)

    as_a_user = await portal.settle(bystander, closed.market_id, "resolve")
    assert (as_a_user.status_code, await everything(container)) == (403, before)

    given = await portal.post(closed.untouched, role_of_the_bystander, {"role": "ADMIN"})
    assert (given.status_code, given.json()["role"]) == (200, "ADMIN"), given.text
    assert (await portal.page(bystander, closed.market_id)).viewer.can_settle

    taken = await portal.post(closed.untouched, role_of_the_bystander, {"role": "USER"})
    assert (taken.status_code, taken.json()["role"]) == (200, "USER"), taken.text
    without_it = await portal.settle(bystander, closed.market_id, "resolve")
    assert without_it.status_code == 403, without_it.text
    assert await settlement_of(container, closed.market_id) is None
    assert not (await portal.page(bystander, closed.market_id)).viewer.can_settle

    await portal.post(closed.untouched, role_of_the_bystander, {"role": "ADMIN"})
    settled = await portal.settle(bystander, closed.market_id, "resolve")

    assert settled.status_code == 200, settled.text
    resolution = await settlement_of(container, closed.market_id)
    assert resolution is not None
    assert (resolution.outcome, resolution.resolved_by) == (ResolutionOutcome.YES, bystander.id)
    await assert_books_balance(container.uow_factory)


# --- An admin who traded the market ---------------------------------------------------------------


@both_settlements
async def test_the_admin_who_traded_the_market_gets_403_and_nothing_is_written(
    closed: Closed, portal: Portal, container: Container, action: str
) -> None:
    """A.4: a resolution could pay that admin, and a void would refund their own
    stake. The API says why, and the market page said so before anybody tried."""
    before = await everything(container)

    refused = await portal.settle(closed.traded, closed.market_id, action)

    assert refused.status_code == 403, refused.text
    assert refused.json() == {
        "code": "FORBIDDEN",
        "message": SELF_DEALING_MESSAGE,
        "details": {},
    }
    assert await everything(container) == before
    assert await status_of(container, closed.market_id) is MarketStatus.CLOSED
    assert not (await portal.page(closed.traded, closed.market_id)).viewer.can_settle
    assert (await portal.page(closed.untouched, closed.market_id)).viewer.can_settle
    assert not (await portal.page(closed.trader, closed.market_id)).viewer.can_settle
    await assert_books_balance(container.uow_factory)


async def test_the_admin_who_traded_is_refused_both_settlements_one_after_the_other(
    closed: Closed, portal: Portal, container: Container
) -> None:
    """Being refused the resolution opens no way to the void, nor the other way round."""
    before = await everything(container)

    answers = [
        await portal.settle(closed.traded, closed.market_id, action)
        for action in ("resolve", "void", "resolve", "void")
    ]

    assert [(answer.status_code, answer.json()["message"]) for answer in answers] == [
        (403, SELF_DEALING_MESSAGE)
    ] * 4
    assert await everything(container) == before
    await assert_books_balance(container.uow_factory)


# --- The admin who did not ------------------------------------------------------------------------


async def test_the_second_admin_resolves_once_and_a_second_resolution_is_409(
    closed: Closed, portal: Portal, container: Container, factory: Factory
) -> None:
    """The whole rule in one run: the first admin, who traded, is refused both ways;
    the second resolves; after that nobody resolves or voids again, the second admin
    included (SPEC §12: "a second resolution fails")."""
    view = container.market_view
    balances = {user.id: await factory.balance_micro(user) for user in closed.everybody}
    for action in SETTLEMENTS:
        refused = await portal.settle(closed.traded, closed.market_id, action)
        assert (refused.status_code, refused.json()["message"]) == (403, SELF_DEALING_MESSAGE)

    settled = await portal.settle(closed.untouched, closed.market_id, "resolve")

    assert settled.status_code == 200, settled.text
    answer = MarketDetailResponse.model_validate(settled.json())
    assert (answer.status, answer.outcome) == (MarketStatus.RESOLVED, ResolutionOutcome.YES)
    assert answer.viewer.can_settle
    resolution = await settlement_of(container, closed.market_id)
    assert resolution is not None
    assert (resolution.outcome, resolution.resolved_by) == (
        ResolutionOutcome.YES,
        closed.untouched.id,
    )
    assert resolution.evidence == RESOLVE["evidence"]
    after_the_first = await everything(container)

    for admin in (closed.untouched, closed.traded):
        for action in ("resolve", "void"):
            again = await portal.settle(admin, closed.market_id, action)
            if admin is closed.untouched:
                assert (again.status_code, again.json()["code"]) == (409, "CONFLICT"), again.text
            else:
                # Who traded is told so before the state of the market is looked at.
                assert (again.status_code, again.json()["message"]) == (403, SELF_DEALING_MESSAGE)

    assert await everything(container) == after_the_first
    async with container.uow_factory() as uow:
        entries = (await uow.session.scalars(select(JournalEntry))).all()
        resolutions = (await uow.session.scalars(select(Resolution))).all()
        positions = (await uow.session.scalars(select(Position))).all()
    assert [entry.kind for entry in entries].count(JournalKind.MARKET_RESOLUTION) == 1
    assert len(resolutions) == 1
    # YES won: the plain trader is paid a credit a share, the admin who held NO nothing.
    for user in closed.everybody:
        owed = view.payout_micro([row for row in positions if row.user_id == user.id], resolution)
        assert owed is not None
        assert await factory.balance_micro(user) == balances[user.id] + owed, user.username
    paid = await factory.balance_micro(closed.trader) - balances[closed.trader.id]
    assert paid == sum(row.shares_micro for row in positions if row.outcome is Outcome.YES) > 0
    assert await factory.balance_micro(closed.traded) == balances[closed.traded.id]
    await assert_books_balance(container.uow_factory)


async def test_the_second_admin_voids_once_and_no_settlement_follows(
    closed: Closed, portal: Portal, container: Container, factory: Factory
) -> None:
    """The same rule for the other way a market ends. A void refunds the stake of the
    admin who traded, which is why that admin could not order it."""
    view = container.market_view
    balances = {user.id: await factory.balance_micro(user) for user in closed.everybody}
    async with container.uow_factory() as uow:
        positions = await uow.positions.list_for_market(closed.market_id)
    assert (await portal.settle(closed.traded, closed.market_id, "void")).status_code == 403

    settled = await portal.settle(closed.untouched, closed.market_id, "void")
    again = await portal.settle(closed.untouched, closed.market_id, "void")
    resolved_after = await portal.settle(closed.untouched, closed.market_id, "resolve")

    assert settled.status_code == 200, settled.text
    assert settled.json()["status"] == MarketStatus.VOID
    for late in (again, resolved_after):
        assert (late.status_code, late.json()["code"]) == (409, "CONFLICT"), late.text
    resolution = await settlement_of(container, closed.market_id)
    assert resolution is not None
    assert (resolution.outcome, resolution.resolved_by) == (
        ResolutionOutcome.VOID,
        closed.untouched.id,
    )
    for user in closed.everybody:
        stake = view.net_stake_micro([row for row in positions if row.user_id == user.id])
        assert await factory.balance_micro(user) == balances[user.id] + stake, user.username
    assert await factory.balance_micro(closed.traded) == WALLET, "the admin's stake came back"
    assert await factory.balance_micro(closed.trader) == WALLET
    async with container.uow_factory() as uow:
        market = await uow.session.get(Market, closed.market_id)
    assert market is not None
    assert (market.status, market.q_yes_micro, market.q_no_micro) == (MarketStatus.VOID, 0, 0)
    await assert_books_balance(container.uow_factory)
