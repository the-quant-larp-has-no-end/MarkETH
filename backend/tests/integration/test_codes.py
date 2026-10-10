"""`CodeService`: how VIS hands out credits (SPEC §2, §12 "Voucher codes", Appendix A.6).

An admin creates a batch and gets its plain codes; a user redeems a code for the value
of its batch, booked from `vis:issuance`. The five rules of SPEC §12 are proven here,
each by the test that carries its name:

    one code redeemed by several users in parallel succeeds once
    expired, revoked and used codes fail with the same error
    a user redeems only one code of a one-per-user batch
    the attempt after the per-user limit is rate limited
    a batch above the monthly issuance cap is refused

and with them what A.6 adds: redemption fails closed without Redis, no admin and no
creator of a batch can cash in a code, revocation, and where a code is kept: in its
own row for as long as it can be redeemed, so that an admin can open the codes of a
batch again, and nowhere once it is redeemed or revoked.

The tests act as admins and users against Postgres, Redis and the ledger. Most go
through the real routes. `as_user` lets one person act at a time, so whatever several
people do at the same moment, and the property at the end, calls the service the routes
call. Values, limits and the cap are those of the test settings. Redemption is limited
per user, so a test gives every person fewer attempts than the limit unless the limit
is what it is about.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter
from sqlalchemy import Select, select, text

from app.container import Container
from app.core.clock import add_months, local_date, start_of_month
from app.core.enums import AccountKind, CodeStatus, JournalKind, UserRole, UserStatus
from app.core.errors import (
    CodeInvalidError,
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
)
from app.core.security import format_code, generate_code, hash_code, normalize_code
from app.core.units import format_cr, to_cr
from app.db.base import Base
from app.models import Account, AuditLogEntry, Code, CodeBatch, JournalEntry, Posting, User
from app.schemas.admin.code_batches import (
    CodeBatchCreatedResponse,
    CreateCodeBatchRequest,
    RevokeCodesRequest,
    RevokeCodesResponse,
)
from app.schemas.codes import RedeemCodeResponse
from app.schemas.common import Limit
from app.services.codes import (
    ADMIN_REDEEM_MESSAGE,
    EXPIRY_NOT_AHEAD_MESSAGE,
    EXPIRY_OFF_THE_CALENDAR_MESSAGE,
    ISSUANCE_LOCK,
    ONE_PER_USER_MESSAGE,
    OWN_BATCH_MESSAGE,
    code_value_message,
)
from app.services.ledger import LedgerRef, code_redemption_key
from app.services.markets import UNSTORABLE_TEXT_MESSAGE
from tests.support.books import assert_books_balance, count_rows, system_balance
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, identity_of
from tests.support.settings import load_base_settings
from tests.support.statements import recorded_statements, takes_a_row_lock

pytestmark = pytest.mark.integration

BATCHES_PATH: Final[str] = "/api/admin/code-batches"
REDEEM_PATH: Final[str] = "/api/codes/redeem"

_SETTINGS = load_base_settings()
_CODES = _SETTINGS.codes

MIN: Final[int] = _CODES.min_value_micro
MAX: Final[int] = _CODES.max_value_micro
DEFAULT: Final[int] = _CODES.default_value_micro
"""CODE_MIN_VALUE_CR, CODE_MAX_VALUE_CR and CODE_DEFAULT_VALUE_CR, in µCR."""

VALUE: Final[int] = MIN
"""What a code is worth when a test does not care: the least, far below the cap."""

CAP: Final[int] = _CODES.max_issuance_per_month_micro
"""MAX_CODE_ISSUANCE_PER_MONTH in µCR."""

FILLS_THE_CAP: Final[int] = CAP // MAX
"""How many codes of the highest value fit under the cap."""

ATTEMPTS: Final[int] = _CODES.redeem_limit_per_user
WINDOW_SECONDS: Final[int] = _CODES.redeem_limit_per_user_window_seconds
"""CODE_REDEEM_LIMIT_PER_USER attempts per CODE_REDEEM_LIMIT_PER_USER_WINDOW_SECONDS."""

CODE_LENGTH: Final[int] = _CODES.length
PUBLIC_BASE_URL: Final[str] = _SETTINGS.app.public_base_url

LIFETIME: Final[timedelta] = timedelta(days=30)
"""How long a batch of a test is valid unless the test says otherwise."""

INSTANT: Final[timedelta] = timedelta(microseconds=1)
"""The smallest step a clock and a timestamp can tell apart."""

LOCK_WAIT_SECONDS: Final[float] = _SETTINGS.postgres.lock_timeout_ms / 2000
"""How long a test waits to see a statement queue for a lock: half of what the
statement itself waits before it gives up (POSTGRES_LOCK_TIMEOUT_MS)."""

USER: Final = AccountKind.USER
ISSUANCE: Final = AccountKind.VIS_ISSUANCE

CODE_INVALID: Final[dict[str, object]] = {
    "code": "CODE_INVALID",
    "message": CodeInvalidError.default_message,
    "details": {},
}
"""The one answer for a code that does not exist, is used, revoked or expired (SPEC §2)."""

_WIRE_TIME: Final = TypeAdapter(datetime)


def on_the_wire(instant: datetime) -> str:
    """An instant as the API writes it."""
    written: str = _WIRE_TIME.dump_python(instant, mode="json")
    return written


@dataclass(frozen=True)
class Batch:
    """A batch as `POST /admin/code-batches` answered it."""

    id: UUID
    codes: list[str]
    """The plain codes, as printed: `XXXX-XXXX-XXXX`."""

    answer: dict[str, Any]


class Acting:
    """The requests of the people in a test: every call says who is asking."""

    def __init__(
        self, client: httpx.AsyncClient, as_user: ActAs, clock: FrozenClock, container: Container
    ) -> None:
        self._client = client
        self._as_user = as_user
        self._clock = clock
        self._uow_factory = container.uow_factory

    async def ask_for_batch(
        self,
        admin: User,
        *,
        count: int = 1,
        value_micro: int | None = VALUE,
        expires_at: datetime | None = None,
        one_per_user: bool = False,
        label: str = "Erstsemester-Apéro",
    ) -> httpx.Response:
        """`value_micro=None` leaves the value out of the request. Left out,
        `expires_at` is `LIFETIME` from now."""
        self._as_user(admin)
        body: dict[str, object] = {
            "label": label,
            "count": count,
            "expires_at": (expires_at or self._clock.now() + LIFETIME).isoformat(),
            "one_per_user": one_per_user,
        }
        if value_micro is not None:
            body["value_cr"] = format_cr(value_micro)
        return await self._client.post(BATCHES_PATH, json=body)

    async def batch(
        self,
        admin: User,
        *,
        count: int = 1,
        value_micro: int | None = VALUE,
        expires_at: datetime | None = None,
        one_per_user: bool = False,
        label: str = "Erstsemester-Apéro",
    ) -> Batch:
        """A batch that was created, with its plain codes."""
        response = await self.ask_for_batch(
            admin,
            count=count,
            value_micro=value_micro,
            expires_at=expires_at,
            one_per_user=one_per_user,
            label=label,
        )
        assert response.status_code == 201, response.text
        answer: dict[str, Any] = response.json()
        return Batch(
            id=UUID(answer["batch"]["id"]),
            codes=[created["code"] for created in answer["codes"]],
            answer=answer,
        )

    async def batches(self, admin: User) -> httpx.Response:
        self._as_user(admin)
        return await self._client.get(BATCHES_PATH)

    async def codes(self, admin: User | None, batch_id: UUID) -> httpx.Response:
        """The codes of a batch that can still be redeemed; None asks as nobody."""
        self._as_user(admin)
        return await self._client.get(f"{BATCHES_PATH}/{batch_id}/codes")

    async def revoke(
        self, admin: User, batch_id: UUID, codes: list[str] | None = None
    ) -> httpx.Response:
        """Without `codes`, the whole batch."""
        self._as_user(admin)
        body = {} if codes is None else {"codes": codes}
        return await self._client.post(f"{BATCHES_PATH}/{batch_id}/revoke", json=body)

    async def redeem(self, user: User, code: str) -> httpx.Response:
        self._as_user(user)
        return await self._client.post(REDEEM_PATH, json={"code": code})

    async def give_role(self, admin: User, user: User, role: UserRole) -> User:
        """`admin` sets the role of `user` in the admin panel. Returns the row as it is
        afterwards: the one a test acts as from then on."""
        self._as_user(admin)
        response = await self._client.post(
            f"/api/admin/users/{user.id}/role", json={"role": role.value}
        )
        assert response.status_code == 200, response.text
        async with self._uow_factory() as uow:
            row = await uow.users.get(user.id)
        assert row is not None
        assert row.role is role
        return row


@pytest.fixture
def acting(
    client: httpx.AsyncClient, as_user: ActAs, clock: FrozenClock, container: Container
) -> Acting:
    return Acting(client, as_user, clock, container)


def plain(code: str) -> str:
    """A printed code without its dashes: what is hashed."""
    normalized = normalize_code(code)
    assert normalized is not None
    return normalized


async def row_of(container: Container, code: str) -> Code:
    """The stored row of a plain code."""
    async with container.uow_factory() as uow:
        row = await uow.codes.get_by_hash(hash_code(plain(code)))
    assert row is not None
    return row


async def statuses(container: Container, codes: list[str]) -> list[CodeStatus]:
    return [(await row_of(container, code)).status for code in codes]


async def redemptions(container: Container) -> int:
    """How many journal entries booked a code."""
    statement = select(JournalEntry.id).where(JournalEntry.kind == JournalKind.CODE_REDEMPTION)
    async with container.uow_factory() as uow:
        return len((await uow.session.scalars(statement)).all())


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


async def audit_rows(container: Container) -> list[AuditLogEntry]:
    async with container.uow_factory() as uow:
        return list(await uow.session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id)))


async def everything_stored(container: Container, *, but: type[Base] | None = None) -> str:
    """Every row of every table, as text. `but` leaves the rows of one table out."""
    stored: list[str] = []
    async with container.uow_factory() as uow:
        for table in Base.metadata.sorted_tables:
            if but is not None and table.name == but.__tablename__:
                continue
            statement = text(f"SELECT row_to_json(t)::text FROM {table.name} AS t")
            stored.extend(await uow.session.scalars(statement))
    return "\n".join(stored)


async def issued_this_month(container: Container) -> int:
    """What counts against the cap right now, in µCR."""
    month = start_of_month(container.clock.now(), container.settings.app.timezone)
    async with container.uow_factory() as uow:
        return await uow.codes.issued_value_since(month)


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


async def until_both_wait(
    container: Container, first: asyncio.Task[Any], second: asyncio.Task[Any]
) -> None:
    """Return once two sessions of the database queue for a lock: nothing else runs in
    a test, so they are the two operations'. Fail if one of them ends instead, or the
    two do not both queue in `LOCK_WAIT_SECONDS`."""
    queued = text(
        "SELECT count(*) FROM pg_stat_activity"
        " WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with asyncio.timeout(LOCK_WAIT_SECONDS):
        while True:
            async with container.uow_factory() as uow:
                if await uow.session.scalar(queued) == 2:
                    return
            assert not first.done(), "the first went through without waiting for its lock"
            assert not second.done(), "the second went through without waiting for its lock"
            await asyncio.sleep(0.01)


async def lockable(container: Container, row: Select[UUID]) -> bool:
    """Whether the row `row` selects can be locked right now: nobody holds it."""
    async with container.uow_factory() as uow:
        free = await uow.session.scalar(row.with_for_update(key_share=True, skip_locked=True))
    return free is not None


def limit_exceeded(issued_micro: int, requested_micro: int) -> dict[str, object]:
    """What a batch above the monthly cap is answered with."""
    return {
        "code": "LIMIT_EXCEEDED",
        "message": LimitExceededError.default_message,
        "details": {
            "limit": "MAX_CODE_ISSUANCE_PER_MONTH",
            "issued_cr": format_cr(issued_micro),
            "requested_cr": format_cr(requested_micro),
            "cap_cr": format_cr(CAP),
        },
    }


# --- Creating a batch -------------------------------------------------------------


async def test_a_batch_answers_its_plain_codes_and_keeps_each_with_its_row_and_nowhere_else(
    acting: Acting,
    factory: Factory,
    container: Container,
    clock: FrozenClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A code is in the answer that creates it and in the row it redeems, beside its
    hash. No other table, no list of batches and no log line holds one."""
    caplog.set_level(logging.DEBUG)
    admin = await factory.admin()
    expires_at = clock.now() + LIFETIME

    response = await acting.ask_for_batch(
        admin, label="Quiz night", count=5, value_micro=2 * MIN, one_per_user=True
    )

    assert response.status_code == 201
    answer = response.json()
    batch_id = UUID(answer["batch"]["id"])
    assert answer["batch"] == {
        "id": str(batch_id),
        "label": "Quiz night",
        "value_cr": format_cr(2 * MIN),
        "count": 5,
        "total_value_cr": format_cr(5 * 2 * MIN),
        "expires_at": on_the_wire(expires_at),
        "expired": False,
        "one_per_user": True,
        "created_at": on_the_wire(clock.now()),
        "revoked_at": None,
        "active_count": 5,
        "redeemed_count": 0,
        "revoked_count": 0,
        "can_revoke": True,
    }
    codes = [created["code"] for created in answer["codes"]]
    assert len(set(codes)) == 5
    for created in answer["codes"]:
        assert set(created) == {"code", "redeem_url"}
        assert len(plain(created["code"])) == CODE_LENGTH
        assert created["code"] == format_code(plain(created["code"])), "printed in groups of four"

    async with container.uow_factory() as uow:
        stored_rows = (await uow.session.scalars(select(Code).order_by(Code.id))).all()
    # Kept without its dashes, under the hash of exactly that, in the order of the answer.
    assert [(row.code_hash, row.code) for row in stored_rows] == [
        (hash_code(plain(code)), plain(code)) for code in codes
    ]
    for row in stored_rows:
        assert (row.batch_id, row.status) == (batch_id, CodeStatus.ACTIVE)
        assert (row.redeemed_by, row.redeemed_at, row.entry_id) == (None, None, None)
    stored_elsewhere = await everything_stored(container, but=Code)
    listed = (await acting.batches(admin)).text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for code in codes:
        for anywhere in (stored_elsewhere, listed, logged):
            assert code not in anywhere
            assert plain(code) not in anywhere


