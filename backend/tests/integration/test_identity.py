"""Who a request comes from, as the portal's two headers say it (SPEC §3, Appendix A.2,
A.3).

`tests/integration/test_errors.py` proves what the guards do with a caller once somebody
has said who is calling. Here nobody says it for the application. In the first half a
request carries the two headers the portal would send (`portal_headers`), the real
`authenticate_request` reads them, and the application's own `IdentityService` looks
the caller up in Postgres and gives them the role of their row, or ADMIN if their
address is a bootstrap admin of the test settings.

What the services behind the guards answer is not the point. A caller was refused if
the answer is 401 or 403 and was let in if it is neither. Where a test wants to be exact
about "let in", it sends something the route cannot take: 422 is an answer only a
caller who got past the guards gets, and no service is asked for it.

The second half asks `IdentityService` directly, with a stand-in for the upkeep it
starts. What a run of upkeep does is `tests/integration/test_upkeep.py`.
"""

import asyncio
import logging
from typing import Any, Final
from uuid import uuid7

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings
from app.core.enums import UserRole, UserStatus
from app.core.errors import SUSPENDED_MESSAGE, ForbiddenError
from app.core.identity import (
    DISPLAY_NAME_MAX_LENGTH,
    AuthenticatedUser,
    Caller,
    PortalIdentity,
)
from app.db.database import Database, is_unavailable
from app.main import create_app
from app.models import User
from app.repositories.unit_of_work import UnitOfWork
from app.services.identity import IdentityService
from app.services.upkeep import UserUpkeepService
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.factories import Factory, PortalHeaders
from tests.support.settings import TEST_ADMIN_EMAILS

pytestmark = pytest.mark.integration

type Operation = tuple[str, str]

ERROR_KEYS: Final[set[str]] = {"code", "message", "details"}

ME: Final[str] = "/api/me"
PROFILE: Final[str] = "/api/me/profile"
ADMIN_ROUTE: Final[str] = "/api/admin/risk"
BARISTA_ROUTE: Final[str] = "/api/barista/vouchers/redeem"
SCANNED: Final[dict[str, str]] = {"code": "ABCD-EFGH"}

NOT_A_MARKET: Final[str] = "/api/markets/not-a-uuid"
"""A member route with a path it cannot take: 422 for whoever gets past its guard."""

STREAM: Final[str] = "/api/stream"
"""The event stream, asked for without its `tab`: 422 for whoever gets past its guard,
and no stream."""

MEMBER_OPERATIONS: Final[tuple[Operation, ...]] = (
    ("GET", ME),
    ("GET", "/api/markets"),
    ("GET", NOT_A_MARKET),
    ("GET", "/api/leaderboard"),
    ("POST", "/api/vouchers"),
    ("POST", "/api/codes/redeem"),
    ("POST", BARISTA_ROUTE),
    ("GET", ADMIN_ROUTE),
    ("GET", STREAM),
)
"""Routes behind the member guard, of every kind: read and write, plain, barista and
admin, and the event stream."""

PROFILE_BODY: Final[dict[str, object]] = {
    "username": "newcomer",
    "degree_level": "BACHELOR",
    "degree_programme": "BSc Computer Science",
    "policy_accepted": True,
}

PATH_VALUES: Final[dict[str, str]] = {
    "market_id": str(uuid7()),
    "batch_id": str(uuid7()),
    "user_id": str(uuid7()),
    "alert_id": "1",
    "message_id": str(uuid7()),
    "broadcast_id": str(uuid7()),
}
"""A value for every path parameter of an admin route."""

REFUSED: Final[tuple[int, int]] = (401, 403)
"""What a guard answers."""

NEWCOMER: Final[PortalIdentity] = PortalIdentity(
    email="newcomer@ethz.ch", name="Nora Newcomer", role=UserRole.USER
)
"""Somebody the portal signed in who has not saved a profile yet."""

ZOE: Final[str] = "Zo" + chr(0xEB) + " M" + chr(0xFC) + "ller"

IMPATIENT_MS: Final[int] = 200
"""A POSTGRES_LOCK_TIMEOUT_MS short enough to sit through in a test."""

UPKEEP_LOGGER: Final[str] = "app.services.upkeep"

TRANSACTIONS_LEFT_OPEN: Final[str] = (
    "SELECT count(*) FROM pg_stat_activity"
    " WHERE datname = current_database() AND pid <> pg_backend_pid()"
    " AND state LIKE 'idle in transaction%'"
)
"""How many sessions sit in a transaction they began and have not ended."""


