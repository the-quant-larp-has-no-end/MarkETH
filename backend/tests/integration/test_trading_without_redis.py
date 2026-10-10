"""The application with Redis stopped (SPEC §9 "When a dependency fails", §12 "Failures",
Appendix A.4, A.6).

Redis makes things faster, counts attempts and tells open phones. It decides nothing:
with it gone a market is still proposed, approved, traded and settled, in Postgres. Two
things change, and both are rules of the spec: trades are counted by the limiter this
process keeps itself, and a code is not redeemed at all (503), because nothing would
count the attempts.

Every test here runs on a `container` whose Redis is a closed port (`redis_down`), and
every request carries the portal's headers, so the way in through `IdentityService`
runs without Redis too.
"""

from datetime import timedelta
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.container import Container
from app.core.enums import (
    CodeStatus,
    JournalKind,
    MarketStatus,
    ResolutionOutcome,
    VoucherStatus,
)
from app.core.units import MICRO, format_cr, to_cr, to_micro
from app.integrations.redis.client import RedisUnavailableError
from app.integrations.redis.rate_limiter import RateLimit
from app.models import Code, JournalEntry, Trade, User, Voucher
from app.schemas.admin.code_batches import CodeBatchCreatedResponse, CodeBatchListResponse
from app.schemas.barista import VoucherRedeemResponse
from app.schemas.health import DependencyStatus, HealthResponse, HealthStatus
from app.schemas.live import HistoryResponse, HistorySource
from app.schemas.markets import MarketDetailResponse, MarketListResponse, SellPortion
from app.schemas.me import MeResponse
from app.schemas.trading import TradeResponse
from app.schemas.vouchers import VoucherIssuedResponse
from app.services.live_quotes import STREAM_UNAVAILABLE_MESSAGE
from app.services.trading import ORDER_WINDOW_SECONDS
from tests.support.books import assert_books_balance, count_rows
from tests.support.clock import FrozenClock
from tests.support.factories import Factory, PortalHeaders
from tests.support.settings import load_base_settings

pytestmark = [pytest.mark.integration, pytest.mark.redis_down]

_SETTINGS = load_base_settings()

