"""`AdminUserService`: the users as an admin manages them (SPEC §3, §10, Appendix A.3,
A.5, A.8).

An admin lists the users, suspends an account and lifts a suspension, gives and takes
roles, and credits or debits a balance. Every change writes its audit row in the
transaction of the change; a refusal writes nothing at all. The adjustment is the one
action here that moves credits: one `ADMIN_ADJUSTMENT` entry between `vis:issuance` and
the user, booked once however often the request is sent. It adds at most
`MAX_ADMIN_ADJUSTMENT_CR` to a balance and takes at most what the balance holds.

Two bootstrap admins are in the test settings (`Factory.admin`). An admin by the row
alone is `Factory.user(role=UserRole.ADMIN)`. What a suspension and a role mean to the
user's own requests is `tests/integration/test_identity.py`, through the portal's
headers.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import event, select

from app.container import Container
from app.core.enums import AccountKind, DegreeLevel, JournalKind, UserRole, UserStatus
from app.core.errors import (
    ConflictError,
    ErrorCode,
    ForbiddenError,
    IdempotencyKeyReusedError,
    InsufficientBalanceError,
    LimitExceededError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.security import sha256_hex
from app.core.units import MICRO, format_cr, to_cr
from app.db.base import Base
from app.models import AuditLogEntry, IdempotencyKey, JournalEntry, Posting, User
from app.schemas.admin.users import (
    AdjustBalanceRequest,
    AdminUserItem,
    AdminUserListQuery,
    BalanceAdjustmentResponse,
    ReinstateUserRequest,
    SetUserRoleRequest,
    SuspendUserRequest,
)
from app.schemas.common import Limit, PageQuery
from app.schemas.me import LedgerListResponse
from app.services.account import ledger_description
from app.services.admin_users import (
    ALREADY_SUSPENDED_MESSAGE,
    BOOTSTRAP_ROLE_MESSAGE,
    BOOTSTRAP_SUSPEND_MESSAGE,
    DEBIT_ABOVE_BALANCE_MESSAGE,
    NOT_SUSPENDED_MESSAGE,
    OWN_ACCOUNT_MESSAGE,
    AdminUserService,
    adjustment_limit_message,
)
from app.services.audit import AuditAction
from app.services.ledger import LedgerRef, admin_adjustment_key
from app.services.markets import UNSTORABLE_TEXT_MESSAGE
from tests.support.books import assert_books_balance, count_rows, system_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory, PortalHeaders, identity_of
from tests.support.settings import TEST_ADMIN_EMAILS, load_base_settings

pytestmark = pytest.mark.integration

USERS: Final[str] = "/api/admin/users"

WALLET: Final[int] = 20 * MICRO
"""What a user whose balance is adjusted here starts with."""

CAP: Final[int] = load_base_settings().risk.max_admin_adjustment_micro
"""MAX_ADMIN_ADJUSTMENT_CR in µCR: the most one adjustment adds to a balance."""

REASON: Final[str] = "Won the quiz at the autumn party."
NUL: Final[str] = "\x00"

type Rows = dict[str, list[tuple[object, ...]]]

type Action = Callable[[Container, AuthenticatedUser, UUID], Awaitable[object]]
"""One of the four actions, with a request that would go through."""


async def everything(container: Container) -> Rows:
    """Every row of every table. Two readings are equal exactly when nothing was
    written in between."""
    found: Rows = {}
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            rows = await uow.session.execute(select(table).order_by(*table.primary_key.columns))
            found[table.name] = [tuple(row) for row in rows]
    return found


async def audit_rows(container: Container) -> list[AuditLogEntry]:
    async with container.uow_factory() as uow:
        statement = select(AuditLogEntry).order_by(AuditLogEntry.id)
        return list((await uow.session.scalars(statement)).all())


async def adjustments(container: Container) -> list[JournalEntry]:
    """The `ADMIN_ADJUSTMENT` entries, in the order they were booked."""
    async with container.uow_factory() as uow:
        statement = (
            select(JournalEntry)
            .where(JournalEntry.kind == JournalKind.ADMIN_ADJUSTMENT)
            .order_by(JournalEntry.id)
        )
        return list((await uow.session.scalars(statement)).all())


async def postings_of(container: Container, entry: JournalEntry) -> dict[UUID, int]:
    """The legs of an entry: µCR by account."""
    async with container.uow_factory() as uow:
        statement = select(Posting).where(Posting.entry_id == entry.id)
        return {
            posting.account_id: posting.amount_micro
            for posting in (await uow.session.scalars(statement)).all()
        }


async def account_id_of(container: Container, user: User) -> UUID:
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id)
    assert account is not None
    return account.id


async def stored(container: Container, user: User) -> User:
    """The row of `user` as it is now."""
    async with container.uow_factory() as uow:
        row = await uow.users.get(user.id)
    assert row is not None
    return row


def adjustment(amount_micro: int, reason: str = REASON) -> AdjustBalanceRequest:
    return AdjustBalanceRequest(amount_cr=to_cr(amount_micro), reason=reason)


async def adjust(
    container: Container,
    admin: AuthenticatedUser,
    user: User,
    amount_micro: int,
    *,
    key: UUID | None = None,
    reason: str = REASON,
) -> BalanceAdjustmentResponse:
    """An adjustment under `key`, or under a key of its own."""
    return await container.admin_users.adjust_balance(
        admin, user.id, key or uuid7(), adjustment(amount_micro, reason)
    )


async def list_users(container: Container, **query: Any) -> list[AdminUserItem]:
    """The whole list under a query: page after page until there is no next one."""
    found: list[AdminUserItem] = []
    cursor: str | None = None
    while True:
        page = await container.admin_users.list_users(
            AdminUserListQuery(**(query | ({"cursor": cursor} if cursor else {})))
        )
        found.extend(page.items)
        if page.next_cursor is None:
            return found
        cursor = page.next_cursor


def usernames(items: Sequence[AdminUserItem]) -> list[str]:
    return [item.username for item in items]


@contextmanager
def recorded_statements(container: Container) -> Iterator[list[str]]:
    """The SQL the application sends to Postgres inside the block, in the order sent."""
    statements: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    engine = container.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def suspend(container: Container, admin: AuthenticatedUser, user_id: UUID) -> object:
    return await container.admin_users.suspend(admin, user_id, SuspendUserRequest(reason=REASON))


async def reinstate(container: Container, admin: AuthenticatedUser, user_id: UUID) -> object:
    return await container.admin_users.reinstate(
        admin, user_id, ReinstateUserRequest(reason=REASON)
    )


async def make_barista(container: Container, admin: AuthenticatedUser, user_id: UUID) -> object:
    return await container.admin_users.set_role(
        admin, user_id, SetUserRoleRequest(role=UserRole.BARISTA)
    )


async def credit(container: Container, admin: AuthenticatedUser, user_id: UUID) -> object:
    return await container.admin_users.adjust_balance(admin, user_id, uuid7(), adjustment(MICRO))


ACTIONS: Final[dict[str, Action]] = {
    "suspend": suspend,
    "reinstate": reinstate,
    "role": make_barista,
    "adjustments": credit,
}
"""The four actions that change something, by the last part of their path."""

BODIES: Final[dict[str, dict[str, str]]] = {
    "suspend": {"reason": REASON},
    "reinstate": {"reason": REASON},
    "role": {"role": "BARISTA"},
    "adjustments": {"amount_cr": "1", "reason": REASON},
}
"""A body each of their routes takes."""

every_action = pytest.mark.parametrize("action", ACTIONS)


async def call(
    client: httpx.AsyncClient,
    action: str,
    user_id: UUID,
    body: dict[str, Any] | None = None,
    **headers: str,
) -> httpx.Response:
    """One of the four routes, with a fresh Idempotency-Key where the route takes one."""
    if action == "adjustments":
        headers = {"Idempotency-Key": str(uuid7())} | headers
    return await client.post(
        f"{USERS}/{user_id}/{action}",
        json=BODIES[action] if body is None else body,
        headers=headers,
    )


# --- The list ----------------------------------------------------------------------------


async def test_the_list_shows_every_user_newest_first_with_role_status_and_balance(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = await factory.user(
        balance_micro=12_500_000,
        email="alice@ethz.ch",
        username="alice",
        name="Alice Muster",
        degree_level=DegreeLevel.OTHER,
        degree_programme="ETH staff",
    )
    clock.advance(timedelta(hours=1))
    barista = await factory.barista()
    suspended = await factory.user(status=UserStatus.SUSPENDED)
    admin = await factory.admin()

    page = await container.admin_users.list_users(AdminUserListQuery())

    assert page.next_cursor is None
    assert [item.id for item in page.items] == [admin.id, suspended.id, barista.id, alice.id]
    assert page.items[-1] == AdminUserItem(
        id=alice.id,
        email="alice@ethz.ch",
        name="Alice Muster",
        username="alice",
        degree_level=DegreeLevel.OTHER,
        degree_programme="ETH staff",
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        bootstrap_admin=False,
        balance_cr=to_cr(12_500_000),
        created_at=TEST_NOW,
    )
    assert [(item.role, item.status, item.bootstrap_admin) for item in page.items] == [
        (UserRole.ADMIN, UserStatus.ACTIVE, True),
        (UserRole.USER, UserStatus.SUSPENDED, False),
        (UserRole.BARISTA, UserStatus.ACTIVE, False),
        (UserRole.USER, UserStatus.ACTIVE, False),
    ]
    assert page.items[1].created_at == TEST_NOW + timedelta(hours=1)
    await assert_books_balance(container.uow_factory)


async def test_the_role_an_item_reports_is_the_one_in_effect(
    container: Container, factory: Factory
) -> None:
    """A bootstrap admin is reported as ADMIN though its row says USER, and is marked
    as one the configuration names. An admin the panel made is ADMIN by the row."""
    bootstrap = await factory.admin()
    made_admin = await factory.user(role=UserRole.ADMIN)
    assert bootstrap.role is UserRole.USER

    by_id = {item.id: item for item in await list_users(container)}

    assert (by_id[bootstrap.id].role, by_id[bootstrap.id].bootstrap_admin) == (UserRole.ADMIN, True)
    assert (by_id[made_admin.id].role, by_id[made_admin.id].bootstrap_admin) == (
        UserRole.ADMIN,
        False,
    )


async def test_an_empty_application_has_an_empty_list(container: Container) -> None:
    page = await container.admin_users.list_users(AdminUserListQuery())

    assert (page.items, page.next_cursor) == ([], None)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("muster", ["zoe", "anna"]),
        ("MUSTER", ["zoe", "anna"]),
        ("Anna", ["anna_k", "anna"]),
        ("anna_k", ["anna_k"]),
        ("ANNA.KELLER@ETHZ", ["anna_k"]),
        ("@ethz.ch", ["ben_100", "zoe", "anna_k", "anna"]),
        ("ller", ["zoe", "anna_k"]),
        ("ü", ["zoe"]),
        ("nobody", []),
        ("anna muster", ["anna"]),
        (" anna ", ["anna_k", "anna"]),
    ],
    ids=[
        "in-a-name",
        "in-capitals",
        "in-a-name-and-in-two-usernames",
        "a-whole-username",
        "an-address-in-capitals",
        "what-every-address-holds",
        "the-end-of-two-names",
        "a-letter-outside-ascii",
        "what-nobody-holds",
        "two-words-of-one-name",
        "padded-with-spaces",
    ],
)
async def test_a_text_finds_the_users_whose_username_name_or_address_contains_it(
    container: Container, factory: Factory, text: str, found: list[str]
) -> None:
    """Whatever the case of the letters, and anywhere inside the three."""
    await factory.user(email="anna@ethz.ch", username="anna", name="Anna Muster")
    await factory.user(email="anna.keller@ethz.ch", username="anna_k", name="A. Keller")
    await factory.user(email="zoe@ethz.ch", username="zoe", name="Zoë Müller-Muster")
    await factory.user(email="ben@ethz.ch", username="ben_100", name="Ben 100%")

    assert usernames(await list_users(container, q=text)) == found


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("%", ["percent"]),
        ("100%", ["percent"]),
        ("_", ["under_score"]),
        ("r_s", ["under_score"]),
        ("a_b", []),
        ("/", ["slash"]),
        ("a/b", ["slash"]),
        ("\\", ["backslash"]),
        ("%%", []),
        ("a%b", []),
    ],
    ids=[
        "a-percent-sign",
        "a-number-and-a-percent-sign",
        "an-underscore",
        "an-underscore-between-letters",
        "an-underscore-where-a-name-has-another-letter",
        "a-slash",
        "a-slash-between-letters",
        "a-backslash",
        "two-percent-signs",
        "a-percent-sign-between-letters",
    ],
)
async def test_the_text_is_taken_literally(
    container: Container, factory: Factory, text: str, found: list[str]
) -> None:
    """`%` and `_` are what a pattern matches anything with. Here they stand for
    themselves, so a search for `%` does not list everybody."""
    await factory.user(username="percent", name="Ben 100% sure")
    await factory.user(username="under_score", name="Una")
    await factory.user(username="slash", name="a/b tester")
    await factory.user(username="backslash", name="back\\slash")
    await factory.user(username="plain", name="axb arsb")

    assert usernames(await list_users(container, q=text)) == found


async def test_a_text_with_a_nul_character_finds_nobody_and_asks_the_database_nothing(
    container: Container, factory: Factory
) -> None:
    """A query string can carry one, no column holds one, and Postgres refuses even to
    compare with it."""
    await factory.user()

    with recorded_statements(container) as statements:
        page = await container.admin_users.list_users(AdminUserListQuery(q=f"us{NUL}er"))

    assert (page.items, page.next_cursor) == ([], None)
    assert statements == []


async def test_the_role_filter_is_about_the_role_in_effect(
    container: Container, factory: Factory
) -> None:
    """ADMIN lists the bootstrap admins beside the admins the panel made. No other role
    lists a bootstrap admin, whatever its row says: the row of one says USER here, the
    row of the other BARISTA."""
    await factory.user(username="student")
    await factory.barista()
    await factory.user(username="made_admin", role=UserRole.ADMIN)
    first = await factory.admin()
    second = await factory.user(
        email=TEST_ADMIN_EMAILS[1], username="admin1", role=UserRole.BARISTA
    )
    assert (first.role, second.role) == (UserRole.USER, UserRole.BARISTA)

    assert usernames(await list_users(container, role="ADMIN")) == [
        "admin1",
        "admin0",
        "made_admin",
    ]
    assert usernames(await list_users(container, role="BARISTA")) == ["barista0"]
    assert usernames(await list_users(container, role="USER")) == ["student"]
    everybody = await list_users(container)
    by_role = [await list_users(container, role=role.value) for role in UserRole]
    assert sorted(usernames(everybody)) == sorted(
        name for part in by_role for name in usernames(part)
    )


async def test_where_the_configuration_names_no_admin_the_role_filter_is_about_the_rows_alone(
    container: Container, factory: Factory
) -> None:
    """The query takes the addresses that are admins whatever their row says. With
    none of them, every role lists the rows that say so."""
    admin = await factory.admin()
    made_admin = await factory.user(role=UserRole.ADMIN)
    barista = await factory.barista()
    found: dict[UserRole, list[UUID]] = {}

    async with container.uow_factory() as uow:
        for role in UserRole:
            rows = await uow.users.search(
                text=None,
                role=role,
                status=None,
                always_admin=frozenset(),
                before_id=None,
                limit=10,
            )
            found[role] = [row.user.id for row in rows]

    assert found == {
        UserRole.USER: [admin.id],
        UserRole.ADMIN: [made_admin.id],
        UserRole.BARISTA: [barista.id],
    }


async def test_the_status_filter_and_the_others_must_all_hold(
    container: Container, factory: Factory
) -> None:
    await factory.user(username="anna")
    await factory.user(username="anna_out", status=UserStatus.SUSPENDED)
    await factory.user(username="bar_anna", role=UserRole.BARISTA)
    await factory.user(username="bar_out", role=UserRole.BARISTA, status=UserStatus.SUSPENDED)
    await factory.user(username="ben")

    assert usernames(await list_users(container, status="SUSPENDED")) == ["bar_out", "anna_out"]
    assert usernames(await list_users(container, status="ACTIVE")) == ["ben", "bar_anna", "anna"]
    assert usernames(await list_users(container, q="anna", status="ACTIVE")) == ["bar_anna", "anna"]
    assert usernames(await list_users(container, q="anna", role="BARISTA")) == ["bar_anna"]
    assert usernames(await list_users(container, role="BARISTA", status="SUSPENDED")) == ["bar_out"]
    assert await list_users(container, q="ben", role="BARISTA") == []
    assert await list_users(container, q="out", role="USER", status="ACTIVE") == []


async def test_the_list_comes_in_pages_that_join_without_a_gap_or_a_repeat(
    container: Container, factory: Factory
) -> None:
    users = [await factory.user() for _ in range(7)]
    newest_first = [user.id for user in reversed(users)]

    first = await container.admin_users.list_users(AdminUserListQuery(limit=3))
    second = await container.admin_users.list_users(
        AdminUserListQuery(limit=3, cursor=first.next_cursor)
    )
    third = await container.admin_users.list_users(
        AdminUserListQuery(limit=3, cursor=second.next_cursor)
    )

    assert [item.id for item in first.items] == newest_first[:3]
    assert [item.id for item in second.items] == newest_first[3:6]
    assert [item.id for item in third.items] == newest_first[6:]
    assert (first.next_cursor, second.next_cursor) == (str(newest_first[2]), str(newest_first[5]))
    assert third.next_cursor is None
    exactly = await container.admin_users.list_users(AdminUserListQuery(limit=7))
    assert (len(exactly.items), exactly.next_cursor) == (7, None), "no empty page follows"


async def test_somebody_who_registers_between_two_pages_shifts_neither(
    container: Container, factory: Factory
) -> None:
    """The cursor is the user a page ended with, not a count: whoever is newer than
    the first page belongs before it and turns up on no later one."""
    users = [await factory.user() for _ in range(4)]
    first = await container.admin_users.list_users(AdminUserListQuery(limit=2))

    newcomer = await factory.user()
    second = await container.admin_users.list_users(
        AdminUserListQuery(limit=2, cursor=first.next_cursor)
    )

    assert [item.id for item in first.items] == [users[3].id, users[2].id]
    assert [item.id for item in second.items] == [users[1].id, users[0].id]
    assert second.next_cursor is None
    again = await container.admin_users.list_users(AdminUserListQuery(limit=2))
    assert [item.id for item in again.items] == [newcomer.id, users[3].id]


async def test_the_pages_of_a_filtered_list_join_too(
    container: Container, factory: Factory
) -> None:
    for number in range(9):
        role = UserRole.BARISTA if number % 2 else UserRole.USER
        await factory.user(username=f"anna_{number}", role=role)

    baristas = await list_users(container, q="anna", role="BARISTA", limit=1)
    everybody = await list_users(container, limit=4)

    assert usernames(baristas) == ["anna_7", "anna_5", "anna_3", "anna_1"]
    assert usernames(everybody) == [f"anna_{number}" for number in reversed(range(9))]


@pytest.mark.parametrize("cursor", ["page-2", "17", "0199c1d4-7a55-7f0e-9a53", "none"])
async def test_a_cursor_the_list_never_handed_out_is_422_on_the_cursor(
    container: Container, factory: Factory, cursor: str
) -> None:
    await factory.user()

    with pytest.raises(ValidationFailedError) as refused:
        await container.admin_users.list_users(AdminUserListQuery(cursor=cursor))

    assert (refused.value.status_code, refused.value.code) == (422, ErrorCode.VALIDATION_ERROR)
    assert refused.value.details == {"field": "cursor"}


async def test_a_page_is_one_statement_however_many_users_it_holds(
    container: Container, factory: Factory
) -> None:
    """The balance comes with the user, from one join: no query per row."""
    for _ in range(12):
        await factory.user(balance_micro=MICRO)

    with recorded_statements(container) as statements:
        page = await container.admin_users.list_users(AdminUserListQuery(q="user", limit=10))

    assert len(page.items) == 10
    selects = [statement for statement in statements if statement.startswith("SELECT")]
    assert len(selects) == 1, statements
    assert "JOIN accounts" in selects[0]


async def test_reading_the_list_writes_nothing(container: Container, factory: Factory) -> None:
    """No audit row either: a list is no change."""
    await factory.admin()
    await factory.user(balance_micro=WALLET)
    before = await everything(container)

    await list_users(container)
    await list_users(container, q="user", role="USER", status="ACTIVE", limit=1)

    assert await everything(container) == before


# --- Suspending and reinstating ----------------------------------------------------------


async def test_suspending_sets_the_status_and_writes_the_audit_row_with_the_reason(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)
    clock.advance(timedelta(minutes=5))

    answer = await container.admin_users.suspend(
        factory.identity(admin), user.id, SuspendUserRequest(reason="  Sold a voucher.  ")
    )

    assert (answer.id, answer.status, answer.role) == (user.id, UserStatus.SUSPENDED, UserRole.USER)
    assert answer.balance_cr == to_cr(WALLET)
    assert (await stored(container, user)).status is UserStatus.SUSPENDED
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action) == (admin.id, AuditAction.USER_SUSPEND)
    assert audited.target == f"user:{user.id}"
    assert audited.before == {"status": "ACTIVE"}
    assert audited.after == {"status": "SUSPENDED", "reason": "Sold a voucher."}
    assert audited.created_at == clock.now()
    assert await factory.balance_micro(user) == WALLET, "a suspension moves no credit"
    await assert_books_balance(container.uow_factory)


async def test_reinstating_lifts_the_suspension_and_writes_its_own_audit_row(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(status=UserStatus.SUSPENDED)

    answer = await container.admin_users.reinstate(
        admin, user.id, ReinstateUserRequest(reason="Cleared.")
    )

    assert (answer.id, answer.status) == (user.id, UserStatus.ACTIVE)
    assert (await stored(container, user)).status is UserStatus.ACTIVE
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action) == (admin.id, AuditAction.USER_REINSTATE)
    assert (audited.target, audited.before) == (f"user:{user.id}", {"status": "SUSPENDED"})
    assert audited.after == {"status": "ACTIVE", "reason": "Cleared."}


async def test_an_account_can_be_suspended_and_reinstated_again_and_again(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user()

    for _ in range(3):
        await suspend(container, admin, user.id)
        assert (await stored(container, user)).status is UserStatus.SUSPENDED
        await reinstate(container, admin, user.id)
        assert (await stored(container, user)).status is UserStatus.ACTIVE

    assert [row.action for row in await audit_rows(container)] == [
        AuditAction.USER_SUSPEND,
        AuditAction.USER_REINSTATE,
    ] * 3


async def test_suspending_a_suspended_account_is_409_and_writes_nothing(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(status=UserStatus.SUSPENDED)
    before = await everything(container)

    with pytest.raises(ConflictError) as refused:
        await suspend(container, admin, user.id)

    assert (refused.value.status_code, refused.value.code) == (409, ErrorCode.CONFLICT)
    assert refused.value.message == ALREADY_SUSPENDED_MESSAGE
    assert await everything(container) == before


async def test_reinstating_an_active_account_is_409_and_writes_nothing(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user()
    before = await everything(container)

    with pytest.raises(ConflictError) as refused:
        await reinstate(container, admin, user.id)

    assert (refused.value.status_code, refused.value.message) == (409, NOT_SUSPENDED_MESSAGE)
    assert await everything(container) == before


async def test_of_two_admins_who_suspend_one_account_at_once_one_does_and_one_gets_409(
    container: Container, factory: Factory
) -> None:
    """The second waits for the first on the row and then finds the account suspended:
    one change, one audit row."""
    first, second = (
        factory.identity(await factory.admin()),
        factory.identity(await factory.admin(1)),
    )
    user = await factory.user()

    outcomes = await asyncio.gather(
        suspend(container, first, user.id),
        suspend(container, second, user.id),
        return_exceptions=True,
    )

    done = [outcome for outcome in outcomes if isinstance(outcome, AdminUserItem)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, ConflictError)]
    assert (len(done), len(refused)) == (1, 1), outcomes
    assert refused[0].message == ALREADY_SUSPENDED_MESSAGE
    assert (await stored(container, user)).status is UserStatus.SUSPENDED
    assert [row.action for row in await audit_rows(container)] == [AuditAction.USER_SUSPEND]


# --- Roles -------------------------------------------------------------------------------


async def test_a_role_is_given_taken_and_given_again_and_every_change_is_audited(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    changes = (UserRole.BARISTA, UserRole.ADMIN, UserRole.USER)

    answers = [
        await container.admin_users.set_role(admin, user.id, SetUserRoleRequest(role=role))
        for role in changes
    ]

    assert [answer.role for answer in answers] == list(changes)
    assert all(answer.bootstrap_admin is False for answer in answers)
    assert (await stored(container, user)).role is UserRole.USER
    audited = await audit_rows(container)
    assert [(row.actor_id, row.action, row.target) for row in audited] == [
        (admin.id, AuditAction.USER_SET_ROLE, f"user:{user.id}")
    ] * 3
    assert [(row.before, row.after) for row in audited] == [
        ({"role": "USER"}, {"role": "BARISTA"}),
        ({"role": "BARISTA"}, {"role": "ADMIN"}),
        ({"role": "ADMIN"}, {"role": "USER"}),
    ]
    assert await factory.balance_micro(user) == WALLET
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("role", list(UserRole))
async def test_setting_the_role_an_account_already_has_changes_and_audits_nothing(
    container: Container, factory: Factory, role: UserRole
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(role=role)
    before = await everything(container)

    answer = await container.admin_users.set_role(admin, user.id, SetUserRoleRequest(role=role))

    assert (answer.id, answer.role) == (user.id, role)
    assert await everything(container) == before


async def test_an_admin_the_panel_made_manages_users_and_one_who_lost_the_role_does_not(
    container: Container, factory: Factory
) -> None:
    """The role is the row's: whoever has it acts, whoever had it is refused. The caller
    is who the application says they are, read again for every request."""
    bootstrap = factory.identity(await factory.admin())
    promoted, user = await factory.user(), await factory.user()
    await container.admin_users.set_role(
        bootstrap, promoted.id, SetUserRoleRequest(role=UserRole.ADMIN)
    )
    as_admin = factory.identity(await stored(container, promoted))
    assert as_admin.role is UserRole.ADMIN

    await suspend(container, as_admin, user.id)
    await container.admin_users.set_role(
        bootstrap, promoted.id, SetUserRoleRequest(role=UserRole.USER)
    )
    demoted = factory.identity(await stored(container, promoted))

    with pytest.raises(ForbiddenError) as refused:
        await reinstate(container, demoted, user.id)
    assert refused.value.message == ForbiddenError.default_message
    assert (await stored(container, user)).status is UserStatus.SUSPENDED
    assert [(row.actor_id, row.action) for row in await audit_rows(container)] == [
        (bootstrap.id, AuditAction.USER_SET_ROLE),
        (promoted.id, AuditAction.USER_SUSPEND),
        (bootstrap.id, AuditAction.USER_SET_ROLE),
    ]


async def test_a_suspended_account_can_be_given_a_role(
    container: Container, factory: Factory
) -> None:
    """It has none of the role's rights until it is reinstated."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(status=UserStatus.SUSPENDED)

    answer = await make_barista(container, admin, user.id)

    assert isinstance(answer, AdminUserItem)
    assert (answer.role, answer.status) == (UserRole.BARISTA, UserStatus.SUSPENDED)
    assert not factory.identity(await stored(container, user)).is_staff