def assert_error(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is the documented error; returns its `details`."""
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == ERROR_KEYS
    assert body["code"] == code
    details: dict[str, Any] = body["details"]
    return details


def assert_let_in(response: httpx.Response) -> None:
    """The caller got past every guard of the route, to a request it cannot take."""
    assert_error(response, 422, "VALIDATION_ERROR")


def assert_suspended(response: httpx.Response) -> None:
    assert assert_error(response, 403, "FORBIDDEN") == {}
    assert response.json()["message"] == SUSPENDED_MESSAGE


def admin_operations(app: FastAPI) -> list[Operation]:
    """Every (METHOD, path) the application serves under `/api/admin/`."""
    return sorted(
        (method.upper(), path.format(**PATH_VALUES))
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/admin/")
        for method in item
    )


def approve_a_market() -> str:
    """An admin route that needs a body: 422 for an admin who sends none."""
    return f"/api/admin/markets/{uuid7()}/approve"


# --- Nobody, and somebody the application does not know -----------------------------------


async def test_a_request_without_the_id_header_is_401(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """A name alone names nobody: the id is who somebody is."""
    only_a_name = {settings.identity.header_user_name: "Ada%20Admin"}

    for headers in ({}, only_a_name):
        for method, path in (*MEMBER_OPERATIONS, ("PUT", PROFILE)):
            response = await client.request(method, path, headers=headers)
            assert_error(response, 401, "UNAUTHENTICATED")


async def test_an_id_header_sent_twice_is_401_whoever_the_two_are(
    client: httpx.AsyncClient, factory: Factory, settings: Settings
) -> None:
    """Whatever a client put in front of the portal's own line must not be the one
    read. Two lines that agree are refused like two that do not."""
    admin, student = await factory.admin(), await factory.user()
    header = settings.identity.header_user_id

    for first, second in (
        (admin.email, student.email),
        (student.email, admin.email),
        (student.email, student.email),
        (student.email, ""),
    ):
        twice = [(header, first), (header, second)]
        assert_error(await client.get(ME, headers=twice), 401, "UNAUTHENTICATED")
        assert_error(await client.get(ADMIN_ROUTE, headers=twice), 401, "UNAUTHENTICATED")
        saved = await client.put(PROFILE, json=PROFILE_BODY, headers=twice)
        assert_error(saved, 401, "UNAUTHENTICATED")


@pytest.mark.parametrize(
    "user_id",
    [
        "someone@uzh.ch",
        "user1@student.ethz.ch",
        "user1@ethz.ch.example.org",
        "user1@ethz.ch, admin@ethz.ch",
        "admin@ethz.ch@ethz.ch",
        "user1+admin@ethz.ch",
        "user1%40ethz.ch",
        "@ethz.ch",
        "user1",
    ],
    ids=[
        "foreign-domain",
        "subdomain",
        "allowed-domain-as-a-prefix",
        "two-addresses",
        "two-at-signs",
        "tagged",
        "percent-encoded",
        "no-local-part",
        "no-domain",
    ],
)
async def test_an_id_that_is_no_address_at_an_allowed_domain_is_403_with_the_allowed_domains(
    client: httpx.AsyncClient, factory: Factory, settings: Settings, user_id: str
) -> None:
    """An id is exactly what the portal sends. One that is merely close to the id of a
    registered user is somebody the application does not know."""
    registered = await factory.user()
    assert registered.email == "user1@ethz.ch"
    headers = {settings.identity.header_user_id: user_id}

    for method, path in (*MEMBER_OPERATIONS, ("PUT", PROFILE)):
        response = await client.request(method, path, headers=headers)
        details = assert_error(response, 403, "FORBIDDEN")
        assert details == {"allowed_domains": settings.identity.allowed_email_domains}
        assert user_id not in response.text


# --- A caller without a profile -----------------------------------------------------------


async def test_an_unregistered_caller_gets_403_profile_required_with_name_and_email(
    client: httpx.AsyncClient, portal_headers: PortalHeaders
) -> None:
    for method, path in MEMBER_OPERATIONS:
        response = await client.request(method, path, headers=portal_headers(NEWCOMER))
        details = assert_error(response, 403, "PROFILE_REQUIRED")
        assert details == {"name": "Nora Newcomer", "email": "newcomer@ethz.ch"}


@pytest.mark.parametrize("address", TEST_ADMIN_EMAILS)
async def test_a_bootstrap_admin_gets_nowhere_before_the_profile_exists(
    client: httpx.AsyncClient, settings: Settings, address: str
) -> None:
    """An address of ADMIN_EMAILS sets up a profile like everybody else."""
    headers = {settings.identity.header_user_id: address}

    for method, path in MEMBER_OPERATIONS:
        response = await client.request(method, path, headers=headers)
        details = assert_error(response, 403, "PROFILE_REQUIRED")
        assert details == {"name": "", "email": address}


async def test_an_unregistered_caller_is_let_in_on_the_profile_route(
    client: httpx.AsyncClient, container: Container, portal_headers: PortalHeaders
) -> None:
    """It is how such a caller gets a profile."""
    assert_let_in(await client.put(PROFILE, json={}, headers=portal_headers(NEWCOMER)))

    saved = await client.put(PROFILE, json=PROFILE_BODY, headers=portal_headers(NEWCOMER))
    assert saved.status_code not in REFUSED, saved.text
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("sent", "name"),
    [
        ("Zo%C3%AB%20M%C3%BCller", ZOE),
        ("%20%20Ada%09%0A%20Admin%0D%0A", "Ada Admin"),
        ("Ada%00Admin", "AdaAdmin"),
        ("Ada%E2%80%AE%20Admin%E2%80%8B", "Ada Admin"),
        ("a" * 300, "a" * DISPLAY_NAME_MAX_LENGTH),
        ("%2500", "%00"),
        ("%00%20%0A", ""),
        ("", ""),
    ],
    ids=[
        "percent-encoded",
        "mixed-whitespace",
        "nul",
        "format-characters",
        "300-characters",
        "decoded-once",
        "nothing-to-keep",
        "empty",
    ],
)
async def test_the_display_name_arrives_cleaned(
    client: httpx.AsyncClient, settings: Settings, sent: str, name: str
) -> None:
    """Decoded once, without control and format characters, whitespace collapsed, cut
    to the width a profile keeps. The identity service takes it as it is: a name that
    was cleaned twice would turn `%2500` into a NUL."""
    names = settings.identity
    headers = {names.header_user_id: NEWCOMER.email, names.header_user_name: sent}

    response = await client.get(ME, headers=headers)

    details = assert_error(response, 403, "PROFILE_REQUIRED")
    assert details == {"name": name, "email": NEWCOMER.email}


async def test_a_name_header_that_is_missing_or_sent_twice_is_an_empty_name(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    names = settings.identity
    without = [(names.header_user_id, NEWCOMER.email)]
    twice = [*without, (names.header_user_name, "Mallory"), (names.header_user_name, "Nora")]

    for headers in (without, twice):
        details = assert_error(await client.get(ME, headers=headers), 403, "PROFILE_REQUIRED")
        assert details == {"name": "", "email": NEWCOMER.email}


# --- A registered caller ------------------------------------------------------------------


async def test_a_registered_caller_is_let_in(
    client: httpx.AsyncClient, factory: Factory, portal_headers: PortalHeaders
) -> None:
    user = await factory.user()
    headers = portal_headers(user)

    assert_let_in(await client.get(NOT_A_MARKET, headers=headers))
    assert_let_in(await client.put(PROFILE, json={}, headers=headers))
    assert (await client.get(ME, headers=headers)).status_code not in REFUSED


async def test_an_id_in_capitals_is_the_account_of_its_lower_case(
    client: httpx.AsyncClient, factory: Factory, settings: Settings
) -> None:
    await factory.user(email="zoe.muster@ethz.ch")
    header = settings.identity.header_user_id

    registered = await client.get(NOT_A_MARKET, headers={header: "Zoe.Muster@ETHZ.CH"})
    stranger = await client.get(NOT_A_MARKET, headers={header: "Nora.Newcomer@ETHZ.ch"})

    assert_let_in(registered)
    details = assert_error(stranger, 403, "PROFILE_REQUIRED")
    assert details["email"] == "nora.newcomer@ethz.ch"


async def test_a_suspended_caller_is_refused_on_member_routes_and_on_the_profile_route(
    client: httpx.AsyncClient, factory: Factory, portal_headers: PortalHeaders
) -> None:
    """The profile route included: a suspended account cannot rename itself either."""
    suspended = await factory.user(status=UserStatus.SUSPENDED)
    headers = portal_headers(suspended)

    for method, path in MEMBER_OPERATIONS:
        assert_suspended(await client.request(method, path, headers=headers))
    assert_suspended(await client.put(PROFILE, json=PROFILE_BODY, headers=headers))
    assert_suspended(await client.put(PROFILE, json={}, headers=headers))


async def test_a_suspended_account_keeps_its_role_and_none_of_its_rights(
    client: httpx.AsyncClient, factory: Factory, portal_headers: PortalHeaders
) -> None:
    """Whether the role is in the row or comes with the address."""
    suspended = [
        await factory.user(email=TEST_ADMIN_EMAILS[0], status=UserStatus.SUSPENDED),
        await factory.user(email=TEST_ADMIN_EMAILS[1], status=UserStatus.SUSPENDED),
        await factory.user(role=UserRole.ADMIN, status=UserStatus.SUSPENDED),
        await factory.user(role=UserRole.BARISTA, status=UserStatus.SUSPENDED),
    ]

    for account in suspended:
        headers = portal_headers(account)
        assert_suspended(await client.get(ADMIN_ROUTE, headers=headers))
        assert_suspended(await client.post(approve_a_market(), json={}, headers=headers))
        assert_suspended(await client.post(BARISTA_ROUTE, json=SCANNED, headers=headers))
        assert_suspended(await client.get(ME, headers=headers))


# --- Roles: the row, and the bootstrap admins ----------------------------------------------


def set_role(user: User, role: UserRole) -> tuple[str, dict[str, str]]:
    """The admin route that gives `user` a role, and its body."""
    return f"/api/admin/users/{user.id}/role", {"role": role.value}


async def test_admin_routes_answer_403_to_a_user_and_to_a_barista(
    client: httpx.AsyncClient, app: FastAPI, factory: Factory, portal_headers: PortalHeaders
) -> None:
    student, barista = await factory.user(), await factory.barista()
    routes = admin_operations(app)
    assert len(routes) == 24

    for method, path in routes:
        for outsider in (student, barista):
            response = await client.request(method, path, headers=portal_headers(outsider))
            assert assert_error(response, 403, "FORBIDDEN") == {}
            assert response.json()["message"] != SUSPENDED_MESSAGE


@pytest.mark.parametrize("index", range(len(TEST_ADMIN_EMAILS)))
async def test_admin_routes_let_every_address_of_admin_emails_in(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
    index: int,
) -> None:
    """A bootstrap admin: the row says USER, and the address is on the list."""
    admin = await factory.admin(index)
    assert admin.role is UserRole.USER
    headers = portal_headers(admin)

    assert_let_in(await client.post(approve_a_market(), json={}, headers=headers))
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code not in REFUSED
    await assert_books_balance(container.uow_factory)


async def test_admin_routes_let_a_user_in_whose_row_says_admin(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    """An admin the panel made: no list of `.env` names the address."""
    made_admin = await factory.user(role=UserRole.ADMIN)
    assert made_admin.email not in TEST_ADMIN_EMAILS
    headers = portal_headers(made_admin)

    assert_let_in(await client.post(approve_a_market(), json={}, headers=headers))
    assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code not in REFUSED
    await assert_books_balance(container.uow_factory)


async def test_the_barista_route_lets_baristas_and_every_admin_in_and_nobody_else(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    student, barista = await factory.user(), await factory.barista()
    admins = [
        await factory.admin(),
        await factory.admin(1),
        await factory.user(role=UserRole.ADMIN),
    ]

    for body in ({}, SCANNED):
        refused = await client.post(BARISTA_ROUTE, json=body, headers=portal_headers(student))
        assert assert_error(refused, 403, "FORBIDDEN") == {}
    for staff in (barista, *admins):
        headers = portal_headers(staff)
        assert_let_in(await client.post(BARISTA_ROUTE, json={}, headers=headers))
        scanned = await client.post(BARISTA_ROUTE, json=SCANNED, headers=headers)
        assert scanned.status_code not in REFUSED, scanned.text
    await assert_books_balance(container.uow_factory)


async def test_a_bootstrap_admin_is_an_admin_whatever_its_row_says(
    client: httpx.AsyncClient,
    factory: Factory,
    owner_engine: AsyncEngine,
    portal_headers: PortalHeaders,
) -> None:
    """No admin route writes the role of such an account. Whatever the row holds, the
    address decides."""
    admin = await factory.admin()
    headers = portal_headers(admin)

    for stored in UserRole:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text("UPDATE users SET role = :role WHERE id = :id"),
                {"role": stored.value, "id": admin.id},
            )

        me = await client.get(ME, headers=headers)

        assert (me.status_code, me.json()["role"]) == (200, "ADMIN"), stored
        assert (await client.get(ADMIN_ROUTE, headers=headers)).status_code not in REFUSED


async def test_under_another_list_a_bootstrap_admin_has_the_role_of_its_row(
    factory: Factory,
    settings: Settings,
    database: Database,
    clock: FrozenClock,
    portal_headers: PortalHeaders,
) -> None:
    """Who is a bootstrap admin is what `.env` said when the process started. An
    address taken off ADMIN_EMAILS is what its row says at the next start of the API,
    one put on it is an admin, and a role an admin gave stays what it is."""
    was_admin, student, barista = (
        await factory.admin(),
        await factory.user(),
        await factory.barista(),
    )
    swapped = settings.identity.model_copy(update={"admin_emails": [student.email]})
    restarted = Container.build(
        settings.model_copy(update={"identity": swapped}), clock=clock, database=database
    )
    origin = settings.app.public_base_url
    transport = httpx.ASGITransport(app=create_app(restarted))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url=origin, headers={"Origin": origin}
        ) as client:
            approve = approve_a_market()
            demoted = await client.post(approve, json={}, headers=portal_headers(was_admin))
            promoted = await client.post(approve, json={}, headers=portal_headers(student))
            at_the_bar = await client.post(
                BARISTA_ROUTE, json={}, headers=portal_headers(was_admin)
            )
            still_a_barista = await client.post(
                BARISTA_ROUTE, json={}, headers=portal_headers(barista)
            )
    finally:
        await restarted.stop()

    assert assert_error(demoted, 403, "FORBIDDEN") == {}
    assert_let_in(promoted)
    assert assert_error(at_the_bar, 403, "FORBIDDEN") == {}
    assert_let_in(still_a_barista)


# --- Nothing is remembered between requests -----------------------------------------------


async def test_a_profile_holds_from_the_request_after_it_was_created(
    client: httpx.AsyncClient, factory: Factory, portal_headers: PortalHeaders
) -> None:
    headers = portal_headers(NEWCOMER)
    assert_error(await client.get(NOT_A_MARKET, headers=headers), 403, "PROFILE_REQUIRED")

    await factory.user(email=NEWCOMER.email)

    assert_let_in(await client.get(NOT_A_MARKET, headers=headers))


async def test_a_suspension_holds_from_the_next_request_on_and_so_does_its_end(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    """An admin suspends the account and reinstates it, through the routes of the
    panel. Nothing tells the user's session: there is none."""
    user, admin = await factory.user(), await factory.admin()
    headers = portal_headers(user)
    reason = {"reason": "Looked into."}

    assert_let_in(await client.get(NOT_A_MARKET, headers=headers))
    suspended = await client.post(
        f"/api/admin/users/{user.id}/suspend", json=reason, headers=portal_headers(admin)
    )
    assert suspended.status_code == 200, suspended.text
    assert_suspended(await client.get(NOT_A_MARKET, headers=headers))
    reinstated = await client.post(
        f"/api/admin/users/{user.id}/reinstate", json=reason, headers=portal_headers(admin)
    )
    assert reinstated.status_code == 200, reinstated.text
    assert_let_in(await client.get(NOT_A_MARKET, headers=headers))
    await assert_books_balance(container.uow_factory)


async def test_a_role_an_admin_sets_holds_from_the_next_request_on(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    """From a plain user to a barista, to an admin and back, each with one request of
    an admin and nothing else."""
    user, admin = await factory.user(), await factory.admin()
    headers, as_admin = portal_headers(user), portal_headers(admin)

    async def may_do() -> tuple[bool, bool, str]:
        """Whether the user gets past the barista guard and past the admin guard, and
        the role `/me` reports."""
        at_the_bar = await client.post(BARISTA_ROUTE, json={}, headers=headers)
        in_the_panel = await client.post(approve_a_market(), json={}, headers=headers)
        me = await client.get(ME, headers=headers)
        return at_the_bar.status_code == 422, in_the_panel.status_code == 422, me.json()["role"]

    assert await may_do() == (False, False, "USER")
    for role, rights in (
        (UserRole.BARISTA, (True, False, "BARISTA")),
        (UserRole.ADMIN, (True, True, "ADMIN")),
        (UserRole.USER, (False, False, "USER")),
    ):
        path, body = set_role(user, role)
        changed = await client.post(path, json=body, headers=as_admin)
        assert (changed.status_code, changed.json()["role"]) == (200, role.value), changed.text
        assert await may_do() == rights
    await assert_books_balance(container.uow_factory)


# --- Upkeep must not stand between a caller and the application --------------------------------


async def test_a_caller_whose_account_is_locked_is_let_in_without_the_upkeep(
    settings: Settings,
    clock: FrozenClock,
    factory: Factory,
    owner_engine: AsyncEngine,
    portal_headers: PortalHeaders,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Through the application's own wiring. The first request of a user starts their
    upkeep, which waits for the lock on their account. Somebody else holds it for
    longer than the lock timeout: the request is not answered 503 for that, it goes on
    without its upkeep, and a warning says so."""
    user = await factory.user()
    postgres = settings.postgres.model_copy(update={"lock_timeout_ms": IMPATIENT_MS})
    impatient = Container.build(settings.model_copy(update={"postgres": postgres}), clock=clock)
    origin = settings.app.public_base_url
    transport = httpx.ASGITransport(app=create_app(impatient))
    try:
        async with (
            httpx.AsyncClient(transport=transport, base_url=origin) as client,
            owner_engine.connect() as holder,
        ):
            await holder.execute(
                text("SELECT id FROM accounts WHERE owner_id = :user_id FOR UPDATE"),
                {"user_id": user.id},
            )
            with caplog.at_level(logging.WARNING, logger=UPKEEP_LOGGER):
                response = await client.get(NOT_A_MARKET, headers=portal_headers(user))
    finally:
        await impatient.stop()

    assert_let_in(response)
    (record,) = [record for record in caplog.records if record.name == UPKEEP_LOGGER]
    assert record.levelno == logging.WARNING
    assert str(user.id) in record.getMessage()


# --- Postgres down ------------------------------------------------------------------------


@pytest.mark.postgres_down
async def test_with_postgres_down_who_is_calling_cannot_be_looked_up_and_the_answer_is_503(
    client: httpx.AsyncClient, settings: Settings, portal_headers: PortalHeaders
) -> None:
    """The lookup loses its connection and nothing catches it (SPEC §9). The two
    answers that need no lookup stay what they are."""
    headers = portal_headers(NEWCOMER)

    for method, path in (*MEMBER_OPERATIONS, ("PUT", PROFILE)):
        response = await client.request(method, path, headers=headers)
        assert assert_error(response, 503, "SERVICE_UNAVAILABLE") == {}

    assert_error(await client.get(ME), 401, "UNAUTHENTICATED")
    foreign = {settings.identity.header_user_id: "someone@uzh.ch"}
    assert_error(await client.get(ME, headers=foreign), 403, "FORBIDDEN")


# --- `IdentityService`, asked directly ----------------------------------------------------


class RecordedUpkeep(UserUpkeepService):
    """Stands in for `UserUpkeepService`: remembers every row it was asked to look
    after and how many transactions were open at that moment, takes a moment as the
    real one does, and then fails if a test told it to. It is built with none of what
    the real service is built from: `run_if_due` is all it has to offer."""

    def __init__(self, owner_engine: AsyncEngine) -> None:
        self._owner_engine = owner_engine
        self.asked: list[User] = []
        self.transactions_open: list[int] = []
        self.finished = 0
        self.failure: Exception | None = None

    async def run_if_due(self, user: User) -> None:
        self.asked.append(user)
        self.transactions_open.append(await transactions_left_open(self._owner_engine))
        await asyncio.sleep(0)
        if self.failure is not None:
            raise self.failure
        self.finished += 1


async def transactions_left_open(owner_engine: AsyncEngine) -> int:
    async with owner_engine.connect() as observer:
        count: int = await observer.scalar(text(TRANSACTIONS_LEFT_OPEN))
    return count


def no_unit_of_work() -> UnitOfWork:
    raise AssertionError("an id that was refused reached Postgres")


@pytest.fixture
def upkeep(owner_engine: AsyncEngine) -> RecordedUpkeep:
    return RecordedUpkeep(owner_engine)


@pytest.fixture
def identity(container: Container, upkeep: RecordedUpkeep) -> IdentityService:
    """The identity service as the container builds it, around the stand-in."""
    return IdentityService(container.uow_factory, container.email_policy, upkeep)


@pytest.fixture
def stood_in_client(
    client: httpx.AsyncClient,
    container: Container,
    identity: IdentityService,
    monkeypatch: pytest.MonkeyPatch,
) -> httpx.AsyncClient:
    """`client`, on an application whose identity service starts the stand-in."""
    monkeypatch.setattr(container, "identity", identity)
    return client


async def test_a_registered_caller_is_the_row_with_the_role_it_has_in_effect(
    container: Container, factory: Factory
) -> None:
    """Through the application's own wiring, its upkeep included. The role is the
    row's own, and ADMIN for the two bootstrap admins, whose rows say USER."""
    student, barista = await factory.user(), await factory.barista()
    admin, second_admin = await factory.admin(), await factory.admin(1)
    made_admin = await factory.user(role=UserRole.ADMIN)
    assert admin.role is second_admin.role is UserRole.USER

    for row, role in (
        (student, UserRole.USER),
        (barista, UserRole.BARISTA),
        (made_admin, UserRole.ADMIN),
        (admin, UserRole.ADMIN),
        (second_admin, UserRole.ADMIN),
    ):
        caller = await container.identity.authenticate(row.email, row.name)

        assert caller == Caller(
            identity=PortalIdentity(email=row.email, name=row.name, role=role),
            user=AuthenticatedUser(
                id=row.id,
                email=row.email,
                name=row.name,
                username=row.username,
                degree_programme=row.degree_programme,
                role=role,
                status=UserStatus.ACTIVE,
            ),
        )


async def test_the_identity_carries_the_name_the_portal_sends_now_and_the_user_the_name_kept(
    identity: IdentityService, factory: Factory
) -> None:
    row = await factory.user(name="Zoe Muster")

    caller = await identity.authenticate(row.email, "Zoe Muster-Meier")

    assert caller.identity.name == "Zoe Muster-Meier"
    assert caller.user is not None
    assert caller.user.name == "Zoe Muster"


@pytest.mark.parametrize("name", ["%00", "  two  spaces ", "Ada%20Admin", ""])
async def test_the_name_is_taken_as_it_is(
    identity: IdentityService, factory: Factory, name: str
) -> None:
    """`authenticate_request` has made the display name of the header. Reading it a
    second time would decode it a second time."""
    row = await factory.user()

    registered = await identity.authenticate(row.email, name)
    newcomer = await identity.authenticate(NEWCOMER.email, name)

    assert registered.identity.name == name
    assert newcomer.identity.name == name


@pytest.mark.parametrize(
    ("address", "role"),
    [
        (NEWCOMER.email, UserRole.USER),
        (TEST_ADMIN_EMAILS[0], UserRole.ADMIN),
        (TEST_ADMIN_EMAILS[1], UserRole.ADMIN),
    ],
)
async def test_a_caller_without_a_row_is_an_identity_without_a_user_and_starts_no_upkeep(
    identity: IdentityService,
    upkeep: RecordedUpkeep,
    factory: Factory,
    address: str,
    role: UserRole,
) -> None:
    """A bootstrap admin is one before it has a profile, and everybody else is a plain
    user until a row says otherwise. The guards make nothing of either before the
    profile exists."""
    await factory.user()  # somebody else has a profile; this caller has none

    caller = await identity.authenticate(address.upper(), "Nora Newcomer")

    assert caller == Caller(
        identity=PortalIdentity(email=address, name="Nora Newcomer", role=role), user=None
    )
    assert upkeep.asked == []


async def test_a_suspended_row_is_returned_with_its_status_and_upkeep_runs_for_it(
    identity: IdentityService, upkeep: RecordedUpkeep, factory: Factory
) -> None:
    """The guards refuse it; a refund is owed whatever the status."""
    row = await factory.user(email=TEST_ADMIN_EMAILS[0], status=UserStatus.SUSPENDED)

    caller = await identity.authenticate(row.email, row.name)

    assert caller.user is not None
    assert (caller.user.id, caller.user.status) == (row.id, UserStatus.SUSPENDED)
    assert (caller.user.role, caller.identity.role) == (UserRole.ADMIN, UserRole.ADMIN)
    assert not caller.user.is_admin
    assert [(asked.id, asked.status) for asked in upkeep.asked] == [(row.id, UserStatus.SUSPENDED)]


async def test_upkeep_is_asked_on_every_request_of_a_registered_caller_with_the_row(
    identity: IdentityService, upkeep: RecordedUpkeep, factory: Factory
) -> None:
    """How often a run starts is upkeep's own to decide, so it has to hear of every
    request. It gets the row as Postgres has it."""
    alice, bob = await factory.user(), await factory.user()

    for row in (alice, alice, bob, alice):
        await identity.authenticate(row.email, "")

    assert [asked.id for asked in upkeep.asked] == [alice.id, alice.id, bob.id, alice.id]
    first = upkeep.asked[0]
    assert (first.email, first.username, first.status, first.created_at) == (
        alice.email,
        alice.username,
        alice.status,
        alice.created_at,
    )


async def test_upkeep_starts_after_the_lookup_has_ended_its_transaction(
    identity: IdentityService,
    upkeep: RecordedUpkeep,
    container: Container,
    factory: Factory,
    owner_engine: AsyncEngine,
) -> None:
    """A run opens a transaction of its own. A request that still held the one of the
    lookup would need two connections at once."""
    row = await factory.user()
    at_rest = await transactions_left_open(owner_engine)
    async with container.uow_factory() as uow:
        await uow.users.get_by_email(row.email)
        assert await transactions_left_open(owner_engine) == at_rest + 1, "the probe sees one"

    await identity.authenticate(row.email, row.name)

    assert upkeep.transactions_open == [at_rest]


async def test_a_request_waits_for_its_upkeep(
    identity: IdentityService, upkeep: RecordedUpkeep, factory: Factory
) -> None:
    """Inline, not in the background: the request that starts a run sees its result."""
    row = await factory.user()

    await identity.authenticate(row.email, row.name)

    assert upkeep.finished == 1


async def test_an_id_the_policy_refuses_reaches_neither_postgres_nor_upkeep(
    container: Container, upkeep: RecordedUpkeep, settings: Settings
) -> None:
    identity = IdentityService(no_unit_of_work, container.email_policy, upkeep)

    with pytest.raises(ForbiddenError) as refused:
        await identity.authenticate("someone@uzh.ch", "Some One")

    assert refused.value.details == {"allowed_domains": settings.identity.allowed_email_domains}
    assert upkeep.asked == []


class Unforeseen(Exception):
    """Stands in for whatever upkeep might let through."""


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionResetError("reset by peer"),
        InterfaceError("SELECT 1", {}, Exception("gone"), connection_invalidated=True),
        Unforeseen("not for this service to judge"),
    ],
    ids=["connection-reset", "connection-lost", "unforeseen"],
)
async def test_whatever_upkeep_raises_leaves_authenticate_as_it_is(
    identity: IdentityService, upkeep: RecordedUpkeep, factory: Factory, failure: Exception
) -> None:
    """Nothing is caught here. Which failures of a run a request gets to see is
    decided in one place, `UserUpkeepService.run_if_due`."""
    row = await factory.user()
    upkeep.failure = failure

    with pytest.raises(type(failure)) as raised:
        await identity.authenticate(row.email, row.name)

    assert raised.value is failure


async def test_a_connection_lost_during_upkeep_is_503_for_the_request(
    stood_in_client: httpx.AsyncClient,
    upkeep: RecordedUpkeep,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    user = await factory.user()
    upkeep.failure = InterfaceError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)
    assert is_unavailable(upkeep.failure)

    for method, path in (("GET", ME), ("PUT", PROFILE), ("GET", ADMIN_ROUTE)):
        response = await stood_in_client.request(method, path, headers=portal_headers(user))
        assert assert_error(response, 503, "SERVICE_UNAVAILABLE") == {}

    # Postgres is back: the same request is answered.
    upkeep.failure = None
    assert_let_in(await stood_in_client.get(NOT_A_MARKET, headers=portal_headers(user)))


async def test_upkeep_is_asked_once_per_request_however_many_guards_the_route_has(
    stood_in_client: httpx.AsyncClient,
    upkeep: RecordedUpkeep,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    """An admin route sits behind three guards that all rest on one lookup."""
    admin = await factory.admin()

    assert_let_in(
        await stood_in_client.post(approve_a_market(), json={}, headers=portal_headers(admin))
    )

    assert [asked.id for asked in upkeep.asked] == [admin.id]