WALLET: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what a trader of these tests starts with."""

MAX_ORDERS: Final[int] = _SETTINGS.risk.max_orders_per_minute
"""MAX_ORDERS_PER_MINUTE: trade requests one user may send per minute."""

SMALLEST_TRADE: Final[int] = _SETTINGS.pricing.min_trade_micro
"""MIN_TRADE_CR in µCR."""

WEEK: Final[timedelta] = timedelta(days=7)
MINUTE: Final[timedelta] = timedelta(minutes=1)


class Portal:
    """The routes as the portal passes a signed-in person on to them."""

    def __init__(self, client: httpx.AsyncClient, headers: PortalHeaders) -> None:
        self._client = client
        self._headers = headers

    async def send(
        self,
        who: User,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: UUID | None = None,
    ) -> httpx.Response:
        headers = self._headers(who) | ({} if key is None else {"Idempotency-Key": str(key)})
        return await self._client.request(method, f"/api{path}", json=body, headers=headers)

    async def ok[ModelT: BaseModel](
        self,
        who: User,
        method: str,
        path: str,
        model: type[ModelT],
        body: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        key: UUID | None = None,
    ) -> ModelT:
        response = await self.send(who, method, path, body, key=key)
        assert response.status_code == status_code, response.text
        return model.model_validate(response.json())

    async def buy(
        self, who: User, market_id: UUID, outcome: str, amount_micro: int
    ) -> TradeResponse:
        return await self.ok(
            who,
            "POST",
            f"/markets/{market_id}/trades",
            TradeResponse,
            {"side": "BUY", "outcome": outcome, "amount_cr": format_cr(amount_micro)},
            status_code=201,
            key=uuid7(),
        )


@pytest.fixture
def portal(client: httpx.AsyncClient, portal_headers: PortalHeaders) -> Portal:
    return Portal(client, portal_headers)


async def open_market(portal: Portal, proposer: User, admin: User, clock: FrozenClock) -> UUID:
    """A market proposed and approved through the routes, without Redis."""
    close_at = clock.now() + WEEK
    proposed = await portal.ok(
        proposer,
        "POST",
        "/markets",
        MarketDetailResponse,
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
        status_code=201,
    )
    opened = await portal.ok(
        admin,
        "POST",
        f"/admin/markets/{proposed.id}/approve",
        MarketDetailResponse,
        {"tier": "DEFAULT"},
    )
    assert opened.status is MarketStatus.OPEN
    return proposed.id


async def test_the_container_of_these_tests_has_no_redis(container: Container) -> None:
    """What every other test here rests on: the first call to Redis fails."""
    with pytest.raises(RedisUnavailableError):
        await container.rate_limiter.hit(RateLimit("probe", limit=1, window_seconds=60), "x")
    assert await container.market_cache.get("anything") is None


async def test_without_redis_a_market_goes_from_its_proposal_to_its_payout(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """SPEC §9: "If Redis or ScyllaDB fails, trades still commit in Postgres." So does
    everything around a trade: the proposal, every admin action, the list and the page
    (read from Postgres every time, there being no cache), the chart, the payout."""
    proposer = await factory.user()
    alice, bob = [await factory.user(balance_micro=WALLET) for _ in range(2)]
    admin = await factory.admin()
    view = container.market_view
    market_id = await open_market(portal, proposer, admin, clock)
    market = f"/markets/{market_id}"

    clock.advance(MINUTE)
    first = await portal.buy(alice, market_id, "YES", 50 * MICRO)
    clock.advance(MINUTE)
    await portal.buy(bob, market_id, "NO", 25 * MICRO)
    page = await portal.ok(alice, "GET", market, MarketDetailResponse)
    (half,) = [
        preset
        for preset in page.viewer.sell_presets
        if (preset.outcome, preset.portion) == (first.outcome, SellPortion.HALF)
    ]
    clock.advance(MINUTE)
    sold = await portal.ok(
        alice,
        "POST",
        f"{market}/trades",
        TradeResponse,
        {"side": "SELL", "outcome": "YES", "shares": format_cr(to_micro(half.shares))},
        status_code=201,
        key=uuid7(),
    )
    await assert_books_balance(container.uow_factory)

    (listed,) = (await portal.ok(bob, "GET", "/markets", MarketListResponse)).items
    chart = await portal.ok(bob, "GET", f"{market}/history", HistoryResponse)
    health = await portal.ok(bob, "GET", "/health", HealthResponse)
    stream = await portal.send(bob, "GET", f"{market}/stream")

    assert (listed.version, listed.p_yes, listed.p_no) == (3, sold.p_yes_after, sold.p_no_after)
    assert [point.version for point in chart.points] == [0, 1, 2, 3]
    assert chart.source is HistorySource.POSTGRES
    assert chart.points[-1].p_yes == sold.p_yes_after
    assert (health.status, health.postgres, health.redis) == (
        HealthStatus.DEGRADED,
        DependencyStatus.UP,
        DependencyStatus.DOWN,
    )
    # No stream without Redis: answered as JSON before it starts, so a phone polls.
    assert stream.status_code == 503, stream.text
    assert stream.json() == {
        "code": "SERVICE_UNAVAILABLE",
        "message": STREAM_UNAVAILABLE_MESSAGE,
        "details": {},
    }

    clock.advance(timedelta(hours=1))
    await portal.ok(admin, "POST", f"/admin{market}/close", MarketDetailResponse, {})
    before = {user.id: await factory.balance_micro(user) for user in (alice, bob)}
    settled = await portal.ok(
        admin,
        "POST",
        f"/admin{market}/resolve",
        MarketDetailResponse,
        {"outcome": "YES", "evidence": "The menu of that Friday shows it."},
    )

    assert (settled.status, settled.outcome) == (MarketStatus.RESOLVED, ResolutionOutcome.YES)
    async with container.uow_factory() as uow:
        resolution = await uow.resolutions.get_by_market(market_id)
        positions = await uow.positions.list_for_market(market_id)
    for user in (alice, bob):
        owed = view.payout_micro([row for row in positions if row.user_id == user.id], resolution)
        assert owed is not None
        assert await factory.balance_micro(user) == before[user.id] + owed
    me = await portal.ok(alice, "GET", "/me", MeResponse)
    assert me.balance_cr == to_cr(await factory.balance_micro(alice))
    assert await factory.balance_micro(alice) > before[alice.id]
    await assert_books_balance(container.uow_factory)


async def test_without_redis_the_order_limit_is_kept_by_the_limiter_of_this_process(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A.4: the same rule, MAX_ORDERS_PER_MINUTE per user, counted in memory. It runs
    on the application's clock: a minute after a user's first request their window is
    over. Somebody else's requests are counted for that somebody."""
    proposer = await factory.user()
    alice, bob = [await factory.user(balance_micro=WALLET) for _ in range(2)]
    market_id = await open_market(portal, proposer, await factory.admin(), clock)
    for _ in range(MAX_ORDERS):
        await portal.buy(alice, market_id, "YES", SMALLEST_TRADE)
    trades = await count_rows(container.uow_factory, Trade)
    balance = await factory.balance_micro(alice)

    over = await portal.send(
        alice,
        "POST",
        f"/markets/{market_id}/trades",
        {"side": "BUY", "outcome": "YES", "amount_cr": format_cr(SMALLEST_TRADE)},
        key=uuid7(),
    )

    assert over.status_code == 429, over.text
    assert over.json()["code"] == "RATE_LIMITED"
    assert over.json()["details"] == {"retry_after_seconds": ORDER_WINDOW_SECONDS}
    assert over.headers["retry-after"] == str(ORDER_WINDOW_SECONDS)
    assert trades == MAX_ORDERS
    assert await count_rows(container.uow_factory, Trade) == trades
    assert await factory.balance_micro(alice) == balance == WALLET - MAX_ORDERS * SMALLEST_TRADE
    await portal.buy(bob, market_id, "NO", SMALLEST_TRADE)

    clock.advance(timedelta(seconds=ORDER_WINDOW_SECONDS))
    await portal.buy(alice, market_id, "YES", SMALLEST_TRADE)

    assert await count_rows(container.uow_factory, Trade) == MAX_ORDERS + 2
    await assert_books_balance(container.uow_factory)


