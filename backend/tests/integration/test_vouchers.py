"""`VoucherService`: what credits buy (SPEC §2, Appendix A.6, A.8).

A voucher is for one product of `VOUCHER_PRODUCTS` and takes that product's price out of
its owner's balance. The price waits in `vis:redemption_pending` until a scan behind the
bar moves it on to `vis:redeemed`, or until the voucher expires and it goes back. A
voucher is redeemed once: the barista who redeemed it is shown that redemption again
for `VOUCHER_RESCAN_SECONDS`, and to everybody else it is used up.

The tests act as the people involved, the owner of a wallet and whoever scans, against
Postgres and the ledger. Most go through the real routes. `as_user` lets one person act
at a time, so whatever several people do at the same moment, and the property at the
end, calls the service the routes call. Products and prices are those of the test
settings; none is typed in here. Every test that moves credits ends by holding the whole
database against the rules of the books.
"""

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter
from sqlalchemy import Select, select, text

from app.container import Container
from app.core.config import VoucherProduct
from app.core.enums import AccountKind, JournalKind, UserRole, VoucherStatus
from app.core.errors import (
    CodeInvalidError,
    ConflictError,
    InsufficientBalanceError,
    NotFoundError,
)
from app.core.security import format_code, generate_code, hash_code, normalize_code
from app.core.units import format_cr, to_cr
from app.db.base import Base
from app.models import Account, JournalEntry, Posting, User, Voucher
from app.repositories.voucher import VoucherRepository
from app.schemas.barista import VoucherRedeemResponse
from app.schemas.vouchers import IssueVoucherRequest, VoucherCodeResponse
from app.services.ledger import (
    LedgerRef,
    voucher_issue_key,
    voucher_redeem_key,
    voucher_refund_key,
)
from app.services.vouchers import UNKNOWN_PRODUCT_MESSAGE, VoucherService
from tests.support.books import assert_books_balance, system_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, PortalHeaders, identity_of
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

PRODUCTS_PATH: Final[str] = "/api/vouchers/products"
VOUCHERS_PATH: Final[str] = "/api/vouchers"
WALLET_PATH: Final[str] = "/api/me/vouchers"
SCAN_PATH: Final[str] = "/api/barista/vouchers/redeem"

_SETTINGS = load_base_settings()

PRODUCTS: Final[tuple[VoucherProduct, ...]] = _SETTINGS.vouchers.products
"""VOUCHER_PRODUCTS of the test settings, in the order written."""

PRODUCT: Final[VoucherProduct] = PRODUCTS[0]
"""What a test gets a voucher for when it does not matter which product it is."""

CHEAPEST: Final[VoucherProduct] = min(PRODUCTS, key=lambda product: product.price_micro)
DEAREST: Final[VoucherProduct] = max(PRODUCTS, key=lambda product: product.price_micro)

PRICE: Final[int] = PRODUCT.price_micro
"""What a voucher for `PRODUCT` takes, in µCR."""

TTL: Final[timedelta] = _SETTINGS.vouchers.ttl

RESCAN: Final[timedelta] = _SETTINGS.vouchers.rescan_window
"""VOUCHER_RESCAN_SECONDS: for how long the barista who redeemed a voucher is shown
that redemption again."""
"""VOUCHER_EXPIRY_DAYS: how long a voucher can be redeemed."""

CODE_LENGTH: Final[int] = _SETTINGS.vouchers.code_length

WEALTH: Final[int] = 10 * DEAREST.price_micro
"""A balance that covers every voucher a test gets."""

INSTANT: Final[timedelta] = timedelta(microseconds=1)
"""The smallest step a clock and a timestamp can tell apart."""

LOCK_WAIT_SECONDS: Final[float] = _SETTINGS.postgres.lock_timeout_ms / 2000
"""How long a test waits to see a statement queue for a lock: half of what the
statement itself waits before it gives up (POSTGRES_LOCK_TIMEOUT_MS)."""

ISSUE: Final = JournalKind.VOUCHER_ISSUE
REDEEM: Final = JournalKind.VOUCHER_REDEEM
REFUND: Final = JournalKind.VOUCHER_REFUND

USER: Final = AccountKind.USER
PENDING: Final = AccountKind.VIS_REDEMPTION_PENDING
REDEEMED: Final = AccountKind.VIS_REDEEMED

CODE_INVALID: Final[dict[str, object]] = {
    "code": "CODE_INVALID",
    "message": CodeInvalidError.default_message,
    "details": {},
}
"""The one answer every scan that fails gets (A.6)."""

NOT_FOUND: Final[dict[str, object]] = {
    "code": "NOT_FOUND",
    "message": NotFoundError.default_message,
    "details": {},
}

CONFLICT: Final[dict[str, object]] = {
    "code": "CONFLICT",
    "message": ConflictError.default_message,
    "details": {},
}

_WIRE_TIME: Final = TypeAdapter(datetime)


def on_the_wire(instant: datetime) -> str:
    """An instant as the API writes it."""
    written: str = _WIRE_TIME.dump_python(instant, mode="json")
    return written


class Acting:
    """The requests of the people in a test: every call says who is asking."""

    def __init__(self, client: httpx.AsyncClient, as_user: ActAs) -> None:
        self._client = client
        self._as_user = as_user

    async def products(self, user: User) -> httpx.Response:
        self._as_user(user)
        return await self._client.get(PRODUCTS_PATH)

    async def ask_for_voucher(self, user: User, product_id: str) -> httpx.Response:
        self._as_user(user)
        return await self._client.post(VOUCHERS_PATH, json={"product_id": product_id})

    async def get_voucher(self, user: User, product: VoucherProduct = PRODUCT) -> dict[str, Any]:
        """A voucher for `product`, as `POST /vouchers` answers it: with its code."""
        response = await self.ask_for_voucher(user, product.id)
        assert response.status_code == 201, response.text
        issued: dict[str, Any] = response.json()
        return issued

    async def wallet(self, user: User) -> httpx.Response:
        self._as_user(user)
        return await self._client.get(WALLET_PATH)

    async def statuses(self, user: User) -> list[str]:
        """The status of each of the user's vouchers as the wallet lists them, oldest
        first."""
        items = (await self.wallet(user)).json()["items"]
        return [item["status"] for item in reversed(items)]

    async def fresh_code(self, user: User, voucher_id: object) -> httpx.Response:
        self._as_user(user)
        return await self._client.post(f"{VOUCHERS_PATH}/{voucher_id}/code")

    async def scan(self, barista: User, code: str) -> httpx.Response:
        self._as_user(barista)
        return await self._client.post(SCAN_PATH, json={"code": code})


@pytest.fixture
def acting(client: httpx.AsyncClient, as_user: ActAs) -> Acting:
    return Acting(client, as_user)


async def rows(container: Container, user: User) -> list[Voucher]:
    """The user's voucher rows as they are stored, oldest first."""
    async with container.uow_factory() as uow:
        return list(reversed(await uow.vouchers.list_for_user(user.id)))


async def parked(container: Container) -> tuple[int, int]:
    """What `vis:redemption_pending` and `vis:redeemed` hold, in µCR."""
    return (
        await system_balance(container.uow_factory, PENDING),
        await system_balance(container.uow_factory, REDEEMED),
    )


async def voucher_entries(container: Container) -> list[JournalKind]:
    """The kinds of the journal entries about vouchers, in the order they were booked."""
    statement = (
        select(JournalEntry.kind)
        .where(JournalEntry.ref_type == LedgerRef.VOUCHER)
        .order_by(JournalEntry.id)
    )
    async with container.uow_factory() as uow:
        return list(await uow.session.scalars(statement))


async def entry(container: Container, key: str) -> tuple[JournalEntry, dict[AccountKind, int]]:
    """The journal entry with this idempotency key, and what it posted to which kind of
    account."""
    async with container.uow_factory() as uow:
        found = await uow.journal.get_entry_by_idempotency_key(key)
        assert found is not None, f"no journal entry {key!r}"
        postings = await uow.session.execute(
            select(Account.kind, Posting.amount_micro)
            .join(Account, Account.id == Posting.account_id)
            .where(Posting.entry_id == found.id)
        )
        return found, dict(postings.all())


async def everything_stored(container: Container) -> str:
    """Every row of every table, as text."""
    stored: list[str] = []
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            statement = text(f"SELECT row_to_json(t)::text FROM {table.name} AS t")
            stored.extend(await uow.session.scalars(statement))
    return "\n".join(stored)