# --- Who may act, and on whom ------------------------------------------------------------


@every_action
async def test_no_admin_does_any_of_it_to_their_own_account(
    container: Container, factory: Factory, action: str
) -> None:
    """Whether the admin is one by the address or by the row. Nothing is written, the
    claim on an Idempotency-Key included."""
    bootstrap = await factory.admin(balance_micro=WALLET)
    by_the_row = await factory.user(role=UserRole.ADMIN, status=UserStatus.ACTIVE)
    before = await everything(container)

    for admin in (bootstrap, by_the_row):
        with pytest.raises(ForbiddenError) as refused:
            await ACTIONS[action](container, factory.identity(admin), admin.id)
        assert (refused.value.status_code, refused.value.message) == (403, OWN_ACCOUNT_MESSAGE)

    assert await everything(container) == before


@every_action
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.BARISTA])
async def test_only_an_admin_acts(
    container: Container, factory: Factory, action: str, role: UserRole
) -> None:
    """The routes have a guard of their own. The service holds the rule for whoever
    else calls it."""
    somebody = factory.identity(await factory.user(role=role))
    user = await factory.user(
        status=UserStatus.SUSPENDED if action == "reinstate" else UserStatus.ACTIVE
    )
    before = await everything(container)

    with pytest.raises(ForbiddenError) as refused:
        await ACTIONS[action](container, somebody, user.id)

    assert refused.value.message == ForbiddenError.default_message
    assert await everything(container) == before