async def test_without_redis_a_code_is_not_redeemed_and_the_answer_is_503(
    portal: Portal, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """SPEC §9 and §12: "with Redis stopped ... code redemption returns 503". The limit
    on attempts fails closed: with nothing to count them, no attempt is looked at.
    Creating a batch needs no Redis, and neither does listing it."""
    admin = await factory.admin()
    nora = await factory.user(balance_micro=WALLET)
    created = await portal.ok(
        admin,
        "POST",
        "/admin/code-batches",
        CodeBatchCreatedResponse,
        {
            "label": "Welcome week",
            "count": 2,
            "expires_at": (clock.now() + WEEK).isoformat(),
            "one_per_user": True,
        },
        status_code=201,
    )
    real = created.codes[0].code

    answers = [
        await portal.send(nora, "POST", "/codes/redeem", {"code": code})
        for code in (real, real, "0000-0000-0000", "not a code")
    ]

    for answer in answers:
        assert answer.status_code == 503, answer.text
        assert answer.json()["code"] == "SERVICE_UNAVAILABLE"
        assert answer.json()["details"] == {}
    assert await factory.balance_micro(nora) == WALLET
    async with container.uow_factory() as uow:
        codes = (await uow.session.scalars(select(Code))).all()
        entries = (await uow.session.scalars(select(JournalEntry))).all()
    assert [code.status for code in codes] == [CodeStatus.ACTIVE] * 2
    assert JournalKind.CODE_REDEMPTION not in {entry.kind for entry in entries}
    (batch,) = (await portal.ok(admin, "GET", "/admin/code-batches", CodeBatchListResponse)).items
    assert (batch.active_count, batch.redeemed_count) == (2, 0)
    await assert_books_balance(container.uow_factory)


async def test_without_redis_a_voucher_is_issued_and_scanned(
    portal: Portal, container: Container, factory: Factory
) -> None:
    """What credits buy needs Postgres alone: the voucher, its fresh code and the scan."""
    product = container.settings.vouchers.products[0]
    nora = await factory.user(balance_micro=WALLET)
    barista = await factory.barista()

    issued = await portal.ok(
        nora,
        "POST",
        "/vouchers",
        VoucherIssuedResponse,
        {"product_id": product.id},
        status_code=201,
    )
    scanned = await portal.ok(
        barista, "POST", "/barista/vouchers/redeem", VoucherRedeemResponse, {"code": issued.code}
    )

    assert issued.balance_cr == to_cr(WALLET - product.price_micro)
    assert (scanned.voucher_id, scanned.product_id) == (issued.id, product.id)
    async with container.uow_factory() as uow:
        (voucher,) = (await uow.session.scalars(select(Voucher))).all()
    assert (voucher.status, voucher.redeemed_by) == (VoucherStatus.REDEEMED, barista.id)
    await assert_books_balance(container.uow_factory)