async def expire_due(container: Container, user: User) -> int:
    """What the upkeep of a user's request does about their vouchers: under the lock of
    their account, refund the ones that expired. Returns how many that was."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        return await container.vouchers.expire_due(uow, account)


async def until_it_waits(container: Container, operation: asyncio.Task[Any]) -> None:
    """Return once a session of the database queues for a lock. Nothing else runs in a
    test, so it is `operation`'s. Fail if the operation ends instead, or nothing queues
    in `LOCK_WAIT_SECONDS`."""
    queued = text(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with asyncio.timeout(LOCK_WAIT_SECONDS):
        while True:
            async with container.uow_factory() as uow:
                if await uow.session.scalar(queued):
                    return
            assert not operation.done(), "it went through without waiting for the lock"
            await asyncio.sleep(0.01)


async def lockable(container: Container, row: Select[UUID]) -> bool:
    """Whether the row `row` selects can be locked right now: nobody holds it."""
    async with container.uow_factory() as uow:
        free = await uow.session.scalar(row.with_for_update(key_share=True, skip_locked=True))
    return free is not None


def with_products(container: Container, products: Sequence[VoucherProduct]) -> VoucherService:
    """The voucher service of an API that was started with another `VOUCHER_PRODUCTS`,
    on the same database."""
    vouchers = container.settings.vouchers.model_copy(update={"products": tuple(products)})
    changed = container.settings.model_copy(update={"vouchers": vouchers})
    return VoucherService(container.uow_factory, changed, container.clock, container.ledger)


# --- Products ---------------------------------------------------------------------


async def test_the_products_are_those_of_the_settings_in_their_order_with_their_prices(
    acting: Acting, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WEALTH)

    response = await acting.products(alice)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": product.id,
                "name": product.name,
                "price_cr": format_cr(product.price_micro),
                "affordable": True,
            }
            for product in PRODUCTS
        ]
    }


async def test_a_product_is_affordable_exactly_when_the_callers_balance_covers_its_price(
    acting: Acting, factory: Factory
) -> None:
    """Per caller and to the micro-credit: a balance of the price is enough, one
    millionth of a credit less is not. The wallet compares nothing (golden rule 6)."""
    for price_micro in sorted({product.price_micro for product in PRODUCTS}):
        for balance_micro in (price_micro, price_micro - 1):
            user = await factory.user(balance_micro=balance_micro)

            items = (await acting.products(user)).json()["items"]

            affordable = {item["id"] for item in items if item["affordable"]}
            covered = {product.id for product in PRODUCTS if product.price_micro <= balance_micro}
            assert affordable == covered, f"with a balance of {balance_micro} µCR"


async def test_nothing_is_affordable_with_an_empty_balance(
    acting: Acting, factory: Factory
) -> None:
    broke = await factory.user()

    items = (await acting.products(broke)).json()["items"]

    assert [item["id"] for item in items] == [product.id for product in PRODUCTS]
    assert not any(item["affordable"] for item in items)


# --- Issuing ----------------------------------------------------------------------


async def test_a_voucher_debits_the_price_of_its_product_and_parks_it_in_redemption_pending(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)

    response = await acting.ask_for_voucher(alice, PRODUCT.id)

    assert response.status_code == 201
    issued = response.json()
    voucher_id = UUID(issued["id"])
    assert issued == {
        "id": str(voucher_id),
        "product_id": PRODUCT.id,
        "product_name": PRODUCT.name,
        "status": "ACTIVE",
        "refunded": False,
        "value_cr": format_cr(PRICE),
        "created_at": on_the_wire(clock.now()),
        "expires_at": on_the_wire(clock.now() + TTL),
        "redeemed_at": None,
        "code": issued["code"],
        "balance_cr": format_cr(WEALTH - PRICE),
    }
    assert await factory.balance_micro(alice) == WEALTH - PRICE
    assert await parked(container) == (PRICE, 0)
    booked, postings = await entry(container, voucher_issue_key(voucher_id))
    assert (booked.kind, booked.ref_type, booked.ref_id) == (ISSUE, "voucher", voucher_id)
    assert booked.created_at == clock.now()
    assert postings == {USER: -PRICE, PENDING: PRICE}
    assert await voucher_entries(container) == [ISSUE]
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("product", PRODUCTS, ids=lambda product: product.id)
async def test_every_product_of_the_settings_can_be_had_for_its_own_price(
    acting: Acting, factory: Factory, container: Container, product: VoucherProduct
) -> None:
    alice = await factory.user(balance_micro=WEALTH)

    issued = await acting.get_voucher(alice, product)

    assert (issued["product_id"], issued["product_name"]) == (product.id, product.name)
    assert issued["value_cr"] == format_cr(product.price_micro)
    [row] = await rows(container, alice)
    assert (row.product_id, row.product_name, row.value_micro) == (
        product.id,
        product.name,
        product.price_micro,
    )
    assert await factory.balance_micro(alice) == WEALTH - product.price_micro
    assert await parked(container) == (product.price_micro, 0)
    await assert_books_balance(container.uow_factory)


async def test_the_code_of_a_voucher_is_answered_once_and_only_its_hash_is_stored(
    acting: Acting,
    factory: Factory,
    container: Container,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    alice = await factory.user(balance_micro=WEALTH)

    issued = await acting.get_voucher(alice)

    code = issued["code"]
    plain = normalize_code(code)
    assert plain is not None
    assert len(plain) == CODE_LENGTH
    assert code == format_code(plain), "shown in groups of four"
    [row] = await rows(container, alice)
    assert row.code_hash == hash_code(plain)
    stored = await everything_stored(container)
    assert row.code_hash in stored
    assert code not in stored
    assert plain not in stored
    listed = (await acting.wallet(alice)).text
    assert code not in listed
    assert plain not in listed
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert code not in logged
    assert plain not in logged
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "product_id",
    ["espresso", PRODUCT.id.upper(), PRODUCT.name, f"{PRODUCT.id}-large"],
    ids=["never-offered", "its-id-in-capitals", "its-name", "a-longer-id"],
)
async def test_an_unknown_product_is_422_on_product_id_and_books_nothing(
    acting: Acting, factory: Factory, container: Container, product_id: str
) -> None:
    alice = await factory.user(balance_micro=WEALTH)

    response = await acting.ask_for_voucher(alice, product_id)

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": UNKNOWN_PRODUCT_MESSAGE,
        "details": {"field": "product_id"},
    }
    assert UNKNOWN_PRODUCT_MESSAGE == "Choose one of the products on offer."
    assert await rows(container, alice) == []
    assert await voucher_entries(container) == []
    assert await factory.balance_micro(alice) == WEALTH
    assert await parked(container) == (0, 0)


async def test_a_balance_below_the_price_is_insufficient_balance(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """One millionth of a credit short is short. The answer says what is there and
    what the product takes."""
    short_micro = PRICE - 1
    alice = await factory.user(balance_micro=short_micro)

    response = await acting.ask_for_voucher(alice, PRODUCT.id)

    assert response.status_code == 409
    assert response.json() == {
        "code": "INSUFFICIENT_BALANCE",
        "message": InsufficientBalanceError.default_message,
        "details": {"balance_cr": format_cr(short_micro), "required_cr": format_cr(PRICE)},
    }
    assert await rows(container, alice) == []
    assert await voucher_entries(container) == []
    assert await factory.balance_micro(alice) == short_micro
    assert await parked(container) == (0, 0)
    await assert_books_balance(container.uow_factory)


async def test_a_balance_of_exactly_the_price_gets_the_voucher_and_no_second_one(
    acting: Acting, factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=CHEAPEST.price_micro)

    issued = await acting.get_voucher(alice, CHEAPEST)
    second = await acting.ask_for_voucher(alice, CHEAPEST.id)

    assert issued["balance_cr"] == "0"
    assert second.status_code == 409
    assert second.json()["code"] == "INSUFFICIENT_BALANCE"
    assert await factory.balance_micro(alice) == 0
    assert await parked(container) == (CHEAPEST.price_micro, 0)
    assert await voucher_entries(container) == [ISSUE]
    await assert_books_balance(container.uow_factory)


async def test_asking_twice_issues_two_vouchers_each_with_a_code_of_its_own(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """`POST /vouchers` carries no idempotency key (see `VoucherService.issue`)."""
    alice = await factory.user(balance_micro=WEALTH)

    first = await acting.get_voucher(alice)
    second = await acting.get_voucher(alice)

    assert first["id"] != second["id"]
    assert first["code"] != second["code"]
    assert second["balance_cr"] == format_cr(WEALTH - 2 * PRICE)
    assert await parked(container) == (2 * PRICE, 0)
    assert await voucher_entries(container) == [ISSUE, ISSUE]
    await assert_books_balance(container.uow_factory)


# --- The snapshot -----------------------------------------------------------------


async def test_a_voucher_keeps_the_name_and_the_price_it_was_issued_with(
    acting: Acting,
    factory: Factory,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VOUCHER_PRODUCTS is edited and the API restarted: the product has another name
    and takes twice as much. The voucher that exists is listed, scanned and booked as
    what it was."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    renamed = replace(PRODUCT, name=f"{PRODUCT.name} (large)", price_micro=2 * PRICE)
    products_now = [renamed if product.id == PRODUCT.id else product for product in PRODUCTS]
    monkeypatch.setattr(container, "vouchers", with_products(container, products_now))

    offered = {item["id"]: item for item in (await acting.products(alice)).json()["items"]}
    another = await acting.get_voucher(alice, renamed)
    listed = {item["id"]: item for item in (await acting.wallet(alice)).json()["items"]}
    scanned = await acting.scan(barista, issued["code"])

    # The settings in force are the edited ones.
    assert offered[PRODUCT.id]["name"] == renamed.name
    assert offered[PRODUCT.id]["price_cr"] == format_cr(renamed.price_micro)
    assert (another["product_name"], another["value_cr"]) == (
        renamed.name,
        format_cr(renamed.price_micro),
    )
    # The voucher from before is untouched by them.
    assert listed[issued["id"]]["product_name"] == PRODUCT.name
    assert listed[issued["id"]]["value_cr"] == format_cr(PRICE)
    assert scanned.status_code == 200
    assert scanned.json()["product_name"] == PRODUCT.name
    assert scanned.json()["value_cr"] == format_cr(PRICE)
    _, postings = await entry(container, voucher_redeem_key(UUID(issued["id"])))
    assert postings == {PENDING: -PRICE, REDEEMED: PRICE}
    assert await parked(container) == (renamed.price_micro, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_voucher_of_a_product_that_left_the_list_is_still_scanned_and_refunded(
    acting: Acting,
    factory: Factory,
    container: Container,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refund is what the voucher took: no product is left to ask for a price."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    to_scan = await acting.get_voucher(alice)
    to_expire = await acting.get_voucher(alice)
    remaining = [product for product in PRODUCTS if product.id != PRODUCT.id]
    without = with_products(container, remaining)
    monkeypatch.setattr(container, "vouchers", without)

    offered = (await acting.products(alice)).json()["items"]
    asked = await acting.ask_for_voucher(alice, PRODUCT.id)
    scanned = await acting.scan(barista, to_scan["code"])
    clock.advance(TTL)
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(alice.id, for_update=True)
        assert account is not None
        refunded = await without.expire_due(uow, account)
    listed = (await acting.wallet(alice)).json()["items"]

    assert PRODUCT.id not in {item["id"] for item in offered}
    assert asked.status_code == 422
    assert scanned.status_code == 200
    assert scanned.json()["product_id"] == PRODUCT.id
    assert scanned.json()["product_name"] == PRODUCT.name
    assert refunded == 1
    _, postings = await entry(container, voucher_refund_key(UUID(to_expire["id"])))
    assert postings == {PENDING: -PRICE, USER: PRICE}
    assert [(item["product_name"], item["value_cr"], item["status"]) for item in listed] == [
        (PRODUCT.name, format_cr(PRICE), "EXPIRED"),
        (PRODUCT.name, format_cr(PRICE), "REDEEMED"),
    ]
    assert await factory.balance_micro(alice) == WEALTH - PRICE
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


# --- The wallet -------------------------------------------------------------------


async def test_the_wallet_lists_the_callers_own_vouchers_newest_first_and_never_a_code(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    bob = await factory.user(balance_micro=WEALTH)
    first = await acting.get_voucher(alice, CHEAPEST)
    clock.advance(timedelta(hours=1))
    second = await acting.get_voucher(alice, DEAREST)
    bobs = await acting.get_voucher(bob)

    response = await acting.wallet(alice)

    assert response.status_code == 200
    without_code = [
        {field: value for field, value in issued.items() if field not in ("code", "balance_cr")}
        for issued in (second, first)
    ]
    assert response.json() == {"items": without_code}
    for issued in (first, second, bobs):
        assert issued["code"] not in response.text
        assert issued["code"].replace("-", "") not in response.text
    assert (await acting.wallet(bob)).json()["items"][0]["id"] == bobs["id"]
    await assert_books_balance(container.uow_factory)


async def test_a_wallet_without_vouchers_is_an_empty_list(acting: Acting, factory: Factory) -> None:
    response = await acting.wallet(await factory.user())

    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_a_voucher_past_its_expiry_is_listed_expired_before_its_refund_is_booked(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The effective status: reading the wallet decides nothing and books nothing."""
    alice = await factory.user(balance_micro=WEALTH)
    await acting.get_voucher(alice)

    clock.advance(TTL - INSTANT)
    just_before = await acting.statuses(alice)
    clock.advance(INSTANT)
    at_expiry = await acting.statuses(alice)

    assert just_before == ["ACTIVE"]
    assert at_expiry == ["EXPIRED"]
    [row] = await rows(container, alice)
    assert row.status is VoucherStatus.ACTIVE, "the row waits for its refund"
    assert row.expires_at == clock.now()
    assert await voucher_entries(container) == [ISSUE]
    assert await factory.balance_micro(alice) == WEALTH - PRICE
    assert await parked(container) == (PRICE, 0)
    await assert_books_balance(container.uow_factory)