@every_action
async def test_a_suspended_admin_acts_on_nobody(
    container: Container, factory: Factory, action: str
) -> None:
    suspended = identity_of(await factory.user(status=UserStatus.SUSPENDED), UserRole.ADMIN)
    user = await factory.user()
    before = await everything(container)

    with pytest.raises(ForbiddenError):
        await ACTIONS[action](container, suspended, user.id)

    assert await everything(container) == before


@every_action
async def test_an_unknown_user_is_404_and_nothing_is_written(
    container: Container, factory: Factory, action: str
) -> None:
    admin = factory.identity(await factory.admin())
    before = await everything(container)

    with pytest.raises(NotFoundError) as refused:
        await ACTIONS[action](container, admin, uuid7())

    assert refused.value.status_code == 404
    assert await everything(container) == before


@pytest.mark.parametrize("by", ["the-other-bootstrap-admin", "an-admin-the-panel-made"])
async def test_nobody_suspends_a_bootstrap_admin_or_changes_its_role(
    container: Container, factory: Factory, by: str
) -> None:
    """An address of `ADMIN_EMAILS` is an admin by the configuration. Each refusal has
    a sentence of its own, and neither writes anything."""
    target = await factory.admin()
    if by == "the-other-bootstrap-admin":
        admin = factory.identity(await factory.admin(1))
    else:
        admin = factory.identity(await factory.user(role=UserRole.ADMIN))
    before = await everything(container)

    with pytest.raises(ForbiddenError) as not_suspended:
        await suspend(container, admin, target.id)
    refused_roles = []
    for role in UserRole:
        with pytest.raises(ForbiddenError) as not_changed:
            await container.admin_users.set_role(admin, target.id, SetUserRoleRequest(role=role))
        refused_roles.append(not_changed.value.message)

    assert (not_suspended.value.status_code, not_suspended.value.message) == (
        403,
        BOOTSTRAP_SUSPEND_MESSAGE,
    )
    assert refused_roles == [BOOTSTRAP_ROLE_MESSAGE] * len(UserRole), "also the role it has"
    assert await everything(container) == before
    assert (await stored(container, target)).role is UserRole.USER