async def test_the_link_on_a_code_card_opens_the_wallet_with_the_code_in_its_query(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """`{PUBLIC_BASE_URL}/wallet?code=...` (A.6). The wallet cuts the code out and sends
    the code alone, which redeems."""
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin)
    [created] = batch.answer["codes"]

    link = urlsplit(created["redeem_url"])
    [code_in_the_link] = parse_qs(link.query)["code"]

    assert created["redeem_url"] == f"{PUBLIC_BASE_URL}/wallet?code={created['code']}"
    assert f"{link.scheme}://{link.netloc}{link.path}" == f"{PUBLIC_BASE_URL}/wallet"
    assert not link.fragment
    assert code_in_the_link == created["code"]
    assert (await acting.redeem(alice, code_in_the_link)).status_code == 200
    await assert_books_balance(container.uow_factory)


async def test_a_batch_that_names_no_value_is_worth_the_default(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    alice = await factory.user()

    batch = await acting.batch(admin, count=2, value_micro=None)
    redeemed = await acting.redeem(alice, batch.codes[0])

    assert batch.answer["batch"]["value_cr"] == format_cr(DEFAULT)
    assert batch.answer["batch"]["total_value_cr"] == format_cr(2 * DEFAULT)
    assert redeemed.json()["value_cr"] == format_cr(DEFAULT)
    assert await factory.balance_micro(alice) == DEFAULT
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("value_micro", [MIN, MAX], ids=["the-minimum", "the-maximum"])
async def test_a_code_may_be_worth_the_minimum_and_the_maximum(
    acting: Acting, factory: Factory, container: Container, value_micro: int
) -> None:
    admin = await factory.admin()
    alice = await factory.user()

    batch = await acting.batch(admin, value_micro=value_micro)
    redeemed = await acting.redeem(alice, batch.codes[0])

    assert redeemed.status_code == 200
    assert await factory.balance_micro(alice) == value_micro
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "value_micro", [MIN - 1, MAX + 1], ids=["below-the-minimum", "above-the-maximum"]
)
async def test_a_value_outside_the_bounds_is_422_on_value_cr_and_stores_nothing(
    acting: Acting, factory: Factory, container: Container, value_micro: int
) -> None:
    """One millionth of a credit outside CODE_MIN_VALUE_CR to CODE_MAX_VALUE_CR. The
    answer names the field and says, in tokens, what a code may be worth."""
    admin = await factory.admin()

    response = await acting.ask_for_batch(admin, value_micro=value_micro)

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": code_value_message(MIN, MAX),
        "details": {"field": "value_cr"},
    }
    assert code_value_message(MIN, MAX) == "Enter a value from 5 to 500 tokens."
    for table in (CodeBatch, Code, AuditLogEntry):
        assert await count_rows(container.uow_factory, table) == 0


@pytest.mark.parametrize(
    "ago", [timedelta(0), INSTANT, timedelta(days=1)], ids=["now", "an-instant-ago", "yesterday"]
)
async def test_a_batch_that_would_already_have_expired_is_422_on_expires_at(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock, ago: timedelta
) -> None:
    admin = await factory.admin()

    response = await acting.ask_for_batch(admin, expires_at=clock.now() - ago)

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": EXPIRY_NOT_AHEAD_MESSAGE,
        "details": {"field": "expires_at"},
    }
    assert EXPIRY_NOT_AHEAD_MESSAGE == "Choose a time in the future."
    for table in (CodeBatch, Code, AuditLogEntry):
        assert await count_rows(container.uow_factory, table) == 0