async def test_the_wallet_says_whether_an_expired_voucher_has_its_value_back(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """`refunded` is true for an EXPIRED voucher whose refund is booked, and false for
    everything else: a voucher that is ACTIVE, one that is REDEEMED, and one that is
    past its expiry while the refund is still to come."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    kept = await acting.get_voucher(alice)
    used = await acting.get_voucher(alice)
    assert (kept["refunded"], used["refunded"]) == (False, False)
    assert (await acting.scan(barista, used["code"])).status_code == 200

    async def listed() -> list[tuple[str, bool]]:
        items = (await acting.wallet(alice)).json()["items"]
        return [(item["status"], item["refunded"]) for item in reversed(items)]

    while_valid = await listed()
    clock.advance(TTL)
    past_its_expiry = await listed()
    balance_before_the_refund = await factory.balance_micro(alice)
    assert await expire_due(container, alice) == 1
    once_refunded = await listed()

    assert while_valid == [("ACTIVE", False), ("REDEEMED", False)]
    assert past_its_expiry == [("EXPIRED", False), ("REDEEMED", False)]
    assert once_refunded == [("EXPIRED", True), ("REDEEMED", False)]
    assert balance_before_the_refund == WEALTH - 2 * PRICE
    assert await factory.balance_micro(alice) == WEALTH - PRICE
    assert await voucher_entries(container) == [ISSUE, ISSUE, REDEEM, REFUND]
    await assert_books_balance(container.uow_factory)


# --- A scan behind the bar --------------------------------------------------------


async def test_a_scan_marks_the_voucher_redeemed_and_moves_its_value_to_redeemed(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    voucher_id = UUID(issued["id"])
    clock.advance(timedelta(days=3))

    response = await acting.scan(barista, issued["code"])

    assert response.status_code == 200
    assert response.json() == {
        "voucher_id": str(voucher_id),
        "product_id": PRODUCT.id,
        "product_name": PRODUCT.name,
        "value_cr": format_cr(PRICE),
        "redeemed_at": on_the_wire(clock.now()),
        "repeat": False,
    }
    [row] = await rows(container, alice)
    assert (row.status, row.redeemed_by, row.redeemed_at) == (
        VoucherStatus.REDEEMED,
        barista.id,
        clock.now(),
    )
    booked, postings = await entry(container, voucher_redeem_key(voucher_id))
    assert (booked.kind, booked.ref_type, booked.ref_id) == (REDEEM, "voucher", voucher_id)
    assert postings == {PENDING: -PRICE, REDEEMED: PRICE}
    assert await parked(container) == (0, PRICE)
    assert await factory.balance_micro(alice) == WEALTH - PRICE, "a scan moves VIS's accounts"
    [listed] = (await acting.wallet(alice)).json()["items"]
    assert (listed["status"], listed["redeemed_at"]) == ("REDEEMED", on_the_wire(clock.now()))
    await assert_books_balance(container.uow_factory)


async def test_a_code_that_is_scanned_is_written_to_no_log_and_no_table(
    acting: Acting,
    factory: Factory,
    container: Container,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither the code of a voucher nor what a scanner read in its place is kept."""
    caplog.set_level(logging.DEBUG)
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    misread = format_code(generate_code(CODE_LENGTH))

    rejected = await acting.scan(barista, misread)
    accepted = await acting.scan(barista, issued["code"])

    assert (rejected.status_code, accepted.status_code) == (400, 200)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    stored = await everything_stored(container)
    for code in (misread, issued["code"]):
        for anywhere in (logged, stored, rejected.text, accepted.text):
            assert code not in anywhere
            assert code.replace("-", "") not in anywhere
    await assert_books_balance(container.uow_factory)


async def test_an_admin_scans_a_voucher_like_a_barista(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Nobody has to be given the role of a barista: an admin can redeem vouchers
    too."""
    alice = await factory.user(balance_micro=WEALTH)
    admin = await factory.admin()
    issued = await acting.get_voucher(alice)

    response = await acting.scan(admin, issued["code"])

    assert response.status_code == 200
    [row] = await rows(container, alice)
    assert (row.status, row.redeemed_by) == (VoucherStatus.REDEEMED, admin.id)
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_the_barista_who_redeemed_a_voucher_is_shown_the_redemption_again_inside_the_window(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The answer to a scan can get lost on its way, and the barista scans again. Up to
    VOUCHER_RESCAN_SECONDS after the redemption, its last instant included, the same
    barista gets the answer of the first scan with `repeat` true: what to hand over, and
    when it was redeemed, which is the first scan's instant and not the repeat's.

    The books and the voucher's row are as the first scan left them: a repeat changes
    no row of any table."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    first = await acting.scan(barista, issued["code"])
    redeemed_at = clock.now()
    after_the_first = await everything_stored(container)

    at_once = await acting.scan(barista, issued["code"])
    clock.advance(RESCAN - INSTANT)
    near_the_end = await acting.scan(barista, issued["code"].lower().replace("-", " "))
    clock.advance(INSTANT)
    at_the_last_instant = await acting.scan(barista, issued["code"])

    assert first.status_code == 200
    assert first.json()["repeat"] is False
    for again in (at_once, near_the_end, at_the_last_instant):
        assert again.status_code == 200, again.text
        assert again.json() == first.json() | {"repeat": True}
        assert again.json()["redeemed_at"] == on_the_wire(redeemed_at)
    assert clock.now() == redeemed_at + RESCAN
    assert await everything_stored(container) == after_the_first
    [row] = await rows(container, alice)
    assert (row.status, row.redeemed_by, row.redeemed_at) == (
        VoucherStatus.REDEEMED,
        barista.id,
        redeemed_at,
    )
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    assert await factory.balance_micro(alice) == WEALTH - PRICE
    await assert_books_balance(container.uow_factory)


async def test_after_the_window_the_barista_who_redeemed_a_voucher_gets_the_generic_answer(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """One instant past VOUCHER_RESCAN_SECONDS the voucher is used up to its barista as
    to anybody, and it stays so."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    assert (await acting.scan(barista, issued["code"])).status_code == 200
    redeemed_at = clock.now()
    after_the_first = await everything_stored(container)

    clock.advance(RESCAN + INSTANT)
    just_after = await acting.scan(barista, issued["code"])
    clock.advance(timedelta(minutes=5))
    later = await acting.scan(barista, issued["code"])

    for answer in (just_after, later):
        assert answer.status_code == 400
        assert answer.json() == CODE_INVALID
    assert await everything_stored(container) == after_the_first
    [row] = await rows(container, alice)
    assert (row.redeemed_by, row.redeemed_at) == (barista.id, redeemed_at)
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_scan_of_a_redeemed_voucher_by_anybody_else_is_rejected_inside_the_window_too(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Another barista and two admins: each is somebody else, and gets the generic
    answer right after the redemption as at the end of the window. Only the barista
    who redeemed the voucher is told about it again."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    others = [await factory.barista(1), await factory.admin(), await factory.admin(1)]
    issued = await acting.get_voucher(alice)
    first = await acting.scan(barista, issued["code"])
    redeemed_at = clock.now()
    after_the_first = await everything_stored(container)

    at_once = [await acting.scan(other, issued["code"]) for other in others]
    clock.advance(RESCAN)
    at_the_end = [await acting.scan(other, issued["code"]) for other in others]
    by_the_barista = await acting.scan(barista, issued["code"])

    assert first.status_code == 200
    for answer in (*at_once, *at_the_end):
        assert answer.status_code == 400
        assert answer.json() == CODE_INVALID
        assert str(issued["id"]) not in answer.text, "nothing about the voucher"
    assert by_the_barista.json() == first.json() | {"repeat": True}
    assert await everything_stored(container) == after_the_first
    [row] = await rows(container, alice)
    assert (row.redeemed_by, row.redeemed_at) == (barista.id, redeemed_at)
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_code_that_was_replaced_is_no_repeat_and_the_one_that_redeemed_is(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A repeat is found by the code the voucher has. The code it had before its owner
    asked for a fresh one finds nothing, also for the barista who just redeemed that
    very voucher."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    fresh = (await acting.fresh_code(alice, issued["id"])).json()["code"]
    first = await acting.scan(barista, fresh)
    after_the_first = await everything_stored(container)

    with_the_replaced_code = await acting.scan(barista, issued["code"])
    with_the_code_that_redeemed = await acting.scan(barista, fresh)

    assert first.status_code == 200
    assert with_the_replaced_code.status_code == 400
    assert with_the_replaced_code.json() == CODE_INVALID
    assert with_the_code_that_redeemed.json() == first.json() | {"repeat": True}
    assert await everything_stored(container) == after_the_first
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_only_a_redeemed_voucher_is_repeated_an_expired_one_is_rejected_at_once(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The scan of an expired voucher redeems nothing, so there is nothing to show
    again: the same barista scanning it a second time gets the generic answer."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    clock.advance(TTL)

    first = await acting.scan(barista, issued["code"])
    again = await acting.scan(barista, issued["code"])

    for answer in (first, again):
        assert answer.status_code == 400
        assert answer.json() == CODE_INVALID
    assert await voucher_entries(container) == [ISSUE, REFUND]
    assert await factory.balance_micro(alice) == WEALTH
    assert await parked(container) == (0, 0)
    await assert_books_balance(container.uow_factory)


async def test_a_wrong_a_redeemed_a_replaced_and_an_expired_code_all_get_the_same_answer(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """A voucher cannot be probed (A.6): nothing in the answer says which it was."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    used = await acting.get_voucher(alice)
    replaced = await acting.get_voucher(alice)
    expired = await acting.get_voucher(alice)
    assert (await acting.scan(barista, used["code"])).status_code == 200
    assert (await acting.fresh_code(alice, replaced["id"])).status_code == 200
    clock.advance(TTL)
    scans = {
        "never issued": generate_code(CODE_LENGTH),
        "redeemed": used["code"],
        "replaced": replaced["code"],
        "expired": expired["code"],
        "expired and refunded": expired["code"],
        "one character short": used["code"][:-1],
        "one character long": f"{used['code']}0",
        "not Crockford Base32": "U" * CODE_LENGTH,
        "the id of a voucher": used["id"],
        "a link": f"{container.settings.app.public_base_url}/wallet?code={used['code']}",
    }

    answers = {what: await acting.scan(barista, code) for what, code in scans.items()}

    for what, answer in answers.items():
        assert answer.status_code == 400, what
        assert answer.json() == CODE_INVALID, what
        assert answer.headers["content-type"] == "application/json", what
    await assert_books_balance(container.uow_factory)


def _lower_case(code: str) -> str:
    return format_code(code).lower()


def _without_its_dash(code: str) -> str:
    return code


def _with_spaces_around_and_inside(code: str) -> str:
    return f"  {code[:3]} {code[3:]}\t"


def _with_letters_for_digits(code: str) -> str:
    return format_code(code).replace("0", "O").replace("1", "I")


def _with_a_lower_case_l_for_one(code: str) -> str:
    return format_code(code).replace("0", "o").replace("1", "l")


@pytest.mark.parametrize(
    "typed",
    [
        _lower_case,
        _without_its_dash,
        _with_spaces_around_and_inside,
        _with_letters_for_digits,
        _with_a_lower_case_l_for_one,
    ],
    ids=lambda typed: typed.__name__.strip("_").replace("_", "-"),
)
async def test_a_code_is_read_however_it_was_scanned_or_typed(
    acting: Acting,
    factory: Factory,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
    typed: Callable[[str], str],
) -> None:
    """Case, dashes and spaces do not matter, O is read as 0, I and L as 1 (SPEC §2)."""
    with_a_zero_and_a_one = ("01" + "A" * CODE_LENGTH)[:CODE_LENGTH]
    monkeypatch.setattr("app.services.vouchers.generate_code", lambda length: with_a_zero_and_a_one)
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    assert issued["code"] == format_code(with_a_zero_and_a_one)

    response = await acting.scan(barista, typed(with_a_zero_and_a_one))

    assert response.status_code == 200, response.text
    assert response.json()["voucher_id"] == issued["id"]
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("scans", [2, 8])
async def test_parallel_scans_of_one_voucher_redeem_it_once(
    acting: Acting, factory: Factory, container: Container, scans: int
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    scanners = [
        factory.identity(await factory.barista()),
        factory.identity(await factory.admin()),
        factory.identity(await factory.admin(1)),
    ]
    issued = await acting.get_voucher(alice)

    results = await asyncio.gather(
        *(
            container.vouchers.redeem(scanners[turn % len(scanners)], issued["code"])
            for turn in range(scans)
        ),
        return_exceptions=True,
    )

    answered = [result for result in results if isinstance(result, VoucherRedeemResponse)]
    rejected = [result for result in results if isinstance(result, CodeInvalidError)]
    assert len(answered) + len(rejected) == scans, results
    # One scan redeems. Whoever sent it gets a repeat for every other scan of theirs,
    # and everybody else is rejected.
    assert [answer.repeat for answer in answered].count(False) == 1, results
    [row] = await rows(container, alice)
    assert row.status is VoucherStatus.REDEEMED
    assert row.redeemed_by in {scanner.id for scanner in scanners}
    by_the_redeemer = sum(
        scanners[turn % len(scanners)].id == row.redeemed_by for turn in range(scans)
    )
    assert len(answered) == by_the_redeemer
    assert {answer.redeemed_at for answer in answered} == {row.redeemed_at}
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_two_scans_of_one_barista_through_the_api_at_once_redeem_once_and_repeat_once(
    acting: Acting,
    client: httpx.AsyncClient,
    as_user: ActAs,
    factory: Factory,
    container: Container,
) -> None:
    """A double tap on the scan button: one answer redeems and the other says that it
    is a repeat of that redemption. One entry is booked."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    as_user(barista)

    answers = await asyncio.gather(
        client.post(SCAN_PATH, json={"code": issued["code"]}),
        client.post(SCAN_PATH, json={"code": issued["code"]}),
    )

    assert [answer.status_code for answer in answers] == [200, 200]
    assert sorted(answer.json()["repeat"] for answer in answers) == [False, True]
    first, second = (answer.json() | {"repeat": None} for answer in answers)
    assert first == second, "the two answers differ in `repeat` alone"
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


# --- Expiry -----------------------------------------------------------------------


async def test_a_voucher_can_be_redeemed_until_the_instant_it_expires(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    in_time = await acting.get_voucher(alice)
    too_late = await acting.get_voucher(alice)

    clock.advance(TTL - INSTANT)
    last_moment = await acting.scan(barista, in_time["code"])
    clock.advance(INSTANT)
    at_expiry = await acting.scan(barista, too_late["code"])

    assert last_moment.status_code == 200
    assert at_expiry.status_code == 400
    assert at_expiry.json() == CODE_INVALID
    assert await acting.statuses(alice) == ["REDEEMED", "EXPIRED"]
    assert await voucher_entries(container) == [ISSUE, ISSUE, REDEEM, REFUND]
    await assert_books_balance(container.uow_factory)


async def test_scanning_an_expired_voucher_refunds_it_and_is_rejected_like_any_other(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The refund commits although the scan fails, and it is booked once."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    voucher_id = UUID(issued["id"])
    clock.advance(TTL + timedelta(days=2))

    response = await acting.scan(barista, issued["code"])

    assert response.status_code == 400
    assert response.json() == CODE_INVALID
    [row] = await rows(container, alice)
    assert (row.status, row.redeemed_by, row.redeemed_at) == (VoucherStatus.EXPIRED, None, None)
    booked, postings = await entry(container, voucher_refund_key(voucher_id))
    assert (booked.kind, booked.ref_type, booked.ref_id) == (REFUND, "voucher", voucher_id)
    assert booked.created_at == clock.now()
    assert postings == {PENDING: -PRICE, USER: PRICE}
    assert await factory.balance_micro(alice) == WEALTH
    assert await parked(container) == (0, 0)

    again = await acting.scan(barista, issued["code"])
    by_upkeep = await expire_due(container, alice)

    assert again.status_code == 400
    assert again.json() == CODE_INVALID
    assert by_upkeep == 0
    assert await voucher_entries(container) == [ISSUE, REFUND]
    assert await factory.balance_micro(alice) == WEALTH
    await assert_books_balance(container.uow_factory)


async def test_expire_due_refunds_exactly_the_owners_expired_active_vouchers(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    bob = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    expired_one = await acting.get_voucher(alice, DEAREST)
    expired_two = await acting.get_voucher(alice, CHEAPEST)
    used = await acting.get_voucher(alice)
    await acting.get_voucher(bob)
    assert (await acting.scan(barista, used["code"])).status_code == 200
    clock.advance(TTL - timedelta(days=1))
    await acting.get_voucher(alice, CHEAPEST)
    clock.advance(timedelta(days=1))

    refunded = await expire_due(container, alice)

    assert refunded == 2
    assert [row.status for row in await rows(container, alice)] == [
        VoucherStatus.EXPIRED,
        VoucherStatus.EXPIRED,
        VoucherStatus.REDEEMED,
        VoucherStatus.ACTIVE,
    ]
    for issued, product in ((expired_one, DEAREST), (expired_two, CHEAPEST)):
        booked, postings = await entry(container, voucher_refund_key(UUID(issued["id"])))
        assert (booked.kind, booked.created_at) == (REFUND, clock.now())
        assert postings == {PENDING: -product.price_micro, USER: product.price_micro}
    # Alice is out what her redeemed and her young voucher took, and nothing else.
    assert await factory.balance_micro(alice) == WEALTH - PRICE - CHEAPEST.price_micro
    # Bob's voucher expired too. It waits for a request of his own.
    [bobs] = await rows(container, bob)
    assert bobs.status is VoucherStatus.ACTIVE
    assert await factory.balance_micro(bob) == WEALTH - PRICE
    assert await parked(container) == (CHEAPEST.price_micro + PRICE, PRICE)

    assert await expire_due(container, alice) == 0
    assert (await voucher_entries(container)).count(REFUND) == 2
    await assert_books_balance(container.uow_factory)


async def test_expire_due_touches_nothing_while_no_voucher_has_expired(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    await acting.get_voucher(alice)
    clock.advance(TTL - INSTANT)

    assert await expire_due(container, alice) == 0
    assert await expire_due(container, await factory.user()) == 0

    assert [row.status for row in await rows(container, alice)] == [VoucherStatus.ACTIVE]
    assert await voucher_entries(container) == [ISSUE]
    assert await parked(container) == (PRICE, 0)
    await assert_books_balance(container.uow_factory)


async def test_expire_due_refunds_to_a_users_account_and_to_no_other(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    await acting.get_voucher(alice)
    clock.advance(TTL)

    async with container.uow_factory() as uow:
        for kind in (PENDING, AccountKind.VIS_ISSUANCE):
            not_a_users = await uow.accounts.get_system_account(kind)
            with pytest.raises(ValueError, match="is no user's"):
                await container.vouchers.expire_due(uow, not_a_users)

    assert await voucher_entries(container) == [ISSUE]
    assert await parked(container) == (PRICE, 0)
    await assert_books_balance(container.uow_factory)


async def test_a_scan_and_the_upkeep_at_once_refund_an_expired_voucher_once_between_them(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Whichever comes first books the refund (A.6) and the other finds nothing left to
    do. Both take the owner's account before the voucher row, so neither waits for the
    other in a circle."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = factory.identity(await factory.barista())
    rounds = 6

    for turn in range(rounds):
        issued = await acting.get_voucher(alice)
        clock.advance(TTL)
        scan = container.vouchers.redeem(barista, issued["code"])
        upkeep = expire_due(container, alice)
        both = (scan, upkeep) if turn % 2 else (upkeep, scan)
        results = await asyncio.gather(*both, return_exceptions=True)
        scanned, refunded_by_upkeep = results if turn % 2 else reversed(results)

        assert isinstance(scanned, CodeInvalidError), scanned
        assert refunded_by_upkeep in (0, 1), refunded_by_upkeep
        assert await factory.balance_micro(alice) == WEALTH

    assert await acting.statuses(alice) == ["EXPIRED"] * rounds
    assert await voucher_entries(container) == [ISSUE, REFUND] * rounds
    assert await parked(container) == (0, 0)
    await assert_books_balance(container.uow_factory)


# --- A fresh code -----------------------------------------------------------------


async def test_a_fresh_code_redeems_and_the_code_it_replaced_stops_working(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    clock.advance(timedelta(days=2))

    response = await acting.fresh_code(alice, issued["id"])

    assert response.status_code == 200
    fresh = response.json()["code"]
    assert response.json() == {
        "voucher_id": issued["id"],
        "product_name": PRODUCT.name,
        "code": fresh,
        "expires_at": issued["expires_at"],
    }
    assert fresh != issued["code"]
    plain = normalize_code(fresh)
    assert plain is not None
    assert len(plain) == CODE_LENGTH
    assert fresh == format_code(plain)
    # Nothing but the hash changed: no credit moved and the voucher expires when it did.
    [row] = await rows(container, alice)
    assert row.code_hash == hash_code(plain)
    assert (row.status, on_the_wire(row.expires_at)) == (
        VoucherStatus.ACTIVE,
        issued["expires_at"],
    )
    assert fresh not in await everything_stored(container)
    assert await voucher_entries(container) == [ISSUE]
    assert await factory.balance_micro(alice) == WEALTH - PRICE

    with_the_code_it_had = await acting.scan(barista, issued["code"])
    with_the_fresh_one = await acting.scan(barista, fresh)

    assert with_the_code_it_had.status_code == 400
    assert with_the_code_it_had.json() == CODE_INVALID
    assert with_the_fresh_one.status_code == 200
    assert with_the_fresh_one.json()["voucher_id"] == issued["id"]
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_of_several_fresh_codes_only_the_last_one_works(
    acting: Acting, factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    codes = [issued["code"]]
    for _ in range(3):
        codes.append((await acting.fresh_code(alice, issued["id"])).json()["code"])

    answers = [await acting.scan(barista, code) for code in codes]

    assert len(set(codes)) == len(codes)
    assert [answer.status_code for answer in answers] == [400, 400, 400, 200]
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    await assert_books_balance(container.uow_factory)


async def test_somebody_elses_voucher_is_404_like_one_that_does_not_exist_and_its_code_still_works(
    acting: Acting, factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    mallory = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    [before] = await rows(container, alice)

    somebody_elses = await acting.fresh_code(mallory, issued["id"])
    unknown = await acting.fresh_code(mallory, uuid7())

    for answer in (somebody_elses, unknown):
        assert answer.status_code == 404
        assert answer.json() == NOT_FOUND
    [after] = await rows(container, alice)
    assert after.code_hash == before.code_hash
    assert await rows(container, mallory) == []
    scanned = await acting.scan(barista, issued["code"])
    assert scanned.status_code == 200
    assert scanned.json()["voucher_id"] == issued["id"]
    await assert_books_balance(container.uow_factory)


async def test_a_redeemed_voucher_takes_no_fresh_code(
    acting: Acting, factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice)
    assert (await acting.scan(barista, issued["code"])).status_code == 200
    [before] = await rows(container, alice)

    response = await acting.fresh_code(alice, issued["id"])

    assert response.status_code == 409
    assert response.json() == CONFLICT
    [after] = await rows(container, alice)
    assert (after.code_hash, after.status) == (before.code_hash, VoucherStatus.REDEEMED)
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    await assert_books_balance(container.uow_factory)


async def test_an_expired_voucher_takes_no_fresh_code_and_asking_books_no_refund(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Past its expiry and again once the refund is booked: 409 both times. The refund
    is the upkeep's or a scan's to book, not this request's."""
    alice = await factory.user(balance_micro=WEALTH)
    issued = await acting.get_voucher(alice)
    [before] = await rows(container, alice)
    clock.advance(TTL - INSTANT)
    assert (await acting.fresh_code(alice, issued["id"])).status_code == 200
    [replaced] = await rows(container, alice)
    clock.advance(INSTANT)

    past_expiry = await acting.fresh_code(alice, issued["id"])

    assert past_expiry.status_code == 409
    assert past_expiry.json() == CONFLICT
    [unrefunded] = await rows(container, alice)
    assert replaced.code_hash != before.code_hash
    assert (unrefunded.code_hash, unrefunded.status) == (replaced.code_hash, VoucherStatus.ACTIVE)
    assert await voucher_entries(container) == [ISSUE]
    assert await factory.balance_micro(alice) == WEALTH - PRICE

    assert await expire_due(container, alice) == 1
    once_refunded = await acting.fresh_code(alice, issued["id"])

    assert once_refunded.status_code == 409
    assert once_refunded.json() == CONFLICT
    assert await factory.balance_micro(alice) == WEALTH
    await assert_books_balance(container.uow_factory)


async def test_a_scan_and_a_fresh_code_at_once_never_both_take_effect(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Both lock the owner's account before the voucher row, so one runs first: either
    the scan redeems and the voucher takes no code, or the code is replaced and the scan
    of the one it had fails."""
    alice_row = await factory.user(balance_micro=WEALTH)
    alice = factory.identity(alice_row)
    barista_row = await factory.barista()
    barista = factory.identity(barista_row)
    outcomes: set[str] = set()

    for turn in range(6):
        issued = await acting.get_voucher(alice_row)
        voucher_id = UUID(issued["id"])
        scan = container.vouchers.redeem(barista, issued["code"])
        fresh_code = container.vouchers.replace_code(alice, voucher_id)
        both = (scan, fresh_code) if turn % 2 else (fresh_code, scan)
        results = await asyncio.gather(*both, return_exceptions=True)
        scanned, replaced = results if turn % 2 else reversed(results)

        row = next(row for row in await rows(container, alice_row) if row.id == voucher_id)
        if isinstance(scanned, VoucherRedeemResponse):
            assert isinstance(replaced, ConflictError), replaced
            assert row.status is VoucherStatus.REDEEMED
            outcomes.add("the scan ran first")
        else:
            assert isinstance(scanned, CodeInvalidError), scanned
            assert isinstance(replaced, VoucherCodeResponse), replaced
            assert row.status is VoucherStatus.ACTIVE
            assert (await acting.scan(barista_row, replaced.code)).status_code == 200
            outcomes.add("the fresh code ran first")

    # Whichever ran first, every voucher ends redeemed exactly once.
    assert outcomes
    assert await acting.statuses(alice_row) == ["REDEEMED"] * 6
    assert (await voucher_entries(container)).count(REDEEM) == 6
    assert await parked(container) == (0, 6 * PRICE)
    await assert_books_balance(container.uow_factory)


# --- Locks ------------------------------------------------------------------------


@pytest.mark.parametrize("requests_at_once", [2, 5])
async def test_requests_at_once_cannot_turn_the_same_credits_into_two_vouchers(
    factory: Factory, container: Container, requests_at_once: int
) -> None:
    """The balance is read under the lock of the account, so the second request sees
    what the first one left."""
    alice_row = await factory.user(balance_micro=PRICE)
    alice = factory.identity(alice_row)
    request = IssueVoucherRequest(product_id=PRODUCT.id)

    results = await asyncio.gather(
        *(container.vouchers.issue(alice, request) for _ in range(requests_at_once)),
        return_exceptions=True,
    )

    refused = [result for result in results if isinstance(result, InsufficientBalanceError)]
    assert len(refused) == requests_at_once - 1, results
    for refusal in refused:
        assert refusal.details == {"balance_cr": to_cr(0), "required_cr": to_cr(PRICE)}
    assert len(await rows(container, alice_row)) == 1
    assert await voucher_entries(container) == [ISSUE]
    assert await factory.balance_micro(alice_row) == 0
    assert await parked(container) == (PRICE, 0)
    await assert_books_balance(container.uow_factory)


async def test_a_request_for_a_voucher_waits_for_the_callers_account_holding_nothing_else(
    factory: Factory, container: Container
) -> None:
    """The user's account is locked before anything of VIS (backend/CLAUDE.md,
    "Transactions"): while it is held, the request queues for it and
    `vis:redemption_pending` is free."""
    alice_row = await factory.user(balance_micro=WEALTH)
    alice = factory.identity(alice_row)
    pending_account = select(Account.id).where(Account.kind == PENDING)

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice_row.id, for_update=True)
        request = asyncio.create_task(
            container.vouchers.issue(alice, IssueVoucherRequest(product_id=PRODUCT.id))
        )
        await until_it_waits(container, request)
        vis_was_free = await lockable(container, pending_account)
    issued = await request

    assert vis_was_free
    assert issued.balance_cr == to_cr(WEALTH - PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_scan_waits_for_the_owners_account_before_it_locks_the_voucher_row(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """The lock order of every voucher operation (A.6): the owner's account, then the
    voucher row. While the account is held, a scan queues for it and holds neither the
    row nor an account of VIS."""
    alice = await factory.user(balance_micro=WEALTH)
    barista = factory.identity(await factory.barista())
    issued = await acting.get_voucher(alice)
    voucher_row = select(Voucher.id).where(Voucher.id == UUID(issued["id"]))
    pending_account = select(Account.id).where(Account.kind == PENDING)

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice.id, for_update=True)
        scan = asyncio.create_task(container.vouchers.redeem(barista, issued["code"]))
        await until_it_waits(container, scan)
        row_was_free = await lockable(container, voucher_row)
        vis_was_free = await lockable(container, pending_account)
    scanned = await scan

    assert row_was_free
    assert vis_was_free
    assert scanned.voucher_id == UUID(issued["id"])
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_repeated_scan_waits_for_the_owners_account_before_it_locks_the_voucher_row(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A repeat is decided on the locked row, reached in the order of every voucher
    operation: while the owner's account is held, the scan queues for it and does not
    hold the row."""
    alice = await factory.user(balance_micro=WEALTH)
    barista_row = await factory.barista()
    barista = factory.identity(barista_row)
    issued = await acting.get_voucher(alice)
    assert (await acting.scan(barista_row, issued["code"])).status_code == 200
    voucher_row = select(Voucher.id).where(Voucher.id == UUID(issued["id"]))

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice.id, for_update=True)
        scan = asyncio.create_task(container.vouchers.redeem(barista, issued["code"]))
        await until_it_waits(container, scan)
        row_was_free = await lockable(container, voucher_row)
    repeated = await scan

    assert row_was_free
    assert (repeated.voucher_id, repeated.repeat) == (UUID(issued["id"]), True)
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    assert await parked(container) == (0, PRICE)
    await assert_books_balance(container.uow_factory)


async def test_a_scan_is_rejected_when_its_code_passed_to_another_owners_voucher_meanwhile(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """A scan learns the owner from a first read and locks that owner's account. If
    the code belongs to a voucher of somebody else by the time the row is read again,
    the account that is locked is the wrong one: an expired voucher would be refunded
    to it. Codes are random, so only a test brings this about, by writing the hashes."""
    alice = await factory.user(balance_micro=WEALTH)
    bob = await factory.user(balance_micro=WEALTH)
    barista = factory.identity(await factory.barista())
    bobs = await acting.get_voucher(bob, DEAREST)
    clock.advance(TTL)
    alices = await acting.get_voucher(alice, CHEAPEST)
    scanned_code = normalize_code(alices["code"])
    assert scanned_code is not None

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice.id, for_update=True)
        scan = asyncio.create_task(container.vouchers.redeem(barista, alices["code"]))
        await until_it_waits(container, scan)
        # Alice's voucher gets another code, and the code being scanned becomes that of
        # Bob's voucher, which has expired.
        alices_row = await holder.vouchers.get_owned(UUID(alices["id"]), alice.id)
        bobs_row = await holder.vouchers.get_owned(UUID(bobs["id"]), bob.id)
        assert alices_row is not None
        assert bobs_row is not None
        alices_row.code_hash = hash_code(generate_code(CODE_LENGTH))
        await holder.flush()
        bobs_row.code_hash = hash_code(scanned_code)
    with pytest.raises(CodeInvalidError):
        await scan

    assert [row.status for row in await rows(container, bob)] == [VoucherStatus.ACTIVE]
    assert await voucher_entries(container) == [ISSUE, ISSUE]
    assert await factory.balance_micro(alice) == WEALTH - CHEAPEST.price_micro
    assert await factory.balance_micro(bob) == WEALTH - DEAREST.price_micro
    assert await parked(container) == (CHEAPEST.price_micro + DEAREST.price_micro, 0)
    await assert_books_balance(container.uow_factory)


async def test_a_fresh_code_waits_for_the_callers_account_before_it_locks_the_voucher_row(
    acting: Acting, factory: Factory, container: Container
) -> None:
    alice_row = await factory.user(balance_micro=WEALTH)
    alice = factory.identity(alice_row)
    barista = await factory.barista()
    issued = await acting.get_voucher(alice_row)
    voucher_id = UUID(issued["id"])
    voucher_row = select(Voucher.id).where(Voucher.id == voucher_id)

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice_row.id, for_update=True)
        fresh_code = asyncio.create_task(container.vouchers.replace_code(alice, voucher_id))
        await until_it_waits(container, fresh_code)
        row_was_free = await lockable(container, voucher_row)
    replaced = await fresh_code

    assert row_was_free
    assert (await acting.scan(barista, replaced.code)).status_code == 200
    await assert_books_balance(container.uow_factory)


async def test_a_fresh_code_for_somebody_elses_voucher_locks_only_the_callers_own_account(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Nothing a request names decides whose account is locked. While the owner's
    account and the voucher row are held, a stranger who asks for a code of that voucher
    is answered 404 at once: neither lock is one the request waits for."""
    alice = await factory.user(balance_micro=WEALTH)
    mallory = factory.identity(await factory.user())
    issued = await acting.get_voucher(alice)
    voucher_id = UUID(issued["id"])

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice.id, for_update=True)
        assert await holder.vouchers.get_owned(voucher_id, alice.id, for_update=True) is not None
        async with asyncio.timeout(LOCK_WAIT_SECONDS):
            with pytest.raises(NotFoundError):
                await container.vouchers.replace_code(mallory, voucher_id)

    await assert_books_balance(container.uow_factory)


# --- The same scan, the same answer -----------------------------------------------
#
# What a scan is answered depends on the voucher, on who scans and on the clock, and on
# nothing else. These tests go through the two headers of the portal, so each request
# is one of the real seam: `IdentityService`, the lazy upkeep of whoever scans, the
# guard, the route, the service.

FRESH_VOUCHERS: Final[int] = 50
"""How many vouchers the bar scans in a row."""


def _as_the_qr_carries_it(code: str) -> str:
    return code


def _typed_in_lower_case(code: str) -> str:
    return code.lower()


def _typed_without_the_dash(code: str) -> str:
    return code.replace("-", "")


def _typed_with_spaces(code: str) -> str:
    return f"  {code[:2]} {code[2:].replace('-', ' - ')}  "


def _read_with_a_line_end(code: str) -> str:
    return f"{code}\r\n"


def _typed_with_letters_for_digits(code: str) -> str:
    return code.replace("0", "O").replace("1", "l")


WRITTEN: Final[tuple[Callable[[str], str], ...]] = (
    _as_the_qr_carries_it,
    _typed_in_lower_case,
    _typed_without_the_dash,
    _typed_with_spaces,
    _read_with_a_line_end,
    _typed_with_letters_for_digits,
)
"""The ways a code reaches the API: as the QR of the wallet carries it, and as people
type it."""


class Bar:
    """The requests of a test that go through the portal's two headers."""

    def __init__(self, client: httpx.AsyncClient, headers: PortalHeaders) -> None:
        self._client = client
        self._headers = headers

    async def get_voucher(self, owner: User, product: VoucherProduct = PRODUCT) -> dict[str, Any]:
        response = await self._client.post(
            VOUCHERS_PATH, json={"product_id": product.id}, headers=self._headers(owner)
        )
        assert response.status_code == 201, response.text
        issued: dict[str, Any] = response.json()
        return issued

    async def fresh_code(self, owner: User, voucher_id: object) -> httpx.Response:
        return await self._client.post(
            f"{VOUCHERS_PATH}/{voucher_id}/code", headers=self._headers(owner)
        )

    async def scan(self, scanner: User, code: str) -> httpx.Response:
        return await self._client.post(
            SCAN_PATH, json={"code": code}, headers=self._headers(scanner)
        )


@pytest.fixture
def bar(client: httpx.AsyncClient, portal_headers: PortalHeaders) -> Bar:
    return Bar(client, portal_headers)


async def test_fifty_fresh_vouchers_in_a_row_are_each_accepted_repeated_and_then_refused(
    bar: Bar, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """One after another, each for another product and written another way. Every
    fresh voucher is accepted, with `repeat` false. The barista who scanned it gets
    the same answer with `repeat` true anywhere in the window, from the same instant
    to its last one. Another barista and an admin are refused inside the window, and
    everybody is once it is over. Not one of the three hundred answers is another."""
    total_micro = sum(
        PRODUCTS[number % len(PRODUCTS)].price_micro for number in range(FRESH_VOUCHERS)
    )
    guest = await factory.user(balance_micro=total_micro)
    barista = await factory.barista()
    others = [await factory.barista(1), await factory.admin()]

    for number in range(FRESH_VOUCHERS):
        product = PRODUCTS[number % len(PRODUCTS)]
        another = others[number % len(others)]
        scanned, scanned_again = (
            WRITTEN[number % len(WRITTEN)],
            WRITTEN[(number + 1) % len(WRITTEN)],
        )
        # From no time at all to the whole window, its last instant included.
        inside_the_window = RESCAN * number / (FRESH_VOUCHERS - 1)
        issued = await bar.get_voucher(guest, product)

        first = await bar.scan(barista, scanned(issued["code"]))
        redeemed_at = clock.now()
        clock.advance(inside_the_window)
        again = await bar.scan(barista, scanned_again(issued["code"]))
        by_another = await bar.scan(another, issued["code"])
        clock.set(redeemed_at + RESCAN + INSTANT)
        afterwards = await bar.scan(barista, issued["code"])
        afterwards_by_another = await bar.scan(another, issued["code"])

        assert first.status_code == 200, (number, first.text)
        assert first.json() == {
            "voucher_id": issued["id"],
            "product_id": product.id,
            "product_name": product.name,
            "value_cr": format_cr(product.price_micro),
            "redeemed_at": on_the_wire(redeemed_at),
            "repeat": False,
        }, number
        assert again.status_code == 200, (number, again.text)
        assert again.json() == first.json() | {"repeat": True}, number
        for refused in (by_another, afterwards, afterwards_by_another):
            assert refused.status_code == 400, (number, refused.text)
            assert refused.json() == CODE_INVALID, number

    assert RESCAN * (FRESH_VOUCHERS - 1) / (FRESH_VOUCHERS - 1) == RESCAN
    stored = await rows(container, guest)
    assert [(row.status, row.redeemed_by) for row in stored] == [
        (VoucherStatus.REDEEMED, barista.id)
    ] * FRESH_VOUCHERS
    assert await voucher_entries(container) == [ISSUE, REDEEM] * FRESH_VOUCHERS
    assert await parked(container) == (0, total_micro)
    assert await factory.balance_micro(guest) == 0
    await assert_books_balance(container.uow_factory)


async def test_what_is_no_voucher_code_is_refused_every_time_and_leaves_the_voucher_as_it_was(
    bar: Bar, factory: Factory, container: Container
) -> None:
    """A link, also one that holds the code of the very voucher, a code with a
    character too many or too few, one of another kind and plain text: asked three
    times each, refused three times each with the same answer, and the voucher is
    accepted afterwards as if nothing had been scanned. A text longer than a code can
    be is refused by the request model, 422 in place of 400, and that every time too."""
    guest = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await bar.get_voucher(guest)
    code: str = issued["code"]
    base = container.settings.app.public_base_url
    bare = code.replace("-", "")
    no_voucher = {
        "the link of a code card": f"{base}/wallet?code=ABCD-EFGH-JKMN",
        "a link that holds this code": f"{base}/wallet?code={code}",
        "a path that holds this code": f"/barista?code={code}",
        "this code and a character more": f"{code}0",
        "this code without its last character": code[:-1],
        "this code twice": f"{code} {code}",
        "this code with a letter no code has": f"{code[:-1]}U",
        "this code with an en dash": code.replace("-", "\u2013"),
        "this code with a long s in it": f"{bare[:-1]}\u017f",
        "a code of the other kind": generate_code(container.settings.codes.length),
        "the id of the voucher": issued["id"],
        "words": "one coffee please",
    }
    too_long = f"{base}/wallet?code={code}&from=a-poster-in-the-main-hall-of-the-eth"
    before = await everything_stored(container)

    for what, text_scanned in no_voucher.items():
        answers = [await bar.scan(barista, text_scanned) for _ in range(3)]
        assert [answer.status_code for answer in answers] == [400, 400, 400], what
        assert [answer.json() for answer in answers] == [CODE_INVALID] * 3, what
    overlong = [await bar.scan(barista, too_long) for _ in range(3)]

    assert len(too_long) > 64
    assert [answer.status_code for answer in overlong] == [422, 422, 422]
    assert {answer.json()["code"] for answer in overlong} == {"VALIDATION_ERROR"}
    assert {
        (tuple(error["loc"]), error["msg"])
        for answer in overlong
        for error in answer.json()["details"]["errors"]
    } == {(("body", "code"), "Use at most 64 characters.")}
    assert await everything_stored(container) == before
    accepted = await bar.scan(barista, code)
    assert (accepted.status_code, accepted.json()["repeat"]) == (200, False)
    await assert_books_balance(container.uow_factory)


async def test_a_code_that_is_replaced_while_its_scan_waits_is_refused_and_the_fresh_one_works(
    bar: Bar,
    factory: Factory,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guest taps "Show QR" while the bar scans the code the voucher had. The
    replacement holds the guest's account, so the scan, which has read the voucher by
    the code it was given, waits. Once the fresh code is committed the scan finds no
    voucher under its code: refused, at once and every time after. The fresh code
    is accepted. Which of the two came first decides, and nothing else does."""
    guest_row = await factory.user(balance_micro=WEALTH)
    guest = factory.identity(guest_row)
    barista_row = await factory.barista()
    barista = factory.identity(barista_row)
    issued = await bar.get_voucher(guest_row)
    shown_first: str = issued["code"]
    get_owned = VoucherRepository.get_owned
    holding, released = asyncio.Event(), asyncio.Event()

    async def held_until_released(self: VoucherRepository, *args: Any, **kwargs: Any) -> Any:
        found = await get_owned(self, *args, **kwargs)
        holding.set()
        await released.wait()
        return found

    monkeypatch.setattr(VoucherRepository, "get_owned", held_until_released)
    replacing = asyncio.create_task(container.vouchers.replace_code(guest, UUID(issued["id"])))
    # The replacement holds the guest's account and the voucher row before the scan starts.
    await asyncio.wait_for(holding.wait(), LOCK_WAIT_SECONDS)
    scan = asyncio.create_task(container.vouchers.redeem(barista, shown_first))
    await until_it_waits(container, scan)
    released.set()

    fresh = await replacing
    with pytest.raises(CodeInvalidError):
        await scan

    assert fresh.code != shown_first
    for _ in range(3):
        stale = await bar.scan(barista_row, shown_first)
        assert (stale.status_code, stale.json()) == (400, CODE_INVALID)
    [row] = await rows(container, guest_row)
    assert row.status is VoucherStatus.ACTIVE
    accepted = await bar.scan(barista_row, fresh.code)
    assert (accepted.status_code, accepted.json()["repeat"]) == (200, False)
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    await assert_books_balance(container.uow_factory)


async def test_a_scan_that_came_first_stands_and_the_voucher_takes_no_other_code(
    bar: Bar, factory: Factory, container: Container
) -> None:
    """The other order: the bar scans, then the guest taps "Show QR". The voucher is
    used up and is given no fresh code, and the barista's second look at the code that
    redeemed it is the repeat."""
    guest = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista()
    issued = await bar.get_voucher(guest)

    first = await bar.scan(barista, issued["code"])
    fresh = await bar.fresh_code(guest, issued["id"])
    again = await bar.scan(barista, issued["code"])

    assert (first.status_code, first.json()["repeat"]) == (200, False)
    assert (fresh.status_code, fresh.json()) == (409, CONFLICT)
    assert again.json() == first.json() | {"repeat": True}
    assert await voucher_entries(container) == [ISSUE, REDEEM]
    await assert_books_balance(container.uow_factory)


async def test_the_upkeep_of_the_baristas_own_request_changes_no_answer_of_a_scan(
    bar: Bar, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The request of a barista runs that barista's own upkeep first (A.3). Here it has
    something to do: a voucher of the barista has expired and is refunded on the way.
    The guest's voucher is accepted all the same, and so is the next one, scanned at
    once, when the upkeep is not due again."""
    interval = container.settings.identity.upkeep_interval
    guest = await factory.user(balance_micro=WEALTH)
    barista = await factory.barista(balance_micro=WEALTH)
    forgotten = await bar.get_voucher(barista, CHEAPEST)
    clock.advance(TTL - timedelta(days=1))
    first, second = await bar.get_voucher(guest), await bar.get_voucher(guest)
    clock.advance(timedelta(days=1))
    assert await voucher_entries(container) == [ISSUE, ISSUE, ISSUE]

    with_upkeep = await bar.scan(barista, first["code"])
    refunded_by_then = await voucher_entries(container)
    without_upkeep = await bar.scan(barista, second["code"])

    assert interval > timedelta(0)
    assert refunded_by_then == [ISSUE, ISSUE, ISSUE, REFUND, REDEEM]
    for answer, issued in ((with_upkeep, first), (without_upkeep, second)):
        assert answer.status_code == 200, answer.text
        assert (answer.json()["voucher_id"], answer.json()["repeat"]) == (issued["id"], False)
    [own] = await rows(container, barista)
    assert (str(own.id), own.status) == (forgotten["id"], VoucherStatus.EXPIRED)
    assert await factory.balance_micro(barista) == WEALTH
    assert await voucher_entries(container) == [ISSUE, ISSUE, ISSUE, REFUND, REDEEM, REDEEM]
    await assert_books_balance(container.uow_factory)


async def test_somebody_who_scans_their_own_voucher_is_answered_like_anybody(
    bar: Bar, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """An admin who tries the bar alone holds the voucher and scans it. The upkeep of
    the request and the scan lock the same account, one after the other: accepted,
    repeated inside the window and refused after it, like every voucher. A voucher of
    their own that has expired is refused, whether the upkeep of the request has
    refunded it by then or the scan does."""
    admin = await factory.admin(balance_micro=WEALTH)
    issued = await bar.get_voucher(admin)
    kept_too_long = [await bar.get_voucher(admin, CHEAPEST) for _ in range(2)]

    first = await bar.scan(admin, issued["code"])
    again = await bar.scan(admin, issued["code"])
    clock.advance(RESCAN + INSTANT)
    afterwards = await bar.scan(admin, issued["code"])
    clock.advance(TTL)
    # The request that carries this scan refunds both expired vouchers in its upkeep;
    # the next scan follows at once and its request runs none.
    expired = [await bar.scan(admin, voucher["code"]) for voucher in kept_too_long]

    assert (first.status_code, first.json()["repeat"]) == (200, False)
    assert again.json() == first.json() | {"repeat": True}
    for refused in (afterwards, *expired):
        assert (refused.status_code, refused.json()) == (400, CODE_INVALID)
    assert await voucher_entries(container) == [ISSUE, ISSUE, ISSUE, REDEEM, REFUND, REFUND]
    assert await factory.balance_micro(admin) == WEALTH - PRICE
    await assert_books_balance(container.uow_factory)


# --- Whatever happens -------------------------------------------------------------


@dataclass(frozen=True)
class Get:
    """The owner asks for a voucher."""

    product: VoucherProduct


@dataclass(frozen=True)
class Scan:
    """Somebody behind the bar scans the code one of the vouchers has."""

    which: int


@dataclass(frozen=True)
class FreshCode:
    """The owner asks for a fresh code for one of the vouchers."""

    which: int


@dataclass(frozen=True)
class Upkeep:
    """The owner makes a request, and its upkeep refunds what has expired."""


@dataclass(frozen=True)
class Wait:
    time: timedelta


type Step = Get | Scan | FreshCode | Upkeep | Wait

STEPS: Final = st.lists(
    st.one_of(
        st.builds(Get, st.sampled_from(PRODUCTS)),
        st.builds(Scan, st.integers(min_value=0, max_value=7)),
        st.builds(FreshCode, st.integers(min_value=0, max_value=7)),
        st.just(Upkeep()),
        st.builds(
            Wait,
            st.sampled_from(
                [RESCAN, RESCAN + INSTANT, timedelta(days=1), TTL - timedelta(days=1), TTL]
            ),
        ),
    ),
    min_size=1,
    max_size=12,
)

BUDGET: Final[int] = 3 * DEAREST.price_micro
"""What the owner starts with: little enough to run out of."""


@dataclass
class Held:
    """What the test expects of a voucher it got: the model the service is held to."""

    id: UUID
    code: str
    value_micro: int
    expires_at: datetime
    status: VoucherStatus
    redeemed_at: datetime | None = None

    def redeemable(self, now: datetime) -> bool:
        return self.status is VoucherStatus.ACTIVE and now < self.expires_at

    def repeatable(self, now: datetime) -> bool:
        """Whether the one scanner of the test, who redeemed it, is shown it again."""
        return self.redeemed_at is not None and now - self.redeemed_at <= RESCAN

    def shown(self, now: datetime) -> VoucherStatus:
        """The status the wallet lists: expired from `expires_at` on, refunded or not."""
        if self.status is VoucherStatus.ACTIVE and not self.redeemable(now):
            return VoucherStatus.EXPIRED
        return self.status


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(steps=STEPS)
async def test_whatever_happens_to_vouchers_every_credit_is_in_the_balance_parked_or_redeemed(
    container: Container, factory: Factory, clock: FrozenClock, steps: list[Step]
) -> None:
    """Any order of getting, scanning, replacing codes, waiting and upkeep: each voucher
    is redeemed or refunded at most once, however often it is scanned again, the
    owner's balance is what the vouchers left of it, and `vis:redemption_pending` holds
    exactly what the ACTIVE vouchers took.

    Every example has an owner of its own on the one database of the test, so the
    accounts of VIS are compared by how far the example moved them.
    """
    owner_row = await factory.user(balance_micro=BUDGET)
    owner = factory.identity(owner_row)
    scanner = identity_of(await factory.user(), UserRole.BARISTA)
    pending_before, redeemed_before = await parked(container)
    held: list[Held] = []
    balance_micro = BUDGET

    for step in steps:
        now = clock.now()
        match step:
            case Get(product):
                request = IssueVoucherRequest(product_id=product.id)
                if balance_micro < product.price_micro:
                    with pytest.raises(InsufficientBalanceError):
                        await container.vouchers.issue(owner, request)
                else:
                    issued = await container.vouchers.issue(owner, request)
                    balance_micro -= product.price_micro
                    assert issued.balance_cr == to_cr(balance_micro)
                    held.append(
                        Held(
                            id=issued.id,
                            code=issued.code,
                            value_micro=product.price_micro,
                            expires_at=issued.expires_at,
                            status=VoucherStatus.ACTIVE,
                        )
                    )
            case Scan(which) if held:
                voucher = held[which % len(held)]
                if voucher.redeemable(now):
                    scanned = await container.vouchers.redeem(scanner, voucher.code)
                    assert (scanned.voucher_id, scanned.value_cr) == (
                        voucher.id,
                        to_cr(voucher.value_micro),
                    )
                    assert (scanned.repeat, scanned.redeemed_at) == (False, now)
                    voucher.status = VoucherStatus.REDEEMED
                    voucher.redeemed_at = now
                elif voucher.repeatable(now):
                    repeated = await container.vouchers.redeem(scanner, voucher.code)
                    assert (repeated.voucher_id, repeated.repeat, repeated.redeemed_at) == (
                        voucher.id,
                        True,
                        voucher.redeemed_at,
                    )
                else:
                    with pytest.raises(CodeInvalidError):
                        await container.vouchers.redeem(scanner, voucher.code)
                    if voucher.status is VoucherStatus.ACTIVE:
                        # It had expired: the scan booked its refund.
                        voucher.status = VoucherStatus.EXPIRED
                        balance_micro += voucher.value_micro
            case FreshCode(which) if held:
                voucher = held[which % len(held)]
                if voucher.redeemable(now):
                    replaced = voucher.code
                    voucher.code = (await container.vouchers.replace_code(owner, voucher.id)).code
                    with pytest.raises(CodeInvalidError):
                        await container.vouchers.redeem(scanner, replaced)
                else:
                    with pytest.raises(ConflictError):
                        await container.vouchers.replace_code(owner, voucher.id)
            case Upkeep():
                due = [
                    voucher
                    for voucher in held
                    if voucher.status is VoucherStatus.ACTIVE and not voucher.redeemable(now)
                ]
                assert await expire_due(container, owner_row) == len(due)
                for voucher in due:
                    voucher.status = VoucherStatus.EXPIRED
                    balance_micro += voucher.value_micro
            case Wait(time):
                clock.advance(time)
            case _:
                pass  # a scan or a fresh code before there is a voucher

    now = clock.now()
    assert await factory.balance_micro(owner_row) == balance_micro
    listed = (await container.vouchers.list_for_user(owner)).items
    assert [(item.id, item.status, item.value_cr) for item in reversed(listed)] == [
        (voucher.id, voucher.shown(now), to_cr(voucher.value_micro)) for voucher in held
    ]
    # An expired voucher has its value back exactly when its refund was booked.
    assert [item.refunded for item in reversed(listed)] == [
        voucher.status is VoucherStatus.EXPIRED for voucher in held
    ]
    pending, redeemed = await parked(container)
    assert pending - pending_before == sum(
        voucher.value_micro for voucher in held if voucher.status is VoucherStatus.ACTIVE
    )
    assert redeemed - redeemed_before == sum(
        voucher.value_micro for voucher in held if voucher.status is VoucherStatus.REDEEMED
    )
    async with container.uow_factory() as uow:
        # What VIS owes on vouchers (SPEC §5) is what is parked for them.
        assert await uow.vouchers.pending_value() == pending
    await assert_books_balance(container.uow_factory)