def test_each_of_the_three_refusals_has_a_sentence_of_its_own() -> None:
    sentences = {
        OWN_ACCOUNT_MESSAGE,
        BOOTSTRAP_ROLE_MESSAGE,
        BOOTSTRAP_SUSPEND_MESSAGE,
        ForbiddenError.default_message,
    }

    assert len(sentences) == 4
    assert all(sentence.endswith(".") for sentence in sentences)


async def test_a_bootstrap_admin_whose_row_is_suspended_is_reinstated_like_anybody(
    container: Container, factory: Factory
) -> None:
    """The rule about such accounts is that nobody suspends them. One whose row says
    SUSPENDED, from before `.env` named the address, has no rights until another admin
    lifts it."""
    locked_out = await factory.user(email=TEST_ADMIN_EMAILS[0], status=UserStatus.SUSPENDED)
    other = factory.identity(await factory.admin(1))
    assert not factory.identity(locked_out).is_admin

    answer = await reinstate(container, other, locked_out.id)

    assert isinstance(answer, AdminUserItem)
    assert (answer.status, answer.role, answer.bootstrap_admin) == (
        UserStatus.ACTIVE,
        UserRole.ADMIN,
        True,
    )
    assert factory.identity(await stored(container, locked_out)).is_admin


def with_the_reason(reason: str) -> dict[str, Action]:
    """The three actions that take a reason, each saying `reason`."""

    async def suspend_saying(
        container: Container, admin: AuthenticatedUser, user_id: UUID
    ) -> object:
        return await container.admin_users.suspend(
            admin, user_id, SuspendUserRequest(reason=reason)
        )

    async def reinstate_saying(
        container: Container, admin: AuthenticatedUser, user_id: UUID
    ) -> object:
        return await container.admin_users.reinstate(
            admin, user_id, ReinstateUserRequest(reason=reason)
        )

    async def adjust_saying(
        container: Container, admin: AuthenticatedUser, user_id: UUID
    ) -> object:
        return await container.admin_users.adjust_balance(
            admin, user_id, uuid7(), adjustment(MICRO, reason)
        )

    return {"suspend": suspend_saying, "reinstate": reinstate_saying, "adjustments": adjust_saying}


@pytest.mark.parametrize("action", ["suspend", "reinstate", "adjustments"])
async def test_a_reason_with_a_nul_character_is_422_on_the_reason_and_nothing_is_written(
    container: Container, factory: Factory, action: str
) -> None:
    """Postgres stores no such text, neither in a column nor in the JSON of an audit
    row. Without the check the request would fail in the database."""
    admin = factory.identity(await factory.admin())
    status = UserStatus.SUSPENDED if action == "reinstate" else UserStatus.ACTIVE
    user = await factory.user(status=status)
    act = with_the_reason(f"Sold{NUL} a voucher.")[action]
    before = await everything(container)

    with pytest.raises(ValidationFailedError) as refused:
        await act(container, admin, user.id)

    assert (refused.value.status_code, refused.value.details) == (422, {"field": "reason"})
    assert refused.value.message == UNSTORABLE_TEXT_MESSAGE
    assert await everything(container) == before


# --- Adjusting a balance -----------------------------------------------------------------


