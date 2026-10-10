"""What no cron job does: monthly grants and voucher refunds, booked when their user
shows up (SPEC §2, Appendix A.3, A.6, A.8).

An ordinary request of a user is all it takes. The portal's headers lead to
`IdentityService`, which asks `UserUpkeepService`, which books what `GrantService` and
`VoucherService` say is owed, before the route answers. Each of those has tests of its
own with stand-ins for the others; here they are the real ones, wired as the
application wires them, and every request carries the portal's headers.

The clock of the tests starts on 9 October 2026 at noon UTC, which is 14:00 in
`APP_TIMEZONE`; a user the factory makes registered then. Their monthly grants fall due
on the 9th of every later month at 14:00 in Zurich.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.container import Container
from app.core.clock import local_date, month_period
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    AccountKind,
    JournalKind,
    VoucherStatus,
)
from app.core.errors import SUSPENDED_MESSAGE
from app.core.units import format_cr, to_cr
from app.main import create_app
from app.models import JournalEntry, User, Voucher
from app.schemas.me import LedgerListResponse, MeResponse
from app.schemas.vouchers import VoucherIssuedResponse, VoucherListResponse
from app.services.account import ledger_description
from tests.support.books import assert_books_balance, system_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import Factory, PortalHeaders
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

_SETTINGS = load_base_settings()

GRANT: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what a user of these tests starts with."""

MONTHLY: Final[int] = _SETTINGS.grants.monthly_grant_micro
"""MONTHLY_GRANT_CR in µCR."""

INTERVAL: Final[timedelta] = _SETTINGS.identity.upkeep_interval
"""UPKEEP_INTERVAL_SECONDS: how often, per user and process, a request starts upkeep."""

EXPIRY: Final[timedelta] = _SETTINGS.vouchers.ttl
"""VOUCHER_EXPIRY_DAYS: how long a voucher waits for its scan."""

PRODUCT: Final = _SETTINGS.vouchers.products[0]
"""What the vouchers of these tests are for."""

ZONE: Final = _SETTINGS.app.timezone
SECOND: Final[timedelta] = timedelta(seconds=1)


def month_of(instant: datetime) -> str:
    """The `grants.period` of the month of `instant`."""
    return month_period(local_date(instant, ZONE))


FIRST_DUE: Final[datetime] = datetime(2026, 11, 9, 14, 0, tzinfo=ZONE).astimezone(UTC)
SECOND_DUE: Final[datetime] = datetime(2026, 12, 9, 14, 0, tzinfo=ZONE).astimezone(UTC)
THIRD_DUE: Final[datetime] = datetime(2027, 1, 9, 14, 0, tzinfo=ZONE).astimezone(UTC)
"""When the first three monthly grants of a user of these tests fall due: a month,
two and three after `TEST_NOW`, on its day and at its time of day in Zurich."""