async def test_a_label_that_postgres_cannot_store_is_422_on_label(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A NUL character is valid in JSON and in no text column."""
    admin = await factory.admin()

    response = await acting.ask_for_batch(admin, label="Quiz\x00night")

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": UNSTORABLE_TEXT_MESSAGE,
        "details": {"field": "label"},
    }
    for table in (CodeBatch, Code, AuditLogEntry):
        assert await count_rows(container.uow_factory, table) == 0


@pytest.mark.parametrize(
    "expires_at",
    [
        datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone(timedelta(hours=-12))),
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=14))),
    ],
    ids=["after-the-last-year-in-utc", "before-the-first-year-in-utc"],
)
async def test_an_expiry_that_has_no_date_in_utc_is_422_on_expires_at(
    acting: Acting, factory: Factory, container: Container, expires_at: datetime
) -> None:
    """A date at the edge of the calendar, written with an offset that takes it over
    the edge: refused as a value of the field, not as a failure of the server."""
    admin = await factory.admin()

    response = await acting.ask_for_batch(admin, expires_at=expires_at)

    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": EXPIRY_OFF_THE_CALENDAR_MESSAGE,
        "details": {"field": "expires_at"},
    }
    assert EXPIRY_OFF_THE_CALENDAR_MESSAGE == "Choose another date."
    for table in (CodeBatch, Code, AuditLogEntry):
        assert await count_rows(container.uow_factory, table) == 0


async def test_a_batch_may_expire_at_the_end_of_the_calendar(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    expires_at = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

    batch = await acting.batch(admin, expires_at=expires_at)
    [listed] = (await acting.batches(admin)).json()["items"]

    assert batch.answer["batch"]["expires_at"] == on_the_wire(expires_at)
    assert listed == batch.answer["batch"]
    assert (await acting.redeem(alice, batch.codes[0])).status_code == 200
    await assert_books_balance(container.uow_factory)


async def test_an_expiry_is_the_same_instant_whatever_offset_it_was_written_with(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Written as Zurich time, answered and listed in UTC, and valid until that instant."""
    admin = await factory.admin()
    alice = await factory.user()
    expires_at = clock.now() + INSTANT
    as_written_in_zurich = expires_at.astimezone(container.settings.app.timezone)
    assert as_written_in_zurich.isoformat() != expires_at.isoformat()

    batch = await acting.batch(admin, expires_at=as_written_in_zurich)
    [listed] = (await acting.batches(admin)).json()["items"]

    assert batch.answer["batch"]["expires_at"] == on_the_wire(expires_at)
    assert listed == batch.answer["batch"]
    assert (await acting.redeem(alice, batch.codes[0])).status_code == 200
    await assert_books_balance(container.uow_factory)


async def test_creating_a_batch_is_audited_with_what_was_created_and_no_code(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    expires_at = clock.now() + LIFETIME

    batch = await acting.batch(admin, label="Quiz night", count=3, one_per_user=True)

    [audited] = await audit_rows(container)
    assert (audited.actor_id, audited.action, audited.target) == (
        admin.id,
        "code_batch.create",
        f"code_batch:{batch.id}",
    )
    assert audited.before is None
    assert audited.after == {
        "label": "Quiz night",
        "count": 3,
        "value_micro": VALUE,
        "expires_at": expires_at.astimezone(UTC).isoformat(),
        "one_per_user": True,
    }
    assert audited.created_at == clock.now()


# --- The monthly cap --------------------------------------------------------------


async def test_a_batch_above_the_monthly_issuance_cap_is_refused(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """SPEC §12. The answer says what is issued, what was asked for and the cap."""
    admin = await factory.admin()
    one_too_many = FILLS_THE_CAP + 1

    response = await acting.ask_for_batch(admin, count=one_too_many, value_micro=MAX)

    assert one_too_many * MAX > CAP
    assert response.status_code == 409
    assert response.json() == limit_exceeded(issued_micro=0, requested_micro=one_too_many * MAX)
    for table in (CodeBatch, Code, AuditLogEntry):
        assert await count_rows(container.uow_factory, table) == 0


async def test_batches_fill_the_cap_to_the_last_credit_and_then_the_least_batch_is_refused(
    acting: Acting, factory: Factory, container: Container
) -> None:
    assert FILLS_THE_CAP * MAX == CAP, "the test settings let codes of the highest value fill it"
    admin = await factory.admin()

    most = await acting.ask_for_batch(admin, count=FILLS_THE_CAP - 1, value_micro=MAX)
    last = await acting.ask_for_batch(admin, count=1, value_micro=MAX)
    least = await acting.ask_for_batch(admin, count=1, value_micro=MIN)

    assert (most.status_code, last.status_code) == (201, 201)
    assert least.status_code == 409
    assert least.json() == limit_exceeded(issued_micro=CAP, requested_micro=MIN)
    assert await issued_this_month(container) == CAP
    assert await count_rows(container.uow_factory, CodeBatch) == 2


async def test_revoked_codes_stop_counting_against_the_cap_and_redeemed_ones_do_not(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A.6: the cap is on the value of the codes of the month that are not revoked."""
    admin = await factory.admin()
    alice = await factory.user()
    full = await acting.batch(admin, count=FILLS_THE_CAP, value_micro=MAX)
    assert (await acting.redeem(alice, full.codes[0])).status_code == 200

    while_full = await acting.ask_for_batch(admin, count=1, value_micro=MIN)
    revoked = await acting.revoke(admin, full.id)
    too_much = await acting.ask_for_batch(admin, count=FILLS_THE_CAP, value_micro=MAX)
    what_fits = await acting.ask_for_batch(admin, count=FILLS_THE_CAP - 1, value_micro=MAX)

    assert while_full.status_code == 409
    assert revoked.json()["revoked_count"] == FILLS_THE_CAP - 1
    assert too_much.status_code == 409
    assert too_much.json() == limit_exceeded(issued_micro=MAX, requested_micro=CAP)
    assert what_fits.status_code == 201
    assert await issued_this_month(container) == CAP
    await assert_books_balance(container.uow_factory)


async def test_the_cap_starts_again_with_the_calendar_month_of_the_app_timezone(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Midnight on the first in Zurich, which is an hour or two before midnight UTC."""
    zurich = container.settings.app.timezone
    first_of_next_month = add_months(local_date(clock.now(), zurich).replace(day=1), 1)
    next_month = datetime.combine(first_of_next_month, time.min, tzinfo=zurich)
    assert next_month.astimezone(UTC).month == clock.now().month, "still this month in UTC"
    later = next_month + LIFETIME
    admin = await factory.admin()
    await acting.batch(admin, count=FILLS_THE_CAP, value_micro=MAX, expires_at=later)

    clock.set(next_month - INSTANT)
    last_instant = await acting.ask_for_batch(admin, value_micro=MIN, expires_at=later)
    clock.set(next_month)
    first_instant = await acting.ask_for_batch(
        admin, count=FILLS_THE_CAP, value_micro=MAX, expires_at=later
    )

    assert last_instant.status_code == 409
    assert last_instant.json() == limit_exceeded(issued_micro=CAP, requested_micro=MIN)
    assert first_instant.status_code == 201
    assert await issued_this_month(container) == CAP


@pytest.mark.parametrize("requests_at_once", [2, 6])
async def test_batches_created_at_once_cannot_all_slip_under_the_cap(
    factory: Factory, container: Container, clock: FrozenClock, requests_at_once: int
) -> None:
    """Each alone fits, any two together do not: the advisory lock lets one check and
    insert at a time (A.6)."""
    admins = [factory.identity(await factory.admin(index)) for index in range(2)]
    more_than_half = FILLS_THE_CAP // 2 + 1
    request = CreateCodeBatchRequest(
        label="Both at once",
        count=more_than_half,
        value_cr=to_cr(MAX),
        expires_at=clock.now() + LIFETIME,
        one_per_user=False,
    )
    assert more_than_half * MAX <= CAP < 2 * more_than_half * MAX

    results = await asyncio.gather(
        *(
            container.codes.create_batch(admins[turn % len(admins)], request)
            for turn in range(requests_at_once)
        ),
        return_exceptions=True,
    )

    created = [result for result in results if isinstance(result, CodeBatchCreatedResponse)]
    refused = [result for result in results if isinstance(result, LimitExceededError)]
    assert len(created) == 1, results
    assert len(refused) == requests_at_once - 1, results
    for refusal in refused:
        assert refusal.details == {
            "limit": Limit.MAX_CODE_ISSUANCE_PER_MONTH,
            "issued_cr": to_cr(more_than_half * MAX),
            "requested_cr": to_cr(more_than_half * MAX),
            "cap_cr": to_cr(CAP),
        }
    assert await count_rows(container.uow_factory, CodeBatch) == 1
    assert await count_rows(container.uow_factory, Code) == more_than_half
    assert await issued_this_month(container) == more_than_half * MAX


# --- Redeeming --------------------------------------------------------------------


async def test_redeeming_a_code_credits_the_value_of_its_batch_from_issuance(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    had_micro = 3 * VALUE
    admin = await factory.admin()
    alice = await factory.user(balance_micro=had_micro)
    batch = await acting.batch(admin, count=2, value_micro=VALUE)
    clock.advance(timedelta(days=2))

    response = await acting.redeem(alice, batch.codes[0])

    assert response.status_code == 200
    assert response.json() == {
        "value_cr": format_cr(VALUE),
        "balance_cr": format_cr(had_micro + VALUE),
    }
    assert await factory.balance_micro(alice) == had_micro + VALUE
    row = await row_of(container, batch.codes[0])
    booked, postings = await entry(container, code_redemption_key(row.id))
    assert (row.status, row.redeemed_by, row.redeemed_at, row.entry_id) == (
        CodeStatus.REDEEMED,
        alice.id,
        clock.now(),
        booked.id,
    )
    assert (booked.kind, booked.ref_type, booked.ref_id, booked.created_at) == (
        JournalKind.CODE_REDEMPTION,
        LedgerRef.CODE,
        row.id,
        clock.now(),
    )
    assert postings == {ISSUANCE: -VALUE, USER: VALUE}
    assert await system_balance(container.uow_factory, ISSUANCE) == -(had_micro + VALUE)
    assert await statuses(container, batch.codes) == [CodeStatus.REDEEMED, CodeStatus.ACTIVE]
    [listed] = (await acting.batches(admin)).json()["items"]
    assert (listed["active_count"], listed["redeemed_count"], listed["revoked_count"]) == (1, 1, 0)
    await assert_books_balance(container.uow_factory)


def _lower_case(code: str) -> str:
    return format_code(code).lower()


def _without_dashes(code: str) -> str:
    return code


def _with_spaces_around_and_inside(code: str) -> str:
    return f"  {code[:5]} {code[5:]}\t"


def _with_letters_for_digits(code: str) -> str:
    return format_code(code).replace("0", "O").replace("1", "I")


def _with_a_lower_case_l_for_one(code: str) -> str:
    return format_code(code).replace("0", "o").replace("1", "l")


@pytest.mark.parametrize(
    "typed",
    [
        _lower_case,
        _without_dashes,
        _with_spaces_around_and_inside,
        _with_letters_for_digits,
        _with_a_lower_case_l_for_one,
    ],
    ids=lambda typed: typed.__name__.strip("_").replace("_", "-"),
)
async def test_a_code_redeems_however_it_was_typed(
    acting: Acting,
    factory: Factory,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
    typed: Callable[[str], str],
) -> None:
    """Uppercase, dashes removed, O read as 0, I and L read as 1 (SPEC §2)."""
    with_a_zero_and_a_one = ("01" + "A" * CODE_LENGTH)[:CODE_LENGTH]
    monkeypatch.setattr("app.services.codes.generate_code", lambda length: with_a_zero_and_a_one)
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin)
    assert batch.codes == [format_code(with_a_zero_and_a_one)]

    response = await acting.redeem(alice, typed(with_a_zero_and_a_one))

    assert response.status_code == 200, response.text
    assert await factory.balance_micro(alice) == VALUE
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("users_at_once", [2, 8])
async def test_one_code_redeemed_by_several_users_in_parallel_succeeds_once(
    acting: Acting, factory: Factory, container: Container, users_at_once: int
) -> None:
    """SPEC §12: the code row is locked, so the others find it redeemed."""
    admin = await factory.admin()
    people = [await factory.user() for _ in range(users_at_once)]
    batch = await acting.batch(admin)
    [code] = batch.codes

    results = await asyncio.gather(
        *(container.codes.redeem(factory.identity(person), code) for person in people),
        return_exceptions=True,
    )

    redeemed = [result for result in results if isinstance(result, RedeemCodeResponse)]
    refused = [result for result in results if isinstance(result, CodeInvalidError)]
    assert len(redeemed) == 1, results
    assert len(refused) == users_at_once - 1, results
    balances = {person.id: await factory.balance_micro(person) for person in people}
    row = await row_of(container, code)
    assert sorted(balances.values()) == [0] * (users_at_once - 1) + [VALUE]
    assert row.redeemed_by is not None
    assert balances[row.redeemed_by] == VALUE
    assert await redemptions(container) == 1
    assert await system_balance(container.uow_factory, ISSUANCE) == -VALUE
    await assert_books_balance(container.uow_factory)


async def test_expired_revoked_and_used_codes_fail_with_the_same_error(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """SPEC §12, and with them what never was a code: nothing in the answer says which
    of these a code is, so codes cannot be probed. Everybody here tries once."""
    admin = await factory.admin()
    soon = clock.now() + timedelta(days=1)
    lasting = await acting.batch(admin, count=4)
    expiring = await acting.batch(admin, expires_at=soon)
    revoked_whole = await acting.batch(admin)
    used, revoked, still_good, in_a_link = lasting.codes
    assert (await acting.redeem(await factory.user(), used)).status_code == 200
    assert (await acting.revoke(admin, lasting.id, [revoked])).json()["revoked_count"] == 1
    assert (await acting.revoke(admin, revoked_whole.id)).json()["revoked_count"] == 1
    clock.set(soon)
    attempts = {
        "used": used,
        "revoked": revoked,
        "of a revoked batch": revoked_whole.codes[0],
        "expired": expiring.codes[0],
        "never issued": format_code(generate_code(CODE_LENGTH)),
        "one character short": still_good[:-1],
        "one character long": f"{still_good}0",
        "not Crockford Base32": "U" * CODE_LENGTH,
        "a sentence": "let me in",
        "the link of a card": f"{PUBLIC_BASE_URL}/wallet?code={in_a_link}",
    }

    answers = {
        what: await acting.redeem(await factory.user(), code) for what, code in attempts.items()
    }

    for what, answer in answers.items():
        assert answer.status_code == 400, what
        assert answer.json() == CODE_INVALID, what
    assert await redemptions(container) == 1
    # The two codes that were only mistyped or wrapped in a link are still good.
    assert await statuses(container, [still_good, in_a_link]) == [CodeStatus.ACTIVE] * 2
    assert (await acting.redeem(await factory.user(), in_a_link)).status_code == 200
    await assert_books_balance(container.uow_factory)


async def test_a_code_can_be_redeemed_until_the_instant_its_batch_expires(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    expires_at = clock.now() + timedelta(days=7)
    batch = await acting.batch(admin, count=2, expires_at=expires_at)

    clock.set(expires_at - INSTANT)
    last_moment = await acting.redeem(alice, batch.codes[0])
    clock.set(expires_at)
    at_expiry = await acting.redeem(alice, batch.codes[1])
    [listed] = (await acting.batches(admin)).json()["items"]

    assert last_moment.status_code == 200
    assert at_expiry.status_code == 400
    assert at_expiry.json() == CODE_INVALID
    assert listed["expired"] is True
    assert await statuses(container, batch.codes) == [CodeStatus.REDEEMED, CodeStatus.ACTIVE]
    assert await factory.balance_micro(alice) == VALUE
    await assert_books_balance(container.uow_factory)


async def test_a_used_code_fails_for_the_user_who_used_it_too(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Acceptance: redeeming a code raises the balance by its value; again fails."""
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, one_per_user=True)

    first = await acting.redeem(alice, batch.codes[0])
    again = await acting.redeem(alice, batch.codes[0])

    assert first.status_code == 200
    assert again.status_code == 400
    assert again.json() == CODE_INVALID, "a used code, not a second code of the batch"
    assert await factory.balance_micro(alice) == VALUE
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


async def test_a_code_that_is_redeemed_or_refused_is_left_in_no_log_and_no_table(
    acting: Acting,
    factory: Factory,
    container: Container,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Redeeming a code takes it out of its row, which held it until then. Neither it
    nor what somebody typed in its place is kept anywhere afterwards."""
    caplog.set_level(logging.DEBUG)
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin)
    guess = format_code(generate_code(CODE_LENGTH))
    assert (await row_of(container, batch.codes[0])).code == plain(batch.codes[0])

    refused = await acting.redeem(alice, guess)
    redeemed = await acting.redeem(alice, batch.codes[0])

    assert (refused.status_code, redeemed.status_code) == (400, 200)
    assert (await row_of(container, batch.codes[0])).code is None
    logged = "\n".join(record.getMessage() for record in caplog.records)
    stored = await everything_stored(container)
    for code in (guess, batch.codes[0]):
        for anywhere in (logged, stored, refused.text, redeemed.text):
            assert code not in anywhere
            assert plain(code) not in anywhere
    await assert_books_balance(container.uow_factory)


# --- One per user -----------------------------------------------------------------


async def test_a_user_redeems_only_one_code_of_a_one_per_user_batch(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """SPEC §12. The second code is valid, so the user is told why it was not taken
    (A.6), and it stays good for somebody else."""
    admin = await factory.admin()
    alice = await factory.user()
    bob = await factory.user()
    batch = await acting.batch(admin, count=3, one_per_user=True)
    first, second, third = batch.codes

    taken = await acting.redeem(alice, first)
    refused = await acting.redeem(alice, second)
    refused_again = await acting.redeem(alice, third)
    by_bob = await acting.redeem(bob, second)

    assert taken.status_code == 200
    for answer in (refused, refused_again):
        assert answer.status_code == 409
        assert answer.json() == {
            "code": "LIMIT_EXCEEDED",
            "message": ONE_PER_USER_MESSAGE,
            "details": {"limit": "ONE_CODE_PER_USER"},
        }
    assert by_bob.status_code == 200
    assert await statuses(container, batch.codes) == [
        CodeStatus.REDEEMED,
        CodeStatus.REDEEMED,
        CodeStatus.ACTIVE,
    ]
    assert await factory.balance_micro(alice) == VALUE
    assert await factory.balance_micro(bob) == VALUE
    assert await redemptions(container) == 2
    await assert_books_balance(container.uow_factory)


async def test_the_rule_is_per_batch_and_only_for_a_batch_that_has_it(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    strict = await acting.batch(admin, count=2, one_per_user=True, label="one each")
    also_strict = await acting.batch(admin, count=1, one_per_user=True, label="one each, too")
    free = await acting.batch(admin, count=2, one_per_user=False, label="as many as you find")

    answers = [
        await acting.redeem(alice, code)
        for code in (strict.codes[0], also_strict.codes[0], *free.codes)
    ]

    assert [answer.status_code for answer in answers] == [200, 200, 200, 200]
    assert await factory.balance_micro(alice) == 4 * VALUE
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("codes_at_once", [2, 4])
async def test_parallel_redemptions_by_one_user_of_a_one_per_user_batch_succeed_once(
    acting: Acting, factory: Factory, container: Container, codes_at_once: int
) -> None:
    """The rule is checked under the lock of the user's account (A.6), so the second
    redemption sees the first."""
    assert codes_at_once < ATTEMPTS
    admin = await factory.admin()
    alice_row = await factory.user()
    alice = factory.identity(alice_row)
    batch = await acting.batch(admin, count=codes_at_once, one_per_user=True)

    results = await asyncio.gather(
        *(container.codes.redeem(alice, code) for code in batch.codes),
        return_exceptions=True,
    )

    redeemed = [result for result in results if isinstance(result, RedeemCodeResponse)]
    refused = [result for result in results if isinstance(result, LimitExceededError)]
    assert len(redeemed) == 1, results
    assert len(refused) == codes_at_once - 1, results
    for refusal in refused:
        assert refusal.details == {"limit": Limit.ONE_CODE_PER_USER}
    assert sorted(await statuses(container, batch.codes)) == sorted(
        [CodeStatus.REDEEMED] + [CodeStatus.ACTIVE] * (codes_at_once - 1)
    )
    assert await factory.balance_micro(alice_row) == VALUE
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


# --- Locks ------------------------------------------------------------------------


async def test_a_redemption_locks_the_code_row_then_the_users_account_then_issuance(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A.6's order. Held up at the user's account, a redemption has the code row and
    nothing of VIS yet; held up at the code row, it has not touched the account."""
    admin = await factory.admin()
    alice_row = await factory.user()
    alice = factory.identity(alice_row)
    batch = await acting.batch(admin, count=2)
    first, second = batch.codes
    first_code = select(Code.id).where(Code.code_hash == hash_code(plain(first)))
    alices_account = select(Account.id).where(Account.owner_id == alice_row.id)
    issuance_account = select(Account.id).where(Account.kind == ISSUANCE)

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice_row.id, for_update=True)
        redemption = asyncio.create_task(container.codes.redeem(alice, first))
        await until_it_waits(container, redemption)
        code_was_held = not await lockable(container, first_code)
        vis_was_free = await lockable(container, issuance_account)
    await redemption

    async with container.uow_factory() as holder:
        await holder.codes.get_by_hash(hash_code(plain(second)), for_update=True)
        redemption = asyncio.create_task(container.codes.redeem(alice, second))
        await until_it_waits(container, redemption)
        account_was_free = await lockable(container, alices_account)
    await redemption

    assert code_was_held
    assert vis_was_free
    assert account_was_free
    assert await factory.balance_micro(alice_row) == 2 * VALUE
    await assert_books_balance(container.uow_factory)


async def test_a_redemption_reads_its_batch_and_never_locks_it(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A revocation holds the row of the batch while it takes the rows of its codes. A
    redemption holds the row of its code; if it locked the batch too, each could end
    up waiting for the other. It reads the batch as it is committed: it does not wait
    for whoever holds that row, and no statement of it asks for a lock on it."""
    admin = await factory.admin()
    alice_row = await factory.user()
    alice = factory.identity(alice_row)
    batch = await acting.batch(admin, count=2)
    first, second = batch.codes

    async with container.uow_factory() as revoking:
        assert await revoking.code_batches.get(batch.id, for_update=True) is not None
        redeemed = await asyncio.wait_for(container.codes.redeem(alice, first), LOCK_WAIT_SECONDS)
    with recorded_statements(container) as statements:
        await container.codes.redeem(alice, second)

    assert redeemed.value_cr == to_cr(VALUE)
    locking = [statement for statement in statements if takes_a_row_lock(statement)]
    assert "FROM codes" in locking[0], "the code row is the first lock of a redemption"
    assert "FROM accounts" in locking[1], "the user's account is the second"
    assert any("FROM code_batches" in statement for statement in statements), "it is read"
    assert not any("code_batches" in statement for statement in locking)
    assert await factory.balance_micro(alice_row) == 2 * VALUE
    await assert_books_balance(container.uow_factory)


async def test_a_redemption_and_a_revocation_that_meet_at_a_code_do_not_wait_for_each_other(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """The revocation holds the row of the batch and queues for the row of a code; the
    redemption holds that row. It goes on without the batch's row, so the two end one
    after the other and neither is ended by Postgres to break a cycle: the code is
    redeemed, and the other code of the batch revoked.

    Each erased the plain text of the code it took, under the lock it held on that
    row: while both were still under way, the committed rows kept both codes."""
    admin = await factory.admin()
    alice_row = await factory.user()
    alice = factory.identity(alice_row)
    batch = await acting.batch(admin, count=2)
    taken, left = batch.codes
    the_code = select(Code.id).where(Code.code_hash == hash_code(plain(taken)))
    the_batch = select(CodeBatch.id).where(CodeBatch.id == batch.id)

    async with container.uow_factory() as holder:
        await holder.accounts.get_user_account(alice_row.id, for_update=True)
        redemption = asyncio.create_task(container.codes.redeem(alice, taken))
        await until_it_waits(container, redemption)
        revocation = asyncio.create_task(
            container.codes.revoke(factory.identity(admin), batch.id, RevokeCodesRequest())
        )
        await until_both_wait(container, redemption, revocation)
        code_was_held = not await lockable(container, the_code)
        batch_was_held = not await lockable(container, the_batch)
        kept_meanwhile = [(await row_of(container, code)).code for code in batch.codes]
    redeemed, revoked = await asyncio.wait_for(
        asyncio.gather(redemption, revocation), 2 * LOCK_WAIT_SECONDS
    )

    assert code_was_held, "by the redemption, which waits for the user's account"
    assert batch_was_held, "by the revocation, which waits for the code"
    assert kept_meanwhile == [plain(taken), plain(left)]
    assert redeemed.value_cr == to_cr(VALUE)
    assert (revoked.revoked_count, revoked.batch.redeemed_count, revoked.batch.active_count) == (
        1,
        1,
        0,
    )
    rows = [await row_of(container, code) for code in batch.codes]
    assert [(row.status, row.code) for row in rows] == [
        (CodeStatus.REDEEMED, None),
        (CodeStatus.REVOKED, None),
    ]
    assert rows[0].redeemed_by == alice_row.id
    assert await factory.balance_micro(alice_row) == VALUE
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


async def test_a_batch_waits_for_the_one_being_created_and_counts_it_against_the_cap(
    factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """The advisory lock of A.6. While another creation holds it, a batch that would fit
    queues before it has read what is issued. What the holder issues before it lets go
    fills the cap, so the batch that waited is refused."""
    admin_row = await factory.admin()
    admin = factory.identity(admin_row)
    expires_at = clock.now() + LIFETIME
    request = CreateCodeBatchRequest(
        label="Fits while nothing is issued",
        count=1,
        value_cr=to_cr(MIN),
        expires_at=expires_at,
        one_per_user=False,
    )

    async with container.uow_factory() as holder:
        await holder.advisory_lock(ISSUANCE_LOCK)
        waiting = asyncio.create_task(container.codes.create_batch(admin, request))
        await until_it_waits(container, waiting)
        full = await holder.code_batches.add(
            CodeBatch(
                label="Fills the cap",
                value_micro=MAX,
                count=FILLS_THE_CAP,
                expires_at=expires_at,
                one_per_user=False,
                created_by=admin_row.id,
                created_at=clock.now(),
            )
        )
        await holder.codes.add_all(
            Code(batch_id=full.id, code_hash=hash_code(code), code=code, status=CodeStatus.ACTIVE)
            for code in (generate_code(CODE_LENGTH) for _ in range(FILLS_THE_CAP))
        )
    with pytest.raises(LimitExceededError) as refused:
        await waiting

    assert FILLS_THE_CAP * MAX == CAP, "the test settings let codes of the highest value fill it"
    assert refused.value.details == {
        "limit": Limit.MAX_CODE_ISSUANCE_PER_MONTH,
        "issued_cr": to_cr(CAP),
        "requested_cr": to_cr(MIN),
        "cap_cr": to_cr(CAP),
    }
    assert await count_rows(container.uow_factory, CodeBatch) == 1
    assert await issued_this_month(container) == CAP


# --- The rate limit ---------------------------------------------------------------


async def test_the_attempt_after_the_per_user_limit_is_rate_limited(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """SPEC §12, with CODE_REDEEM_LIMIT_PER_USER attempts per window: the next one is
    refused before the code is looked at, also when the code is good, and only for the
    user who made the attempts."""
    admin = await factory.admin()
    alice = await factory.user()
    bob = await factory.user()
    batch = await acting.batch(admin)
    [good] = batch.codes
    guess = format_code(generate_code(CODE_LENGTH))

    guesses = [await acting.redeem(alice, guess) for _ in range(ATTEMPTS)]
    limited = await acting.redeem(alice, good)
    by_bob = await acting.redeem(bob, good)

    assert [answer.status_code for answer in guesses] == [400] * ATTEMPTS
    assert limited.status_code == 429
    body = limited.json()
    retry_after = body["details"]["retry_after_seconds"]
    assert body == {
        "code": "RATE_LIMITED",
        "message": RateLimitedError.default_message,
        "details": {"retry_after_seconds": retry_after},
    }
    assert isinstance(retry_after, int)
    assert 0 < retry_after <= WINDOW_SECONDS
    assert limited.headers["retry-after"] == str(retry_after)
    assert await factory.balance_micro(alice) == 0
    assert by_bob.status_code == 200
    assert (await row_of(container, good)).redeemed_by == bob.id
    await assert_books_balance(container.uow_factory)


async def test_every_attempt_counts_also_one_that_redeemed_a_code(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, count=ATTEMPTS + 1)

    answers = [await acting.redeem(alice, code) for code in batch.codes]

    assert [answer.status_code for answer in answers] == [200] * ATTEMPTS + [429]
    assert await factory.balance_micro(alice) == ATTEMPTS * VALUE
    assert await statuses(container, batch.codes) == [CodeStatus.REDEEMED] * ATTEMPTS + [
        CodeStatus.ACTIVE
    ]
    await assert_books_balance(container.uow_factory)


@pytest.mark.redis_down
async def test_with_redis_down_redemption_answers_503_and_books_nothing(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """SPEC §12 "Failures": the limit cannot be counted, so redemption fails closed,
    for a good code as for anything else. Creating a batch needs no Redis."""
    had_micro = VALUE
    admin = await factory.admin()
    alice = await factory.user(balance_micro=had_micro)
    batch = await acting.batch(admin)

    for attempt in (batch.codes[0], "not a code"):
        response = await acting.redeem(alice, attempt)

        assert response.status_code == 503
        assert response.json() == {
            "code": "SERVICE_UNAVAILABLE",
            "message": ServiceUnavailableError.default_message,
            "details": {},
        }
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE]
    assert await redemptions(container) == 0
    assert await factory.balance_micro(alice) == had_micro
    await assert_books_balance(container.uow_factory)


# --- Who holds the codes does not redeem them -------------------------------------


@pytest.mark.parametrize("appointed", [False, True], ids=["by-the-settings", "by-the-panel"])
async def test_an_admin_who_opened_the_codes_of_another_admins_batch_cannot_redeem_them(
    acting: Acting, factory: Factory, container: Container, appointed: bool
) -> None:
    """Every admin can open the codes of every batch, so the rule is about every admin
    and not about the creator alone: a second admin reads the codes of a batch they did
    not create and is refused each one, in a sentence of their own. Nothing is booked
    and no row of any table changes."""
    creator = await factory.admin()
    second = await factory.user(role=UserRole.ADMIN) if appointed else await factory.admin(1)
    batch = await acting.batch(creator, count=3)
    opened = await acting.codes(second, batch.id)
    assert [read["code"] for read in opened.json()["codes"]] == batch.codes
    before = await everything_stored(container)

    answers = [await acting.redeem(second, code) for code in batch.codes]

    for answer in answers:
        assert answer.status_code == 403
        assert answer.json() == {
            "code": "FORBIDDEN",
            "message": ADMIN_REDEEM_MESSAGE,
            "details": {},
        }
    assert ADMIN_REDEEM_MESSAGE == "Admins hand out codes and cannot redeem them."
    assert await everything_stored(container) == before
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 3
    assert await factory.balance_micro(second) == 0
    assert await redemptions(container) == 0
    assert await system_balance(container.uow_factory, ISSUANCE) == 0
    await assert_books_balance(container.uow_factory)


async def test_an_admin_is_refused_whatever_state_the_code_is_in_and_for_their_own_batch_too(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """A good code, a used, a revoked and an expired one get the admin the same
    sentence, and so does a code of a batch the admin created: to an admin the answer
    is about being an admin. What is no code at all gets the generic answer."""
    creator = await factory.admin()
    second = await factory.admin(1)
    student = await factory.user()
    batch = await acting.batch(creator, count=3)
    good, used, revoked = batch.codes
    assert (await acting.redeem(student, used)).status_code == 200
    assert (await acting.revoke(creator, batch.id, [revoked])).status_code == 200

    by_second = [await acting.redeem(second, code) for code in (good, used, revoked)]
    unknown = await acting.redeem(second, format_code(generate_code(CODE_LENGTH)))
    by_creator = await acting.redeem(creator, good)
    clock.advance(LIFETIME)
    once_expired = await acting.redeem(second, good)

    for answer in (*by_second, by_creator, once_expired):
        assert answer.status_code == 403
        assert answer.json()["message"] == ADMIN_REDEEM_MESSAGE
    assert unknown.status_code == 400
    assert unknown.json() == CODE_INVALID
    assert await statuses(container, batch.codes) == [
        CodeStatus.ACTIVE,
        CodeStatus.REDEEMED,
        CodeStatus.REVOKED,
    ]
    assert [await factory.balance_micro(admin) for admin in (creator, second)] == [0, 0]
    assert await factory.balance_micro(student) == VALUE
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


async def test_the_service_refuses_an_admin_whoever_calls_it_and_whatever_the_status(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """The rule is the service's, on the role alone: also for a caller that reached it
    past the route, and for an admin whose account is suspended."""
    creator = await factory.admin()
    appointed = await factory.user(role=UserRole.ADMIN)
    suspended = await factory.user(role=UserRole.ADMIN, status=UserStatus.SUSPENDED)
    batch = await acting.batch(creator, count=2)

    for admin, code in zip((appointed, suspended), batch.codes, strict=True):
        with pytest.raises(ForbiddenError) as refused:
            await container.codes.redeem(factory.identity(admin), code)
        assert refused.value.message == ADMIN_REDEEM_MESSAGE

    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 2
    assert await redemptions(container) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_user_and_a_barista_redeem_a_code(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Everybody who is no admin redeems: a barista scans vouchers and holds no code."""
    admin = await factory.admin()
    student = await factory.user()
    barista = await factory.barista()
    batch = await acting.batch(admin, count=2)

    answers = [
        await acting.redeem(person, code)
        for person, code in zip((student, barista), batch.codes, strict=True)
    ]

    assert [answer.status_code for answer in answers] == [200, 200]
    assert [(await row_of(container, code)).redeemed_by for code in batch.codes] == [
        student.id,
        barista.id,
    ]
    assert [await factory.balance_micro(person) for person in (student, barista)] == [VALUE] * 2
    assert await redemptions(container) == 2
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("role", [UserRole.USER, UserRole.BARISTA])
async def test_the_creator_of_a_batch_cannot_redeem_its_codes_whatever_state_they_are_in(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock, role: UserRole
) -> None:
    """A.6: whoever made a batch got all of its plain codes in the answer that created
    it. The rule about admins covers the creator while they are one; this one holds
    for a creator whose role is another by the time they redeem. They are told so in
    a sentence of their own, for a good code as for a used, a revoked and an expired
    one; a code that does not exist is no code of theirs and gets the generic answer."""
    appointer = await factory.admin()
    creator = await factory.user(role=UserRole.ADMIN)
    student = await factory.user()
    batch = await acting.batch(creator, count=3)
    good, used, revoked = batch.codes
    assert (await acting.redeem(student, used)).status_code == 200
    assert (await acting.revoke(appointer, batch.id, [revoked])).status_code == 200
    creator = await acting.give_role(appointer, creator, role)

    own_codes = [await acting.redeem(creator, code) for code in (good, used, revoked)]
    unknown = await acting.redeem(creator, format_code(generate_code(CODE_LENGTH)))
    clock.advance(LIFETIME)
    once_expired = await acting.redeem(creator, good)

    for answer in (*own_codes, once_expired):
        assert answer.status_code == 403
        assert answer.json() == {"code": "FORBIDDEN", "message": OWN_BATCH_MESSAGE, "details": {}}
    assert unknown.status_code == 400
    assert unknown.json() == CODE_INVALID
    assert await statuses(container, batch.codes) == [
        CodeStatus.ACTIVE,
        CodeStatus.REDEEMED,
        CodeStatus.REVOKED,
    ]
    assert await factory.balance_micro(creator) == 0
    assert await factory.balance_micro(student) == VALUE
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


# --- Revoking ---------------------------------------------------------------------


async def test_revoking_listed_codes_revokes_those_and_leaves_every_other_code_alone(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A redeemed code, a code of another batch, an unknown code and what is no code at
    all are not touched and not an error. A code listed twice is revoked once."""
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, count=5)
    other = await acting.batch(admin, label="another batch")
    redeemed, first, second, third, _ = batch.codes
    assert (await acting.redeem(alice, redeemed)).status_code == 200

    response = await acting.revoke(
        admin,
        batch.id,
        [
            first.lower(),
            f" {second.replace('-', ' ')} ",
            first,
            redeemed,
            other.codes[0],
            format_code(generate_code(CODE_LENGTH)),
            "not a code",
            third[:-1],
        ],
    )

    assert response.status_code == 200
    answer = response.json()
    assert answer["revoked_count"] == 2
    assert answer["batch"] == batch.answer["batch"] | {
        "active_count": 2,
        "redeemed_count": 1,
        "revoked_count": 2,
    }
    assert answer["batch"]["revoked_at"] is None, "single codes do not revoke the batch"
    assert await statuses(container, batch.codes) == [
        CodeStatus.REDEEMED,
        CodeStatus.REVOKED,
        CodeStatus.REVOKED,
        CodeStatus.ACTIVE,
        CodeStatus.ACTIVE,
    ]
    assert await statuses(container, other.codes) == [CodeStatus.ACTIVE]
    bob = await factory.user()
    assert (await acting.redeem(bob, first)).json() == CODE_INVALID
    assert (await acting.redeem(bob, third)).status_code == 200
    assert (await acting.redeem(bob, other.codes[0])).status_code == 200
    assert await factory.balance_micro(alice) == VALUE, "a redeemed code stays redeemed"
    await assert_books_balance(container.uow_factory)


async def test_revoking_a_batch_revokes_every_unredeemed_code_and_stamps_it_once(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, count=4)
    assert (await acting.redeem(alice, batch.codes[0])).status_code == 200
    clock.advance(timedelta(hours=1))
    revoked_at = clock.now()

    response = await acting.revoke(admin, batch.id)
    clock.advance(timedelta(hours=1))
    again = await acting.revoke(admin, batch.id)

    assert response.status_code == 200
    assert response.json() == {
        "revoked_count": 3,
        "batch": batch.answer["batch"]
        | {
            "active_count": 0,
            "redeemed_count": 1,
            "revoked_count": 3,
            "revoked_at": on_the_wire(revoked_at),
            "can_revoke": False,
        },
    }
    assert again.status_code == 200
    assert again.json() == response.json() | {"revoked_count": 0}, "the first stamp stays"
    assert await statuses(container, batch.codes) == [
        CodeStatus.REDEEMED,
        CodeStatus.REVOKED,
        CodeStatus.REVOKED,
        CodeStatus.REVOKED,
    ]
    bob = await factory.user()
    assert (await acting.redeem(bob, batch.codes[1])).json() == CODE_INVALID
    assert await factory.balance_micro(alice) == VALUE, "a redeemed code stays redeemed"
    assert await redemptions(container) == 1
    await assert_books_balance(container.uow_factory)


async def test_a_batch_says_whether_a_revoke_would_revoke_anything(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """`can_revoke` is true exactly while the batch has a code that is neither redeemed
    nor revoked, whether or not it has expired. It is what a revoke of the batch would
    answer: where it is false, the revoke revokes nothing."""
    admin = await factory.admin()
    alice = await factory.user()
    soon = clock.now() + timedelta(days=1)
    mixed = await acting.batch(admin, count=3, label="mixed")
    used_up = await acting.batch(admin, count=1, label="used up")
    expiring = await acting.batch(admin, count=1, label="expiring", expires_at=soon)

    async def listed() -> dict[str, bool]:
        items = (await acting.batches(admin)).json()["items"]
        return {item["label"]: item["can_revoke"] for item in items}

    assert mixed.answer["batch"]["can_revoke"] is True
    assert await listed() == {"mixed": True, "used up": True, "expiring": True}

    assert (await acting.redeem(alice, mixed.codes[0])).status_code == 200
    assert (await acting.redeem(alice, used_up.codes[0])).status_code == 200
    one_revoked = await acting.revoke(admin, mixed.id, [mixed.codes[1]])
    clock.set(soon)

    assert one_revoked.json()["batch"]["can_revoke"] is True, "one code is still good"
    assert await listed() == {"mixed": True, "used up": False, "expiring": True}
    opened = (await acting.codes(admin, expiring.id)).json()
    assert (opened["codes"], opened["batch"]["expired"]) == ([], True)
    assert opened["batch"]["can_revoke"] is True, "its row is ACTIVE and a revoke clears it"

    revoked = {
        label: (await acting.revoke(admin, batch.id)).json()
        for label, batch in (("mixed", mixed), ("used up", used_up), ("expiring", expiring))
    }

    assert {label: answer["revoked_count"] for label, answer in revoked.items()} == {
        "mixed": 1,
        "used up": 0,
        "expiring": 1,
    }
    assert [answer["batch"]["can_revoke"] for answer in revoked.values()] == [False] * 3
    assert await listed() == {"mixed": False, "used up": False, "expiring": False}
    for batch in (mixed, used_up, expiring):
        assert (await acting.revoke(admin, batch.id)).json()["revoked_count"] == 0
    await assert_books_balance(container.uow_factory)


async def test_a_batch_revoked_code_by_code_is_not_stamped_as_revoked(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    batch = await acting.batch(admin, count=2)

    response = await acting.revoke(admin, batch.id, batch.codes)

    assert response.json()["revoked_count"] == 2
    assert response.json()["batch"]["active_count"] == 0
    assert response.json()["batch"]["revoked_at"] is None
    assert await statuses(container, batch.codes) == [CodeStatus.REVOKED] * 2


async def test_a_list_in_which_nothing_can_be_a_code_revokes_nothing(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    batch = await acting.batch(admin, count=2)

    response = await acting.revoke(admin, batch.id, ["not a code", "U" * CODE_LENGTH, "A"])

    assert response.status_code == 200
    assert response.json() == {"revoked_count": 0, "batch": batch.answer["batch"]}
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 2


async def test_a_revocation_is_audited_with_how_many_codes_it_revoked(
    acting: Acting, factory: Factory, container: Container
) -> None:
    creator = await factory.admin()
    revoker = await factory.admin(1)
    batch = await acting.batch(creator, count=3)

    await acting.revoke(revoker, batch.id, [batch.codes[0]])
    await acting.revoke(revoker, batch.id)

    created, single, whole = await audit_rows(container)
    assert created.action == "code_batch.create"
    for audited, after in (
        (single, {"revoked_count": 1, "whole_batch": False}),
        (whole, {"revoked_count": 2, "whole_batch": True}),
    ):
        assert (audited.actor_id, audited.action, audited.target) == (
            revoker.id,
            "code_batch.revoke",
            f"code_batch:{batch.id}",
        )
        assert audited.before is None
        assert audited.after == after
    stored = await everything_stored(container)
    for code in batch.codes:
        assert code not in stored
        assert plain(code) not in stored


async def test_revoking_in_a_batch_that_does_not_exist_is_404_and_writes_nothing(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    batch = await acting.batch(admin)

    for codes in (None, batch.codes):
        response = await acting.revoke(admin, uuid7(), codes)

        assert response.status_code == 404
        assert response.json() == {
            "code": "NOT_FOUND",
            "message": NotFoundError.default_message,
            "details": {},
        }
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE]
    assert [audited.action for audited in await audit_rows(container)] == ["code_batch.create"]


async def test_codes_redeemed_while_their_batch_is_revoked_end_redeemed_or_revoked(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """Each code goes one way or the other, whoever was first: none is revoked after it
    was redeemed, none is redeemed after it was revoked, and none is left."""
    admin = await factory.admin()
    people = [await factory.user() for _ in range(6)]
    batch = await acting.batch(admin, count=len(people))

    revoked, *results = await asyncio.gather(
        container.codes.revoke(factory.identity(admin), batch.id, RevokeCodesRequest()),
        *(
            container.codes.redeem(factory.identity(person), code)
            for person, code in zip(people, batch.codes, strict=True)
        ),
        return_exceptions=True,
    )

    assert isinstance(revoked, RevokeCodesResponse), revoked
    redeemed = [result for result in results if isinstance(result, RedeemCodeResponse)]
    refused = [result for result in results if isinstance(result, CodeInvalidError)]
    assert len(redeemed) + len(refused) == len(people), results
    assert revoked.revoked_count == len(refused)
    assert (revoked.batch.active_count, revoked.batch.redeemed_count) == (0, len(redeemed))
    stored = await statuses(container, batch.codes)
    assert stored.count(CodeStatus.REDEEMED) == len(redeemed)
    assert stored.count(CodeStatus.REVOKED) == len(refused)
    balances = [await factory.balance_micro(person) for person in people]
    assert sum(balances) == len(redeemed) * VALUE
    assert await redemptions(container) == len(redeemed)
    await assert_books_balance(container.uow_factory)


# --- Opening the codes of a batch again -------------------------------------------


async def test_the_codes_of_a_batch_read_back_are_the_ones_it_was_created_with(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """The same codes with the same links, in the same order, and each still redeems:
    what the admin prints later is what would have been printed at once."""
    creator = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(creator, count=5, label="Quiz night", one_per_user=True)

    response = await acting.codes(creator, batch.id)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store", "no browser or proxy keeps a copy"
    answer = response.json()
    assert set(answer) == {"batch", "codes"}
    assert answer["batch"] == batch.answer["batch"]
    assert answer["codes"] == batch.answer["codes"]
    assert [read["code"] for read in answer["codes"]] == batch.codes
    for read in answer["codes"]:
        assert read == {
            "code": format_code(plain(read["code"])),
            "redeem_url": f"{PUBLIC_BASE_URL}/wallet?code={read['code']}",
        }
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 5
    redeemed = await acting.redeem(alice, answer["codes"][-1]["code"])
    assert redeemed.status_code == 200, redeemed.text
    assert await factory.balance_micro(alice) == VALUE
    await assert_books_balance(container.uow_factory)


async def test_a_redeemed_or_revoked_code_is_gone_from_the_list_and_its_plain_text_from_the_table(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """The statement that redeems or revokes a code clears it, so there is nothing left
    to open: not in the answer, not in the row, not in any table."""
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, count=4)
    redeemed, revoked, third, fourth = batch.codes
    assert [(await row_of(container, code)).code for code in batch.codes] == [
        plain(code) for code in batch.codes
    ]

    assert (await acting.redeem(alice, redeemed)).status_code == 200
    assert (await acting.revoke(admin, batch.id, [revoked])).json()["revoked_count"] == 1
    response = await acting.codes(admin, batch.id)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert [read["code"] for read in answer["codes"]] == [third, fourth]
    assert answer["codes"] == batch.answer["codes"][2:]
    assert answer["batch"] == batch.answer["batch"] | {
        "active_count": 2,
        "redeemed_count": 1,
        "revoked_count": 1,
    }
    rows = [await row_of(container, code) for code in batch.codes]
    assert [(row.status, row.code) for row in rows] == [
        (CodeStatus.REDEEMED, None),
        (CodeStatus.REVOKED, None),
        (CodeStatus.ACTIVE, plain(third)),
        (CodeStatus.ACTIVE, plain(fourth)),
    ]
    stored = await everything_stored(container)
    for gone in (redeemed, revoked):
        assert gone not in response.text
        assert gone not in stored
        assert plain(gone) not in stored
    assert plain(third) in stored

    # Revoking the batch takes the rest: nothing of it can be opened by anybody again.
    assert (await acting.revoke(admin, batch.id)).json()["revoked_count"] == 2
    emptied = await acting.codes(admin, batch.id)

    assert emptied.status_code == 200
    assert emptied.json()["codes"] == []
    assert emptied.json()["batch"]["active_count"] == 0
    stored = await everything_stored(container)
    for code in batch.codes:
        assert code not in stored
        assert plain(code) not in stored
    await assert_books_balance(container.uow_factory)


async def test_a_batch_that_has_expired_lists_no_code(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """Until the instant it expires every code is listed; from then on none can be
    redeemed, so none is handed out. The rows stay ACTIVE: nothing revoked them."""
    admin = await factory.admin()
    expires_at = clock.now() + timedelta(days=7)
    batch = await acting.batch(admin, count=2, expires_at=expires_at)

    clock.set(expires_at - INSTANT)
    last_moment = await acting.codes(admin, batch.id)
    clock.set(expires_at)
    at_expiry = await acting.codes(admin, batch.id)

    assert [read["code"] for read in last_moment.json()["codes"]] == batch.codes
    assert last_moment.json()["batch"]["expired"] is False
    assert at_expiry.status_code == 200
    assert at_expiry.json() == {
        "batch": batch.answer["batch"] | {"expired": True},
        "codes": [],
    }
    assert at_expiry.json()["batch"]["active_count"] == 2
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 2
    assert all(code not in at_expiry.text for code in batch.codes)


async def test_only_an_admin_opens_the_codes_of_a_batch(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A user, a barista and nobody are refused before anything is read, and no audit
    row says otherwise. Any admin opens them, not the creator alone."""
    creator = await factory.admin()
    another_admin = await factory.admin(1)
    appointed = await factory.user(role=UserRole.ADMIN)
    student = await factory.user()
    barista = await factory.barista()
    batch = await acting.batch(creator, count=2)

    for outsider in (student, barista):
        refused = await acting.codes(outsider, batch.id)
        assert refused.status_code == 403
        assert refused.json() == {
            "code": "FORBIDDEN",
            "message": ForbiddenError.default_message,
            "details": {},
        }
        # The service says so too, to whoever calls it past the route.
        with pytest.raises(ForbiddenError):
            await container.codes.batch_codes(factory.identity(outsider), batch.id)
    unnamed = await acting.codes(None, batch.id)
    assert (unnamed.status_code, unnamed.json()["code"]) == (401, "UNAUTHENTICATED")
    assert [audited.action for audited in await audit_rows(container)] == ["code_batch.create"]

    for admin in (another_admin, appointed, creator):
        opened = await acting.codes(admin, batch.id)
        assert opened.status_code == 200, opened.text
        assert [read["code"] for read in opened.json()["codes"]] == batch.codes
    for refused_text in (refused.text, unnamed.text):
        assert all(code not in refused_text for code in batch.codes)


async def test_every_read_of_the_codes_is_audited_with_who_asked_which_batch_and_no_code(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    """One row per read, in the transaction of the read: the actor, the batch, how many
    codes went out, and when. Also for a read that lists nothing."""
    creator = await factory.admin()
    reader = await factory.admin(1)
    alice = await factory.user()
    batch = await acting.batch(creator, count=3)
    other = await acting.batch(creator, label="another batch")

    first_read = clock.advance(timedelta(minutes=5))
    assert (await acting.codes(reader, batch.id)).status_code == 200
    assert (await acting.redeem(alice, batch.codes[0])).status_code == 200
    second_read = clock.advance(timedelta(minutes=5))
    assert (await acting.codes(reader, batch.id)).status_code == 200
    assert (await acting.codes(creator, other.id)).status_code == 200
    assert (await acting.revoke(creator, other.id)).status_code == 200
    third_read = clock.advance(timedelta(minutes=5))
    assert (await acting.codes(creator, other.id)).json()["codes"] == []

    reads = [row for row in await audit_rows(container) if row.action == "code_batch.read_codes"]
    assert [(row.actor_id, row.target, row.before, row.after, row.created_at) for row in reads] == [
        (reader.id, f"code_batch:{batch.id}", None, {"code_count": 3}, first_read),
        (reader.id, f"code_batch:{batch.id}", None, {"code_count": 2}, second_read),
        (creator.id, f"code_batch:{other.id}", None, {"code_count": 1}, second_read),
        (creator.id, f"code_batch:{other.id}", None, {"code_count": 0}, third_read),
    ]
    assert [row.action for row in await audit_rows(container)] == [
        "code_batch.create",
        "code_batch.create",
        "code_batch.read_codes",
        "code_batch.read_codes",
        "code_batch.read_codes",
        "code_batch.revoke",
        "code_batch.read_codes",
    ]
    audited = await everything_stored(container, but=Code)
    for code in (*batch.codes, *other.codes):
        assert code not in audited
        assert plain(code) not in audited
    await assert_books_balance(container.uow_factory)


async def test_reading_the_codes_writes_nothing_but_its_audit_row(
    acting: Acting, factory: Factory, container: Container
) -> None:
    """A read hands codes out and changes none: every row of every other table is as it
    was, and each code still redeems once."""
    admin = await factory.admin()
    alice = await factory.user()
    batch = await acting.batch(admin, count=2)
    before = await everything_stored(container, but=AuditLogEntry)

    for _ in range(3):
        assert (await acting.codes(admin, batch.id)).status_code == 200

    assert await everything_stored(container, but=AuditLogEntry) == before
    assert await count_rows(container.uow_factory, AuditLogEntry) == 1 + 3
    assert await statuses(container, batch.codes) == [CodeStatus.ACTIVE] * 2
    assert [(await acting.redeem(alice, code)).status_code for code in batch.codes] == [200, 200]
    assert (await acting.redeem(alice, batch.codes[0])).json() == CODE_INVALID
    await assert_books_balance(container.uow_factory)


async def test_reading_the_codes_of_a_batch_that_does_not_exist_is_404_and_audits_nothing(
    acting: Acting, factory: Factory, container: Container
) -> None:
    admin = await factory.admin()
    await acting.batch(admin)

    response = await acting.codes(admin, uuid7())

    assert response.status_code == 404
    assert response.json() == {
        "code": "NOT_FOUND",
        "message": NotFoundError.default_message,
        "details": {},
    }
    assert [audited.action for audited in await audit_rows(container)] == ["code_batch.create"]


async def test_a_read_that_cannot_be_audited_hands_out_no_code(
    factory: Factory, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit row and the read are one transaction. Where the row cannot be written
    the read fails with it, and nobody has seen a code the log does not know of."""
    admin = factory.identity(await factory.admin())
    created = await container.codes.create_batch(
        admin,
        CreateCodeBatchRequest(
            label="Unaudited",
            count=2,
            expires_at=container.clock.now() + LIFETIME,
            one_per_user=False,
        ),
    )

    async def no_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the audit log cannot be written")

    monkeypatch.setattr(container.audit, "record", no_audit)

    with pytest.raises(RuntimeError, match="audit log"):
        await container.codes.batch_codes(admin, created.batch.id)

    monkeypatch.undo()
    assert [audited.action for audited in await audit_rows(container)] == ["code_batch.create"]
    opened = await container.codes.batch_codes(admin, created.batch.id)
    assert opened.codes == created.codes


# --- The list ---------------------------------------------------------------------


async def test_batches_are_listed_newest_first_with_what_became_of_their_codes(
    acting: Acting, factory: Factory, container: Container, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    alice = await factory.user()
    short_lived = await acting.batch(
        admin, label="first", count=3, expires_at=clock.now() + timedelta(days=1)
    )
    clock.advance(timedelta(hours=1))
    lasting = await acting.batch(admin, label="second", count=2, value_micro=MAX)
    assert (await acting.redeem(alice, short_lived.codes[0])).status_code == 200
    assert (await acting.revoke(admin, lasting.id, [lasting.codes[0]])).status_code == 200
    clock.advance(timedelta(days=1))

    response = await acting.batches(admin)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            lasting.answer["batch"] | {"active_count": 1, "redeemed_count": 0, "revoked_count": 1},
            short_lived.answer["batch"]
            | {"active_count": 2, "redeemed_count": 1, "revoked_count": 0, "expired": True},
        ]
    }
    assert lasting.answer["batch"]["total_value_cr"] == format_cr(2 * MAX)
    assert "codes" not in response.text
    await assert_books_balance(container.uow_factory)


async def test_the_list_is_empty_before_any_batch_exists(acting: Acting, factory: Factory) -> None:
    response = await acting.batches(await factory.admin())

    assert response.status_code == 200
    assert response.json() == {"items": []}


# --- Whatever is asked for --------------------------------------------------------


@dataclass(frozen=True)
class Asked:
    """A batch an admin asks for, and whether it is revoked again right away."""

    count: int
    value_micro: int
    revoked_again: bool


ASKED: Final = st.lists(
    st.builds(
        Asked,
        count=st.integers(min_value=1, max_value=2 * FILLS_THE_CAP // 3),
        value_micro=st.sampled_from([MIN, DEFAULT, MAX]),
        revoked_again=st.booleans(),
    ),
    min_size=1,
    max_size=8,
)


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(asked=ASKED)
async def test_whatever_batches_are_asked_for_a_month_never_holds_more_than_the_cap(
    container: Container, factory: Factory, clock: FrozenClock, asked: list[Asked]
) -> None:
    """A batch is refused exactly when it would take the unrevoked codes of the month
    above MAX_CODE_ISSUANCE_PER_MONTH, and then nothing of it is stored.

    The examples share the one database of the test, so each ends by revoking what it
    left: revoked codes do not count.
    """
    admin = identity_of(await factory.user(), UserRole.ADMIN)
    expires_at = clock.now() + LIFETIME
    issued_micro = 0
    left: list[UUID] = []
    assert await issued_this_month(container) == 0

    try:
        for batch in asked:
            request = CreateCodeBatchRequest(
                label="Whatever",
                count=batch.count,
                value_cr=to_cr(batch.value_micro),
                expires_at=expires_at,
                one_per_user=False,
            )
            requested_micro = batch.count * batch.value_micro
            if issued_micro + requested_micro > CAP:
                with pytest.raises(LimitExceededError) as refused:
                    await container.codes.create_batch(admin, request)
                assert refused.value.details == {
                    "limit": Limit.MAX_CODE_ISSUANCE_PER_MONTH,
                    "issued_cr": to_cr(issued_micro),
                    "requested_cr": to_cr(requested_micro),
                    "cap_cr": to_cr(CAP),
                }
            else:
                created = await container.codes.create_batch(admin, request)
                left.append(created.batch.id)
                assert len({code.code for code in created.codes}) == batch.count
                if batch.revoked_again:
                    revoked = await container.codes.revoke(
                        admin, created.batch.id, RevokeCodesRequest()
                    )
                    assert revoked.revoked_count == batch.count
                else:
                    issued_micro += requested_micro
            assert await issued_this_month(container) == issued_micro <= CAP
    finally:
        # Also after a failure, so that the example Hypothesis tries next starts clean.
        for batch_id in left:
            await container.codes.revoke(admin, batch_id, RevokeCodesRequest())
    assert await issued_this_month(container) == 0