async def test_a_credit_is_one_entry_from_issuance_to_the_user(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)
    issued_before = await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE)
    clock.advance(timedelta(minutes=5))
    key = uuid7()

    booked = await adjust(container, factory.identity(admin), user, 7_500_000, key=key)

    assert booked == BalanceAdjustmentResponse(
        user_id=user.id,
        amount_cr=Decimal("7.5"),
        balance_cr=to_cr(WALLET + 7_500_000),
        created_at=clock.now(),
    )
    assert await factory.balance_micro(user) == WALLET + 7_500_000
    (entry,) = await adjustments(container)
    assert (entry.kind, entry.ref_type, entry.ref_id) == (
        JournalKind.ADMIN_ADJUSTMENT,
        LedgerRef.USER,
        user.id,
    )
    assert (entry.idempotency_key, entry.created_at) == (
        admin_adjustment_key(admin.id, key),
        clock.now(),
    )
    issuance = await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE)
    assert issuance == issued_before - 7_500_000
    legs = await postings_of(container, entry)
    assert legs[await account_id_of(container, user)] == 7_500_000
    assert sorted(legs.values()) == [-7_500_000, 7_500_000]
    await assert_books_balance(container.uow_factory)


async def test_a_debit_is_one_entry_from_the_user_back_to_issuance(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    issued_before = await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE)

    booked = await adjust(container, admin, user, -5 * MICRO)

    assert (booked.amount_cr, booked.balance_cr) == (Decimal(-5), to_cr(WALLET - 5 * MICRO))
    assert await factory.balance_micro(user) == WALLET - 5 * MICRO
    (entry,) = await adjustments(container)
    legs = await postings_of(container, entry)
    assert legs[await account_id_of(container, user)] == -5 * MICRO
    assert sorted(legs.values()) == [-5 * MICRO, 5 * MICRO]
    issuance = await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE)
    assert issuance == issued_before + 5 * MICRO
    await assert_books_balance(container.uow_factory)


async def test_an_adjustment_is_audited_with_the_amount_the_reason_and_its_entry(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)

    await adjust(container, factory.identity(admin), user, -1_250_000, reason="  Booked twice.  ")

    (entry,) = await adjustments(container)
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action) == (admin.id, AuditAction.USER_ADJUST_BALANCE)
    assert (audited.target, audited.created_at) == (f"user:{user.id}", clock.now())
    assert audited.before == {"balance_micro": WALLET}
    assert audited.after == {
        "balance_micro": WALLET - 1_250_000,
        "amount_micro": -1_250_000,
        "reason": "Booked twice.",
        "entry_id": str(entry.id),
    }
    await assert_books_balance(container.uow_factory)


async def test_the_users_ledger_shows_an_adjustment_under_its_own_label(
    container: Container, factory: Factory
) -> None:
    """The line says what moved and by how much. The reason is the admins'."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    await adjust(container, admin, user, 3 * MICRO, reason="A secret reason.")
    await adjust(container, admin, user, -MICRO)

    ledger = await container.account.ledger(factory.identity(user), PageQuery())

    assert [(item.kind, item.amount_cr, item.description) for item in ledger.items] == [
        (JournalKind.ADMIN_ADJUSTMENT, to_cr(-MICRO), "Adjustment by VIS"),
        (JournalKind.ADMIN_ADJUSTMENT, to_cr(3 * MICRO), "Adjustment by VIS"),
        (JournalKind.SIGNUP_GRANT, to_cr(WALLET), ledger_description(JournalKind.SIGNUP_GRANT)),
    ]
    assert ledger_description(JournalKind.ADMIN_ADJUSTMENT) == "Adjustment by VIS"
    assert "secret" not in ledger.model_dump_json()
    assert sum(item.amount_cr for item in ledger.items) == to_cr(WALLET + 2 * MICRO)
    await assert_books_balance(container.uow_factory)


async def test_a_debit_can_take_everything_and_not_a_millionth_more(
    container: Container, factory: Factory
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    with pytest.raises(InsufficientBalanceError) as refused:
        await adjust(container, admin, user, -(WALLET + 1))
    emptied = await adjust(container, admin, user, -WALLET)

    assert (refused.value.status_code, refused.value.code) == (
        409,
        ErrorCode.INSUFFICIENT_BALANCE,
    )
    assert refused.value.message == DEBIT_ABOVE_BALANCE_MESSAGE
    assert refused.value.details == {
        "balance_cr": to_cr(WALLET),
        "required_cr": to_cr(WALLET + 1),
    }
    assert emptied.balance_cr == to_cr(0)
    assert await factory.balance_micro(user) == 0
    assert len(await adjustments(container)) == 1
    await assert_books_balance(container.uow_factory)


async def test_a_debit_above_the_balance_writes_nothing(
    container: Container, factory: Factory
) -> None:
    """No entry, no audit row, and no claim on the key: the admin may send it again."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()
    before = await everything(container)

    with pytest.raises(InsufficientBalanceError):
        await adjust(container, admin, user, -2 * WALLET, key=key)

    assert await everything(container) == before
    assert await count_rows(container.uow_factory, IdempotencyKey) == 0

    # The same key and the same request, once the balance covers it.
    await adjust(container, admin, user, WALLET)
    booked = await adjust(container, admin, user, -2 * WALLET, key=key)

    assert booked.balance_cr == to_cr(0)
    await assert_books_balance(container.uow_factory)


async def test_the_same_key_answers_what_the_first_request_answered_and_books_nothing_again(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Also after the balance has moved on: the answer is the stored one."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()
    first = await adjust(container, admin, user, 5 * MICRO, key=key)
    clock.advance(timedelta(hours=2))
    await adjust(container, admin, user, -3 * MICRO)
    after_both = await everything(container)

    again = [await adjust(container, admin, user, 5 * MICRO, key=key) for _ in range(3)]
    other_spelling = await container.admin_users.adjust_balance(
        admin, user.id, key, AdjustBalanceRequest(amount_cr=Decimal("5.000000"), reason=REASON)
    )

    assert again == [first] * 3
    assert other_spelling == first, "one amount written two ways is one request"
    assert (first.balance_cr, first.created_at) == (to_cr(WALLET + 5 * MICRO), TEST_NOW)
    assert await everything(container) == after_both
    assert await factory.balance_micro(user) == WALLET + 2 * MICRO
    assert len(await adjustments(container)) == 2
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "changed",
    ["the-amount", "the-sign", "the-reason", "the-user"],
)
async def test_the_same_key_with_another_request_is_422_and_books_nothing(
    container: Container, factory: Factory, changed: str
) -> None:
    admin = factory.identity(await factory.admin())
    user, somebody_else = await factory.user(balance_micro=WALLET), await factory.user()
    key = uuid7()
    await adjust(container, admin, user, 5 * MICRO, key=key)
    after_the_first = await everything(container)
    request = {
        "the-amount": (user, adjustment(6 * MICRO)),
        "the-sign": (user, adjustment(-5 * MICRO)),
        "the-reason": (user, adjustment(5 * MICRO, "Another reason.")),
        "the-user": (somebody_else, adjustment(5 * MICRO)),
    }[changed]

    with pytest.raises(IdempotencyKeyReusedError) as refused:
        await container.admin_users.adjust_balance(admin, request[0].id, key, request[1])

    assert (refused.value.status_code, refused.value.code) == (
        422,
        ErrorCode.IDEMPOTENCY_KEY_REUSED,
    )
    assert await everything(container) == after_the_first
    await assert_books_balance(container.uow_factory)