class Portal:
    """The routes as the portal passes a signed-in person on to them."""

    def __init__(self, client: httpx.AsyncClient, headers: PortalHeaders) -> None:
        self._client = client
        self._headers = headers

    async def send(
        self, who: User, method: str, path: str, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await self._client.request(
            method, f"/api{path}", json=body, headers=self._headers(who)
        )

    async def ok[ModelT: BaseModel](
        self,
        who: User,
        method: str,
        path: str,
        model: type[ModelT],
        body: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
    ) -> ModelT:
        response = await self.send(who, method, path, body)
        assert response.status_code == status_code, response.text
        return model.model_validate(response.json())

    async def me(self, who: User) -> MeResponse:
        return await self.ok(who, "GET", "/me", MeResponse)

    async def browse(self, who: User) -> None:
        """An ordinary request that is about nothing of the user's own: the market list."""
        response = await self.send(who, "GET", "/markets")
        assert response.status_code == 200, response.text


@pytest.fixture
def portal(client: httpx.AsyncClient, portal_headers: PortalHeaders) -> Portal:
    return Portal(client, portal_headers)


async def periods(container: Container, user: User) -> list[str]:
    """The grants a user was booked, as their periods, ascending."""
    async with container.uow_factory() as uow:
        return list(await uow.grants.list_periods(user.id))


async def entries_of(container: Container, kind: JournalKind) -> int:
    """How many journal entries of a kind there are."""
    async with container.uow_factory() as uow:
        entries = (await uow.session.scalars(select(JournalEntry))).all()
    return [entry.kind for entry in entries].count(kind)


async def pending(container: Container) -> int:
    """What is parked for vouchers that wait for their scan, in µCR."""
    return await system_balance(container.uow_factory, AccountKind.VIS_REDEMPTION_PENDING)


# --- The monthly grant -----------------------------------------------------------------------


async def test_the_first_request_after_a_grant_fell_due_books_it_and_shows_it_in_its_own_answer(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Upkeep runs before the route answers: the request that brings the grant about is
    the first to see it. Until the day and the time of day of the sign-up have come
    round, no request brings one about, the first of the month included."""
    user = await factory.user(balance_micro=GRANT)

    at_sign_up = await portal.me(user)
    clock.set(datetime(2026, 11, 1, tzinfo=ZONE))
    on_the_first = await portal.me(user)
    clock.set(FIRST_DUE - 2 * INTERVAL)
    just_before = await portal.me(user)
    clock.set(FIRST_DUE)
    due = await portal.me(user)

    for before in (at_sign_up, on_the_first, just_before):
        assert before.balance_cr == to_cr(GRANT)
        assert before.next_monthly_grant_at == FIRST_DUE
    assert FIRST_DUE.astimezone(ZONE) == datetime(2026, 11, 9, 14, 0, tzinfo=ZONE)
    assert due.balance_cr == to_cr(GRANT + MONTHLY)
    assert due.monthly_grant_cr == to_cr(MONTHLY)
    assert due.next_monthly_grant_at == SECOND_DUE
    assert await periods(container, user) == sorted([month_of(FIRST_DUE), SIGNUP_GRANT_PERIOD])
    ledger = await portal.ok(user, "GET", "/me/ledger", LedgerListResponse)
    assert [(item.kind, item.description, item.amount_cr) for item in ledger.items] == [
        (JournalKind.MONTHLY_GRANT, ledger_description(JournalKind.MONTHLY_GRANT), to_cr(MONTHLY)),
        (JournalKind.SIGNUP_GRANT, ledger_description(JournalKind.SIGNUP_GRANT), to_cr(GRANT)),
    ]
    assert ledger.items[0].created_at == FIRST_DUE
    await assert_books_balance(container.uow_factory)


async def test_any_ordinary_request_books_the_grant_and_it_is_booked_once(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The request does not have to ask about the balance, and however often the user
    comes back before the next grant falls due, within the interval or long after it,
    nothing more is booked."""
    user = await factory.user(balance_micro=GRANT)
    clock.set(FIRST_DUE + timedelta(hours=9))

    await portal.browse(user)
    assert await factory.balance_micro(user) == GRANT + MONTHLY
    for wait in (SECOND, INTERVAL, timedelta(days=1), timedelta(days=20)):
        clock.advance(wait)
        await portal.browse(user)
        assert (await portal.me(user)).balance_cr == to_cr(GRANT + MONTHLY)

    assert clock.now() < SECOND_DUE
    assert await entries_of(container, JournalKind.MONTHLY_GRANT) == 1
    assert await periods(container, user) == sorted([month_of(FIRST_DUE), SIGNUP_GRANT_PERIOD])
    await assert_books_balance(container.uow_factory)


async def test_a_user_who_stayed_away_is_booked_every_month_they_missed_with_their_next_request(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A.3: every grant that has fallen due, including those of the months in which the
    user never came by. The month of signup itself brings none: the signup grant covers
    it."""
    user = await factory.user(balance_micro=GRANT)
    clock.set(THIRD_DUE + timedelta(days=14))

    back = await portal.me(user)

    months = [month_of(FIRST_DUE), month_of(SECOND_DUE), month_of(THIRD_DUE)]
    assert months == ["2026-11", "2026-12", "2027-01"]
    assert back.balance_cr == to_cr(GRANT + len(months) * MONTHLY)
    assert await periods(container, user) == sorted([*months, SIGNUP_GRANT_PERIOD])
    assert month_of(TEST_NOW) not in await periods(container, user)
    assert await entries_of(container, JournalKind.MONTHLY_GRANT) == len(months)
    ledger = await portal.ok(user, "GET", "/me/ledger", LedgerListResponse)
    assert sum(item.amount_cr for item in ledger.items) == back.balance_cr
    await assert_books_balance(container.uow_factory)


async def test_requests_that_arrive_together_as_a_grant_falls_due_book_one_grant(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A phone opens with several requests at once. One of them starts the upkeep; the
    others do not wait for it, so each shows the balance before or after the grant, and
    the grant is there once."""
    user = await factory.user(balance_micro=GRANT)
    clock.set(FIRST_DUE)

    answers = await asyncio.gather(*(portal.me(user) for _ in range(8)))

    assert {answer.balance_cr for answer in answers} <= {to_cr(GRANT), to_cr(GRANT + MONTHLY)}
    assert (await portal.me(user)).balance_cr == to_cr(GRANT + MONTHLY)
    assert await entries_of(container, JournalKind.MONTHLY_GRANT) == 1
    await assert_books_balance(container.uow_factory)


@asynccontextmanager
async def another_process(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    """A second API process on the same Postgres and Redis: an application of its own
    around a container of its own, which knows nothing of what the first one did."""
    other = Container.build(container.settings, clock=container.clock, database=container.database)
    await other.start()
    origin = container.settings.app.public_base_url
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(other)),
            base_url=origin,
            headers={"Origin": origin},
        ) as client:
            yield client
    finally:
        await other.stop()


async def test_two_api_processes_that_both_find_a_user_due_book_the_grant_once(
    portal: Portal,
    portal_headers: PortalHeaders,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
) -> None:
    """Each process keeps its own note of when it last looked after a user, so both
    start the upkeep. The account lock makes one wait for the other, and the `grants`
    row makes the month happen once (SPEC §2)."""
    user = await factory.user(balance_micro=GRANT)
    clock.set(FIRST_DUE)

    async with another_process(container) as elsewhere:
        answers = await asyncio.gather(
            portal.send(user, "GET", "/me"),
            elsewhere.get("/api/me", headers=portal_headers(user)),
            portal.send(user, "GET", "/markets"),
            elsewhere.get("/api/markets", headers=portal_headers(user)),
        )
        later = await elsewhere.get("/api/me", headers=portal_headers(user))

    assert [answer.status_code for answer in answers] == [200, 200, 200, 200]
    assert later.json()["balance_cr"] == format_cr(GRANT + MONTHLY)
    assert await factory.balance_micro(user) == GRANT + MONTHLY
    assert await entries_of(container, JournalKind.MONTHLY_GRANT) == 1
    assert await periods(container, user) == sorted([month_of(FIRST_DUE), SIGNUP_GRANT_PERIOD])
    await assert_books_balance(container.uow_factory)


# --- The refund of an expired voucher ------------------------------------------------------------


async def issued_voucher(portal: Portal, user: User) -> VoucherIssuedResponse:
    """A voucher the user gets through the route, which is also their first request."""
    return await portal.ok(
        user,
        "POST",
        "/vouchers",
        VoucherIssuedResponse,
        {"product_id": PRODUCT.id},
        status_code=201,
    )


async def test_an_expired_voucher_is_refunded_by_the_next_ordinary_request_of_its_owner(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Nobody scanned it within VOUCHER_EXPIRY_DAYS. Until its owner shows up the
    credits stay parked; then any request of theirs moves them back, once, and the
    wallet and the ledger say so."""
    user = await factory.user(balance_micro=GRANT)
    voucher = await issued_voucher(portal, user)
    price = PRODUCT.price_micro
    assert voucher.expires_at == TEST_NOW + EXPIRY
    assert voucher.expires_at + timedelta(hours=13) < FIRST_DUE, "no grant falls due in this test"
    assert await factory.balance_micro(user) == GRANT - price

    # A minute before it expires: the visit books no refund.
    clock.set(voucher.expires_at - 2 * INTERVAL)
    (waiting,) = (await portal.ok(user, "GET", "/me/vouchers", VoucherListResponse)).items
    assert waiting.status is VoucherStatus.ACTIVE
    assert await factory.balance_micro(user) == GRANT - price
    assert await pending(container) == price

    # Expired, and its owner has not been back yet: nothing has moved.
    clock.set(voucher.expires_at)
    assert await factory.balance_micro(user) == GRANT - price
    assert await pending(container) == price

    await portal.browse(user)

    assert await factory.balance_micro(user) == GRANT
    assert await pending(container) == 0
    (expired,) = (await portal.ok(user, "GET", "/me/vouchers", VoucherListResponse)).items
    assert (expired.id, expired.status) == (voucher.id, VoucherStatus.EXPIRED)
    ledger = await portal.ok(user, "GET", "/me/ledger", LedgerListResponse)
    assert [(item.description, item.amount_cr) for item in ledger.items] == [
        (ledger_description(JournalKind.VOUCHER_REFUND, PRODUCT.name), to_cr(price)),
        (ledger_description(JournalKind.VOUCHER_ISSUE, PRODUCT.name), -to_cr(price)),
        (ledger_description(JournalKind.SIGNUP_GRANT), to_cr(GRANT)),
    ]
    assert ledger.items[0].created_at == voucher.expires_at

    # Coming back again books nothing more.
    for wait in (INTERVAL, timedelta(hours=12)):
        clock.advance(wait)
        assert (await portal.me(user)).balance_cr == to_cr(GRANT)
    assert await entries_of(container, JournalKind.VOUCHER_REFUND) == 1
    assert await entries_of(container, JournalKind.MONTHLY_GRANT) == 0
    await assert_books_balance(container.uow_factory)


async def test_one_request_books_the_refund_and_the_grant_of_somebody_who_was_away_for_both(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The voucher expired a day before the first monthly grant fell due, and the user
    came back after both: one request, one run of upkeep, both lines in the ledger with
    the instant of that request."""
    user = await factory.user(balance_micro=GRANT)
    voucher = await issued_voucher(portal, user)
    price = PRODUCT.price_micro
    back_at = FIRST_DUE + timedelta(days=2)
    assert voucher.expires_at < FIRST_DUE < back_at
    clock.set(back_at)

    back = await portal.me(user)

    assert back.balance_cr == to_cr(GRANT + MONTHLY)
    assert await pending(container) == 0
    ledger = await portal.ok(user, "GET", "/me/ledger", LedgerListResponse)
    assert [(item.description, item.amount_cr, item.created_at) for item in ledger.items] == [
        (ledger_description(JournalKind.VOUCHER_REFUND, PRODUCT.name), to_cr(price), back_at),
        (ledger_description(JournalKind.MONTHLY_GRANT), to_cr(MONTHLY), back_at),
        (ledger_description(JournalKind.VOUCHER_ISSUE, PRODUCT.name), -to_cr(price), TEST_NOW),
        (ledger_description(JournalKind.SIGNUP_GRANT), to_cr(GRANT), TEST_NOW),
    ]
    await assert_books_balance(container.uow_factory)


async def test_the_code_of_a_voucher_its_owner_was_refunded_for_buys_nothing_at_the_counter(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The refund and the scan are two ways the parked credits can leave, and only one
    of them happens: after the refund the barista's scan is the one generic refusal,
    and no credit moves a second time."""
    user = await factory.user(balance_micro=GRANT)
    barista = await factory.barista()
    voucher = await issued_voucher(portal, user)
    clock.set(voucher.expires_at + INTERVAL)
    await portal.browse(user)
    refunded = await factory.balance_micro(user)

    scan = await portal.send(barista, "POST", "/barista/vouchers/redeem", {"code": voucher.code})
    fresh = await portal.send(user, "POST", f"/vouchers/{voucher.id}/code")

    assert refunded == GRANT
    assert (scan.status_code, scan.json()["code"]) == (400, "CODE_INVALID")
    assert (fresh.status_code, fresh.json()["code"]) == (409, "CONFLICT"), (
        "an expired one takes none"
    )
    assert await factory.balance_micro(user) == refunded
    assert await system_balance(container.uow_factory, AccountKind.VIS_REDEEMED) == 0
    assert await pending(container) == 0
    assert await entries_of(container, JournalKind.VOUCHER_REFUND) == 1
    assert await entries_of(container, JournalKind.VOUCHER_REDEEM) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_voucher_scanned_in_time_is_not_refunded_when_its_date_has_passed(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The other order: the barista's scan came first, so the owner's later visit finds
    nothing to refund."""
    user = await factory.user(balance_micro=GRANT)
    barista = await factory.barista()
    voucher = await issued_voucher(portal, user)
    price = PRODUCT.price_micro
    clock.advance(timedelta(days=1))
    scan = await portal.send(barista, "POST", "/barista/vouchers/redeem", {"code": voucher.code})
    assert scan.status_code == 200, scan.text

    clock.set(FIRST_DUE + timedelta(days=1))
    assert voucher.expires_at < clock.now()
    after = await portal.me(user)

    assert after.balance_cr == to_cr(GRANT - price + MONTHLY), "the grant, and no refund"
    assert await system_balance(container.uow_factory, AccountKind.VIS_REDEEMED) == price
    assert await entries_of(container, JournalKind.VOUCHER_REFUND) == 0
    async with container.uow_factory() as uow:
        (row,) = (await uow.session.scalars(select(Voucher))).all()
    assert (row.status, row.redeemed_by) == (VoucherStatus.REDEEMED, barista.id)
    await assert_books_balance(container.uow_factory)


# --- A suspended account --------------------------------------------------------------------------


async def test_a_suspended_user_is_refunded_and_gets_no_grant_until_the_suspension_is_lifted(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A.3: a suspension stops what a user may do, not what they are owed back. An
    admin suspends the account through the panel's route. The user's request is then
    refused, and the refund of their expired voucher is booked by it all the same; a
    monthly grant is for ACTIVE users, and follows once an admin has reinstated the
    account."""
    user = await factory.user(balance_micro=GRANT)
    admin = await factory.admin()
    voucher = await issued_voucher(portal, user)
    suspended = await portal.send(
        admin, "POST", f"/admin/users/{user.id}/suspend", {"reason": "Under review."}
    )
    assert (suspended.status_code, suspended.json()["status"]) == (200, "SUSPENDED")
    clock.set(FIRST_DUE + INTERVAL)
    assert voucher.expires_at < clock.now(), "a refund and a grant have both fallen due"

    refused = await portal.send(user, "GET", "/me")

    assert refused.status_code == 403, refused.text
    assert refused.json() == {"code": "FORBIDDEN", "message": SUSPENDED_MESSAGE, "details": {}}
    assert await factory.balance_micro(user) == GRANT, "the voucher's price is back, and no grant"
    assert await pending(container) == 0
    assert await periods(container, user) == [SIGNUP_GRANT_PERIOD]
    assert await entries_of(container, JournalKind.VOUCHER_REFUND) == 1
    await assert_books_balance(container.uow_factory)

    reinstated = await portal.send(
        admin, "POST", f"/admin/users/{user.id}/reinstate", {"reason": "Cleared."}
    )
    assert (reinstated.status_code, reinstated.json()["status"]) == (200, "ACTIVE")
    clock.advance(INTERVAL)
    back = await portal.me(user)

    assert back.balance_cr == to_cr(GRANT + MONTHLY)
    assert await periods(container, user) == sorted([month_of(FIRST_DUE), SIGNUP_GRANT_PERIOD])
    await assert_books_balance(container.uow_factory)