async def test_a_key_is_one_admins_own(container: Container, factory: Factory) -> None:
    """Two admins who happen to send one key book one adjustment each, and each gets
    their own back."""
    first, second = (
        factory.identity(await factory.admin()),
        factory.identity(await factory.admin(1)),
    )
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()

    by_the_first = await adjust(container, first, user, 2 * MICRO, key=key)
    by_the_second = await adjust(container, second, user, 3 * MICRO, key=key)

    assert (by_the_first.amount_cr, by_the_second.amount_cr) == (Decimal(2), Decimal(3))
    assert await adjust(container, first, user, 2 * MICRO, key=key) == by_the_first
    assert await adjust(container, second, user, 3 * MICRO, key=key) == by_the_second
    assert await factory.balance_micro(user) == WALLET + 5 * MICRO
    assert [entry.idempotency_key for entry in await adjustments(container)] == [
        admin_adjustment_key(first.id, key),
        admin_adjustment_key(second.id, key),
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_key_the_admin_used_for_a_trade_is_no_key_for_an_adjustment(
    container: Container, factory: Factory
) -> None:
    """The keys of an admin's trades and of their adjustments are claimed in one table.
    A key that stands for a trade is refused, and the trade's stored answer is not
    handed out as an adjustment."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()
    async with container.uow_factory() as uow:
        await uow.idempotency.claim(admin.id, str(key), sha256_hex("a trade"), TEST_NOW)
        await uow.idempotency.store_response(admin.id, str(key), {"trade_id": str(uuid7())})
    before = await everything(container)

    with pytest.raises(IdempotencyKeyReusedError):
        await adjust(container, admin, user, MICRO, key=key)

    assert await everything(container) == before


async def test_requests_under_one_key_that_arrive_together_book_one_adjustment(
    container: Container, factory: Factory
) -> None:
    """A double tap, or a retry that overtakes the first attempt. The later ones wait
    on the claim and get the stored answer."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()

    answers = await asyncio.gather(
        *(adjust(container, admin, user, 4 * MICRO, key=key) for _ in range(6))
    )

    assert len({answer.model_dump_json() for answer in answers}) == 1
    assert answers[0].balance_cr == to_cr(WALLET + 4 * MICRO)
    assert await factory.balance_micro(user) == WALLET + 4 * MICRO
    assert len(await adjustments(container)) == 1
    assert len(await audit_rows(container)) == 1
    await assert_books_balance(container.uow_factory)


async def test_debits_that_arrive_together_never_take_more_than_the_account_holds(
    container: Container, factory: Factory
) -> None:
    """Five debits of 6 against a balance of 20, each under its own key: three go
    through, two are refused, and the balance is what the three left."""
    admins = [factory.identity(await factory.admin()), factory.identity(await factory.admin(1))]
    user = await factory.user(balance_micro=WALLET)

    outcomes = await asyncio.gather(
        *(adjust(container, admins[turn % 2], user, -6 * MICRO) for turn in range(5)),
        return_exceptions=True,
    )

    booked = [outcome for outcome in outcomes if isinstance(outcome, BalanceAdjustmentResponse)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, InsufficientBalanceError)]
    assert (len(booked), len(refused)) == (3, 2), outcomes
    assert sorted(answer.balance_cr for answer in booked) == [
        to_cr(2 * MICRO),
        to_cr(8 * MICRO),
        to_cr(14 * MICRO),
    ]
    assert await factory.balance_micro(user) == 2 * MICRO
    assert len(await adjustments(container)) == len(await audit_rows(container)) == 3
    await assert_books_balance(container.uow_factory)


async def test_an_adjustment_locks_the_users_account_before_it_writes_to_the_ledger(
    container: Container, factory: Factory
) -> None:
    """Lock order: the user's account first, then what `LedgerService.post` locks. A
    grant and a redeemed code take the two in the same order."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    with recorded_statements(container) as statements:
        await adjust(container, admin, user, MICRO)

    locks = [
        index
        for index, statement in enumerate(statements)
        if "FROM accounts" in statement and "FOR NO KEY UPDATE" in statement
    ]
    entries = [
        index
        for index, statement in enumerate(statements)
        if "INSERT INTO journal_entries" in statement
    ]
    assert len(locks) == 2, statements
    assert len(entries) == 1, statements
    assert "accounts.owner_id" in statements[locks[0]], "the user's own account"
    assert "accounts.id IN" in statements[locks[1]], "the accounts of the entry, in id order"
    assert locks[0] < locks[1] < entries[0]
    await assert_books_balance(container.uow_factory)


async def test_a_suspended_account_and_an_admins_account_are_adjusted_like_any_other(
    container: Container, factory: Factory
) -> None:
    """A bootstrap admin's too: what no admin does is adjust their own."""
    admin = factory.identity(await factory.admin())
    suspended = await factory.user(balance_micro=WALLET, status=UserStatus.SUSPENDED)
    other_admin = await factory.admin(1, balance_micro=WALLET)

    taken = await adjust(container, admin, suspended, -WALLET)
    given = await adjust(container, admin, other_admin, 5 * MICRO)

    assert (taken.balance_cr, given.balance_cr) == (to_cr(0), to_cr(WALLET + 5 * MICRO))
    assert (await stored(container, suspended)).status is UserStatus.SUSPENDED
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "amount_micro",
    [1, -1, CAP],
    ids=["a-millionth", "minus-a-millionth", "the-most-one-adjustment-adds"],
)
async def test_the_smallest_and_the_largest_adjustment_are_booked_to_the_millionth(
    container: Container, factory: Factory, amount_micro: int
) -> None:
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    booked = await adjust(container, admin, user, amount_micro)

    assert booked.amount_cr == to_cr(amount_micro)
    assert await factory.balance_micro(user) == WALLET + amount_micro
    await assert_books_balance(container.uow_factory)


# --- The most one adjustment adds ------------------------------------------------------------


async def test_a_credit_above_the_cap_is_refused_before_a_key_is_claimed_or_anything_is_booked(
    container: Container, factory: Factory
) -> None:
    """One millionth above MAX_ADMIN_ADJUSTMENT_CR. The answer names the limit, what was
    asked for and the cap, in a sentence with the figure in tokens. No row of any table
    changes, so the same key takes the amount at the cap right after."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()
    before = await everything(container)

    with pytest.raises(LimitExceededError) as refused:
        await adjust(container, admin, user, CAP + 1, key=key)

    assert (refused.value.status_code, refused.value.code) == (409, ErrorCode.LIMIT_EXCEEDED)
    assert refused.value.message == adjustment_limit_message(CAP)
    assert refused.value.details == {
        "limit": Limit.MAX_ADMIN_ADJUSTMENT_CR,
        "requested_cr": to_cr(CAP + 1),
        "cap_cr": to_cr(CAP),
    }
    assert await everything(container) == before
    assert await count_rows(container.uow_factory, IdempotencyKey) == 0
    assert await adjustments(container) == []

    at_the_cap = await adjust(container, admin, user, CAP, key=key)

    assert (at_the_cap.amount_cr, at_the_cap.balance_cr) == (to_cr(CAP), to_cr(WALLET + CAP))
    assert len(await adjustments(container)) == 1
    await assert_books_balance(container.uow_factory)


def test_the_sentence_of_the_cap_names_the_figure_in_tokens() -> None:
    """The word on screen for a credit is a token, and the figure is grouped as the
    screens group it."""
    assert CAP == 1000 * MICRO, "MAX_ADMIN_ADJUSTMENT_CR of the test settings"
    assert adjustment_limit_message(CAP) == "One adjustment can add at most 1,000 tokens."
    assert adjustment_limit_message(MICRO) == "One adjustment can add at most 1 token."
    assert adjustment_limit_message(2_500_000) == "One adjustment can add at most 2.5 tokens."
    for message in (adjustment_limit_message(CAP), DEBIT_ABOVE_BALANCE_MESSAGE):
        assert "credit" not in message.lower()
        assert "CR" not in message


async def test_the_cap_is_on_one_adjustment_and_a_removal_is_bounded_by_the_balance_alone(
    container: Container, factory: Factory
) -> None:
    """Credits at the cap can follow one another. What an adjustment takes away may be
    far above the cap: the balance is its bound, and a debit beyond the balance is
    refused for the balance, not for the cap."""
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    for _ in range(3):
        await adjust(container, admin, user, CAP)
    holds = WALLET + 3 * CAP
    with pytest.raises(InsufficientBalanceError) as refused:
        await adjust(container, admin, user, -(holds + 1))
    taken = await adjust(container, admin, user, -(holds - 1))

    assert refused.value.message == DEBIT_ABOVE_BALANCE_MESSAGE
    assert refused.value.details == {"balance_cr": to_cr(holds), "required_cr": to_cr(holds + 1)}
    assert holds - 1 > 2 * CAP, "a removal of more than twice the cap"
    assert taken.balance_cr == to_cr(1)
    assert await factory.balance_micro(user) == 1
    assert len(await adjustments(container)) == 4
    await assert_books_balance(container.uow_factory)


async def test_the_cap_is_checked_after_who_acts_and_before_the_key(
    container: Container, factory: Factory
) -> None:
    """403 for the admin's own account whatever the amount; and a key that stands for
    another request is not looked at for an amount that is refused anyway."""
    admin_row = await factory.admin()
    admin = factory.identity(admin_row)
    user = await factory.user(balance_micro=WALLET)
    key = uuid7()
    await adjust(container, admin, user, MICRO, key=key)
    before = await everything(container)

    with pytest.raises(ForbiddenError) as own:
        await adjust(container, admin, admin_row, CAP + 1)
    with pytest.raises(LimitExceededError):
        await adjust(container, admin, user, CAP + 1, key=key)
    with pytest.raises(IdempotencyKeyReusedError):
        await adjust(container, admin, user, CAP, key=key)

    assert own.value.message == OWN_ACCOUNT_MESSAGE
    assert await everything(container) == before


@pytest.mark.parametrize("cap_micro", [5 * MICRO, 0], ids=["five", "nothing"])
async def test_the_cap_is_the_one_of_the_settings(
    container: Container, factory: Factory, cap_micro: int
) -> None:
    """An API started with another MAX_ADMIN_ADJUSTMENT_CR, on the same database. With
    a cap of zero an adjustment can still take away."""
    risk = container.settings.risk.model_copy(update={"max_admin_adjustment_micro": cap_micro})
    users = AdminUserService(
        container.uow_factory,
        container.settings.model_copy(update={"risk": risk}),
        container.clock,
        container.email_policy,
        container.ledger,
        container.audit,
    )
    admin = factory.identity(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    with pytest.raises(LimitExceededError) as refused:
        await users.adjust_balance(admin, user.id, uuid7(), adjustment(cap_micro + 1))
    taken = await users.adjust_balance(admin, user.id, uuid7(), adjustment(-6 * MICRO))

    assert refused.value.message == adjustment_limit_message(cap_micro)
    assert refused.value.details["cap_cr"] == to_cr(cap_micro)
    assert taken.balance_cr == to_cr(WALLET - 6 * MICRO)
    if cap_micro:
        added = await users.adjust_balance(admin, user.id, uuid7(), adjustment(cap_micro))
        assert added.balance_cr == to_cr(WALLET - 6 * MICRO + cap_micro)
    await assert_books_balance(container.uow_factory)


async def test_the_adjustment_route_answers_a_credit_above_the_cap_with_409_and_both_amounts(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    response = await call(
        client, "adjustments", user.id, {"amount_cr": "1000.000001", "reason": REASON}
    )
    at_the_cap = await call(client, "adjustments", user.id, {"amount_cr": "1000", "reason": REASON})

    assert response.status_code == 409, response.text
    assert response.json() == {
        "code": "LIMIT_EXCEEDED",
        "message": "One adjustment can add at most 1,000 tokens.",
        "details": {
            "limit": "MAX_ADMIN_ADJUSTMENT_CR",
            "requested_cr": "1000.000001",
            "cap_cr": "1000",
        },
    }
    assert (at_the_cap.status_code, at_the_cap.json()["balance_cr"]) == (201, "1020")
    assert await factory.balance_micro(user) == WALLET + CAP
    await assert_books_balance(container.uow_factory)


@hypothesis_settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    amounts=st.lists(
        st.integers(min_value=-30 * MICRO, max_value=30 * MICRO).filter(lambda amount: amount != 0),
        min_size=1,
        max_size=8,
    )
)
async def test_whatever_is_adjusted_the_balance_is_the_sum_of_what_went_through(
    container: Container, factory: Factory, amounts: list[int]
) -> None:
    """A run of credits and debits, each of which goes through exactly when the balance
    covers it. Every example is a user of its own in the one database of the test."""
    admin = factory.identity(await factory.user(role=UserRole.ADMIN))
    user = await factory.user(balance_micro=WALLET)
    balance_micro = WALLET
    booked = 0

    for amount_micro in amounts:
        if balance_micro + amount_micro < 0:
            with pytest.raises(InsufficientBalanceError):
                await adjust(container, admin, user, amount_micro)
            continue
        answer = await adjust(container, admin, user, amount_micro)
        balance_micro += amount_micro
        booked += 1
        assert answer.balance_cr == to_cr(balance_micro)

    assert await factory.balance_micro(user) == balance_micro
    ledger = await container.account.ledger(factory.identity(user), PageQuery())
    assert sum(item.amount_cr for item in ledger.items) == to_cr(balance_micro)
    assert [item.kind for item in ledger.items].count(JournalKind.ADMIN_ADJUSTMENT) == booked
    await assert_books_balance(container.uow_factory)


# --- Through the routes ------------------------------------------------------------------


async def test_the_list_route_answers_with_amounts_as_strings_and_takes_its_filters_as_a_query(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    admin = await factory.admin()
    await factory.user(username="anna", name="Anna Muster", balance_micro=12_500_000)
    barista = await factory.barista()
    as_user(admin)

    everybody = await client.get(USERS)
    found = await client.get(USERS, params={"q": "BARISTA", "role": "BARISTA", "status": "ACTIVE"})
    first_page = await client.get(USERS, params={"limit": 1})

    assert everybody.status_code == found.status_code == first_page.status_code == 200
    assert [item["username"] for item in everybody.json()["items"]] == [
        "barista0",
        "anna",
        "admin0",
    ]
    assert everybody.json()["next_cursor"] is None
    assert everybody.json()["items"][1] == {
        "id": everybody.json()["items"][1]["id"],
        "email": "user2@ethz.ch",
        "name": "Anna Muster",
        "username": "anna",
        "degree_level": "BACHELOR",
        "degree_programme": everybody.json()["items"][1]["degree_programme"],
        "role": "USER",
        "status": "ACTIVE",
        "bootstrap_admin": False,
        "balance_cr": "12.5",
        "created_at": TEST_NOW.isoformat().replace("+00:00", "Z"),
    }
    assert everybody.json()["items"][2]["bootstrap_admin"] is True
    assert [item["id"] for item in found.json()["items"]] == [str(barista.id)]
    assert first_page.json()["next_cursor"] == str(barista.id)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("query", "where"),
    [
        ({"q": ""}, "q"),
        ({"q": "   "}, "q"),
        ({"q": "x" * 101}, "q"),
        ({"role": "OWNER"}, "role"),
        ({"status": "GONE"}, "status"),
        ({"limit": "0"}, "limit"),
        ({"limit": "201"}, "limit"),
        ({"sort": "balance"}, "sort"),
    ],
    ids=[
        "an-empty-text",
        "a-blank-text",
        "a-text-of-101-characters",
        "an-unknown-role",
        "an-unknown-status",
        "no-page",
        "a-page-too-large",
        "a-parameter-nobody-asked-for",
    ],
)
async def test_the_list_route_refuses_a_query_it_cannot_take(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs, query: dict[str, str], where: str
) -> None:
    as_user(await factory.admin())

    response = await client.get(USERS, params=query)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert [error["loc"] for error in response.json()["details"]["errors"]] == [["query", where]]


async def test_the_list_route_answers_a_cursor_it_never_handed_out_with_422_on_the_cursor(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.admin())

    response = await client.get(USERS, params={"cursor": "page-2"})

    assert response.status_code == 422, response.text
    assert response.json()["details"] == {"field": "cursor"}


async def test_the_three_actions_on_an_account_answer_with_the_user_as_they_are_afterwards(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)
    as_user(admin)

    suspended = await call(client, "suspend", user.id)
    reinstated = await call(client, "reinstate", user.id)
    promoted = await call(client, "role", user.id)

    for response in (suspended, reinstated, promoted):
        assert response.status_code == 200, response.text
        assert (response.json()["id"], response.json()["balance_cr"]) == (str(user.id), "20")
    assert [response.json()["status"] for response in (suspended, reinstated, promoted)] == [
        "SUSPENDED",
        "ACTIVE",
        "ACTIVE",
    ]
    assert [response.json()["role"] for response in (suspended, reinstated, promoted)] == [
        "USER",
        "USER",
        "BARISTA",
    ]
    assert [row.action for row in await audit_rows(container)] == [
        AuditAction.USER_SUSPEND,
        AuditAction.USER_REINSTATE,
        AuditAction.USER_SET_ROLE,
    ]
    await assert_books_balance(container.uow_factory)


async def test_the_adjustment_route_answers_201_and_the_same_again_under_the_same_key(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)
    as_user(admin)
    key = str(uuid7())
    body = {"amount_cr": "-5.5", "reason": REASON}

    first = await call(client, "adjustments", user.id, body, **{"Idempotency-Key": key})
    again = await call(client, "adjustments", user.id, body, **{"Idempotency-Key": key})
    other_body = await call(
        client, "adjustments", user.id, body | {"amount_cr": "5.5"}, **{"Idempotency-Key": key}
    )

    assert first.status_code == again.status_code == 201, first.text
    assert (
        first.json()
        == again.json()
        == {
            "user_id": str(user.id),
            "amount_cr": "-5.5",
            "balance_cr": "14.5",
            "created_at": TEST_NOW.isoformat().replace("+00:00", "Z"),
        }
    )
    assert (other_body.status_code, other_body.json()["code"]) == (422, "IDEMPOTENCY_KEY_REUSED")
    assert other_body.json()["details"] == {}
    assert await factory.balance_micro(user) == WALLET - 5_500_000
    assert len(await adjustments(container)) == 1
    await assert_books_balance(container.uow_factory)


async def test_the_adjustment_route_answers_a_debit_above_the_balance_with_409_and_both_amounts(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    response = await call(
        client, "adjustments", user.id, {"amount_cr": "-20.000001", "reason": REASON}
    )

    assert response.status_code == 409, response.text
    assert response.json() == {
        "code": "INSUFFICIENT_BALANCE",
        "message": DEBIT_ABOVE_BALANCE_MESSAGE,
        "details": {"balance_cr": "20", "required_cr": "20.000001"},
    }
    assert await factory.balance_micro(user) == WALLET
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("body", "where", "rule"),
    [
        ({"amount_cr": "0", "reason": REASON}, ["body", "amount_cr"], "decimal_zero"),
        ({"amount_cr": "-0.000000", "reason": REASON}, ["body", "amount_cr"], "decimal_zero"),
        ({"amount_cr": 5, "reason": REASON}, None, None),
        ({"amount_cr": 5.5, "reason": REASON}, ["body", "amount_cr"], "decimal_string"),
        ({"amount_cr": "+5", "reason": REASON}, ["body", "amount_cr"], "decimal_string"),
        ({"amount_cr": "0.0000001", "reason": REASON}, ["body", "amount_cr"], "decimal_places"),
        ({"amount_cr": "1000000000", "reason": REASON}, ["body", "amount_cr"], "decimal_too_large"),
        (
            {"amount_cr": "-1000000000", "reason": REASON},
            ["body", "amount_cr"],
            "decimal_too_large",
        ),
        (
            {"amount_cr": "9" * 1_000_000, "reason": REASON},
            ["body", "amount_cr"],
            "decimal_too_large",
        ),
        (
            {"amount_cr": "0." + "0" * 1_100_000 + "1", "reason": REASON},
            ["body", "amount_cr"],
            "decimal_places",
        ),
        ({"reason": REASON}, ["body", "amount_cr"], "missing"),
        ({"amount_cr": "5"}, ["body", "reason"], "missing"),
        ({"amount_cr": "5", "reason": "   "}, ["body", "reason"], "string_too_short"),
        ({"amount_cr": "5", "reason": "x" * 501}, ["body", "reason"], "string_too_long"),
        ({"amount_cr": "5", "reason": REASON, "note": "x"}, ["body", "note"], "extra_forbidden"),
    ],
    ids=[
        "nothing",
        "minus-nothing",
        "a-whole-number",
        "a-float",
        "a-plus-sign",
        "a-seventh-place",
        "too-much",
        "too-much-below-zero",
        "a-million-digits",
        "a-million-zeros-after-the-point",
        "no-amount",
        "no-reason",
        "a-blank-reason",
        "a-reason-of-501-characters",
        "a-field-nobody-asked-for",
    ],
)
async def test_the_adjustment_route_takes_a_signed_amount_that_is_not_zero_and_a_reason(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    body: dict[str, Any],
    where: list[str] | None,
    rule: str | None,
) -> None:
    """Whatever the request model refuses is 422 on its field and books nothing. A JSON
    integer is an exact amount and goes through."""
    as_user(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    response = await call(client, "adjustments", user.id, body)

    if where is None:
        assert (response.status_code, response.json()["amount_cr"]) == (201, "5"), response.text
        assert await factory.balance_micro(user) == WALLET + 5 * MICRO
    else:
        assert response.status_code == 422, response.text[:300]
        assert [
            (error["loc"], error["type"]) for error in response.json()["details"]["errors"]
        ] == [(where, rule)]
        assert await factory.balance_micro(user) == WALLET
        assert await adjustments(container) == []
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("headers", "rule"),
    [
        ({}, "missing"),
        ({"Idempotency-Key": "tap-1"}, "uuid_parsing"),
        ({"Idempotency-Key": ""}, "uuid_parsing"),
    ],
    ids=["no-key", "a-key-that-is-no-uuid", "an-empty-key"],
)
async def test_an_adjustment_without_an_idempotency_key_that_is_a_uuid_is_422(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    headers: dict[str, str],
    rule: str,
) -> None:
    as_user(await factory.admin())
    user = await factory.user(balance_micro=WALLET)

    response = await client.post(
        f"{USERS}/{user.id}/adjustments", json=BODIES["adjustments"], headers=headers
    )

    assert response.status_code == 422, response.text
    (error,) = response.json()["details"]["errors"]
    assert (error["loc"], error["type"]) == (["header", "Idempotency-Key"], rule)
    assert await adjustments(container) == []


@pytest.mark.parametrize(
    ("action", "body", "where"),
    [
        ("suspend", {}, "reason"),
        ("suspend", {"reason": ""}, "reason"),
        ("suspend", {"reason": "x" * 501}, "reason"),
        ("reinstate", {}, "reason"),
        ("reinstate", {"reason": 7}, "reason"),
        ("role", {}, "role"),
        ("role", {"role": "OWNER"}, "role"),
        ("role", {"role": "admin"}, "role"),
        ("role", {"role": "ADMIN", "reason": REASON}, "reason"),
    ],
)
async def test_the_routes_refuse_a_body_they_cannot_take_and_change_nothing(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    action: str,
    body: dict[str, Any],
    where: str,
) -> None:
    as_user(await factory.admin())
    user = await factory.user(
        status=UserStatus.SUSPENDED if action == "reinstate" else UserStatus.ACTIVE
    )
    before = await everything(container)

    response = await call(client, action, user.id, body)

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["details"]["errors"]] == [["body", where]]
    assert await everything(container) == before


@every_action
async def test_the_routes_answer_the_refusals_of_the_service_with_their_status_and_sentence(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs, action: str
) -> None:
    """403 for the admin's own account, 404 for nobody's."""
    admin = await factory.admin()
    as_user(admin)
    before = await everything(container)

    own = await call(client, action, admin.id)
    unknown = await call(client, action, uuid7())
    no_id = await client.post(f"{USERS}/not-a-uuid/{action}", json=BODIES[action])

    assert own.status_code == 403, own.text
    assert own.json() == {"code": "FORBIDDEN", "message": OWN_ACCOUNT_MESSAGE, "details": {}}
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND"), unknown.text
    assert no_id.status_code == 422
    assert await everything(container) == before


async def test_the_routes_answer_for_a_bootstrap_admin_and_for_a_state_that_does_not_fit(
    client: httpx.AsyncClient, container: Container, factory: Factory, as_user: ActAs
) -> None:
    admin, other_admin = await factory.admin(), await factory.admin(1)
    active, suspended = await factory.user(), await factory.user(status=UserStatus.SUSPENDED)
    as_user(admin)
    before = await everything(container)

    answers = {
        "suspend a bootstrap admin": await call(client, "suspend", other_admin.id),
        "change its role": await call(client, "role", other_admin.id),
        "suspend twice": await call(client, "suspend", suspended.id),
        "reinstate an active one": await call(client, "reinstate", active.id),
    }

    assert {
        name: (answer.status_code, answer.json()["message"]) for name, answer in answers.items()
    } == {
        "suspend a bootstrap admin": (403, BOOTSTRAP_SUSPEND_MESSAGE),
        "change its role": (403, BOOTSTRAP_ROLE_MESSAGE),
        "suspend twice": (409, ALREADY_SUSPENDED_MESSAGE),
        "reinstate an active one": (409, NOT_SUSPENDED_MESSAGE),
    }
    assert {answer.json()["code"] for answer in answers.values()} == {"FORBIDDEN", "CONFLICT"}
    assert all(answer.json()["details"] == {} for answer in answers.values())
    assert await everything(container) == before


@every_action
@pytest.mark.parametrize("role", [UserRole.USER, UserRole.BARISTA])
async def test_the_routes_are_for_admins_and_change_nothing_for_anybody_else(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    action: str,
    role: UserRole,
) -> None:
    user = await factory.user(balance_micro=WALLET)
    as_user(await factory.user(role=role))
    before = await everything(container)

    acted = await call(client, action, user.id)
    listed = await client.get(USERS)

    for response in (acted, listed):
        assert (response.status_code, response.json()["code"]) == (403, "FORBIDDEN")
    assert await everything(container) == before


async def test_an_adjustment_made_through_the_portal_shows_in_the_users_own_ledger(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
) -> None:
    """The whole way, with the two headers of the portal and nothing else: the admin
    credits the user, and the user's next requests show the balance and the line."""
    admin = await factory.admin()
    user = await factory.user(balance_micro=WALLET)

    booked = await client.post(
        f"{USERS}/{user.id}/adjustments",
        json={"amount_cr": "15", "reason": REASON},
        headers=portal_headers(admin) | {"Idempotency-Key": str(uuid7())},
    )
    me = await client.get("/api/me", headers=portal_headers(user))
    ledger = await client.get("/api/me/ledger", headers=portal_headers(user))
    listed = await client.get(USERS, params={"q": user.username}, headers=portal_headers(admin))

    assert booked.status_code == 201, booked.text
    assert booked.json()["balance_cr"] == me.json()["balance_cr"] == format_cr(WALLET + 15 * MICRO)
    lines = LedgerListResponse.model_validate(ledger.json()).items
    assert (lines[0].kind, lines[0].amount_cr, lines[0].description) == (
        JournalKind.ADMIN_ADJUSTMENT,
        Decimal(15),
        "Adjustment by VIS",
    )
    assert json.loads(listed.text)["items"][0]["balance_cr"] == "35"
    assert REASON not in ledger.text
    await assert_books_balance(container.uow_factory)
