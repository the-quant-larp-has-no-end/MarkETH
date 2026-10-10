"""The admin risk page and its alerts: `RiskService` (SPEC §5, §12, A.5, A.6).

Acceptance criterion of SPEC §12: "`/admin/risk` shows open markets, summed loss
budgets against the cap and outstanding obligations."

Every expected amount is built from what went into the book: balances handed out,
costs and proceeds as `app.pricing.lmsr` quotes them, the price of a product, the value
of a code. The trades are the factory's buys and the `sell` below, which is its
counterpart; vouchers and codes are written the way A.8 books them, so no test here
waits for the trading, voucher or code service.
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid7
from zoneinfo import ZoneInfo

import httpx
import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings, VoucherProduct
from app.core.enums import (
    STORED_MARKET_STATUSES,
    AccountKind,
    AlertSeverity,
    CodeStatus,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    TradeSide,
    UserRole,
    UserStatus,
    VoucherStatus,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.identity import AuthenticatedUser
from app.core.security import sha256_hex
from app.core.units import MICRO, format_cr, to_cr
from app.models import (
    Alert,
    AuditLogEntry,
    Code,
    CodeBatch,
    JournalEntry,
    Market,
    Trade,
    User,
    Voucher,
)
from app.pricing import lmsr
from app.schemas.admin.risk import AlertItem, RiskMarketRow, RiskResponse
from app.schemas.admin.users import AdjustBalanceRequest
from app.services.ledger import (
    LedgerRef,
    PostingLine,
    code_redemption_key,
    trade_key,
    voucher_issue_key,
    voucher_redeem_key,
    voucher_refund_key,
)
from tests.support.books import assert_books_balance, count_rows, system_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.economy import PRECISION, TIER_B
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

RISK: Final[str] = "/api/admin/risk"
WALLET_MICRO: Final[int] = 500 * MICRO
"""What a trader of these tests starts with."""

DEEP_WALLET_MICRO: Final[int] = 2_000 * MICRO
"""Affords whatever one trader can buy in the random books below: six times 150 shares
cost less than their number."""

ZURICH: Final[ZoneInfo] = ZoneInfo("Europe/Zurich")


# --- Writing the book ------------------------------------------------------------------


def state_of(market: Market) -> lmsr.MarketState:
    return lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)


async def buy(
    factory: Factory, user: User, market: Market, outcome: Outcome, shares_micro: int
) -> int:
    """A buy of the factory. Returns what it cost, as lmsr prices it on the market
    before the buy moved it."""
    quote = lmsr.quote_buy_shares(state_of(market), outcome, shares_micro, precision=PRECISION)
    await factory.position(user, market, outcome, shares_micro=shares_micro)
    return quote.amount_micro


async def sell(
    container: Container, user: User, market: Market, outcome: Outcome, shares_micro: int
) -> int:
    """Let `user` sell shares they hold, as a committed trade would (A.4, A.8): the
    counterpart of `Factory.position`. Returns the proceeds lmsr quotes.

    Books `TRADE_SELL` (market to user account), lowers the position and raises its
    proceeds, adds the trade row and moves the market's quantity, version and volume.
    `market` is brought up to the committed state in place.
    """
    now = container.clock.now()
    async with container.uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        market_account = await uow.accounts.get_market_account(market.id)
        position = await uow.positions.get(user.id, market.id, outcome, for_update=True)
        assert row is not None
        assert account is not None
        assert market_account is not None
        assert position is not None
        quote = lmsr.quote_sell_shares(state_of(row), outcome, shares_micro, precision=PRECISION)
        trade_id = uuid7()
        await container.ledger.post(
            uow,
            kind=JournalKind.TRADE_SELL,
            ref_type=LedgerRef.TRADE,
            ref_id=trade_id,
            idempotency_key=trade_key(trade_id),
            lines=[
                PostingLine(market_account.id, -quote.amount_micro),
                PostingLine(account.id, quote.amount_micro),
            ],
            at=now,
        )
        position.shares_micro -= shares_micro
        position.proceeds_micro += quote.amount_micro
        row.q_yes_micro = quote.after.q_yes
        row.q_no_micro = quote.after.q_no
        row.version += 1
        row.volume_micro += quote.amount_micro
        await uow.trades.add(
            Trade(
                id=trade_id,
                market_id=market.id,
                user_id=user.id,
                side=TradeSide.SELL,
                outcome=outcome,
                shares_micro=shares_micro,
                amount_micro=quote.amount_micro,
                p_yes_before_micro=quote.p_yes_before_micro,
                p_yes_after_micro=quote.p_yes_after_micro,
                market_version=row.version,
                created_at=now,
            )
        )
    market.q_yes_micro = row.q_yes_micro
    market.q_no_micro = row.q_no_micro
    market.version = row.version
    market.volume_micro = row.volume_micro
    return quote.amount_micro


async def issue_voucher(container: Container, user: User, product: VoucherProduct) -> Voucher:
    """A pending voucher as issuing one leaves it: the row, and `VOUCHER_ISSUE` moving
    its price from the user to `vis:redemption_pending` (A.8)."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        voucher = await uow.vouchers.add(
            Voucher(
                user_id=user.id,
                product_id=product.id,
                product_name=product.name,
                code_hash=sha256_hex(str(uuid7())),
                status=VoucherStatus.ACTIVE,
                value_micro=product.price_micro,
                created_at=now,
                expires_at=now + container.settings.vouchers.ttl,
            )
        )
        await container.ledger.post(
            uow,
            kind=JournalKind.VOUCHER_ISSUE,
            ref_type=LedgerRef.VOUCHER,
            ref_id=voucher.id,
            idempotency_key=voucher_issue_key(voucher.id),
            lines=[
                PostingLine(account.id, -voucher.value_micro),
                PostingLine(pending.id, voucher.value_micro),
            ],
            at=now,
        )
    return voucher


async def redeem_voucher(container: Container, voucher: Voucher, barista: User) -> None:
    """What a barista's scan leaves: `VOUCHER_REDEEM` from `vis:redemption_pending` to
    `vis:redeemed`, and the voucher REDEEMED (A.8)."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        assert await uow.accounts.get_user_account(voucher.user_id, for_update=True) is not None
        row = await uow.vouchers.get_owned(voucher.id, voucher.user_id, for_update=True)
        assert row is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        redeemed = await uow.accounts.get_system_account(AccountKind.VIS_REDEEMED)
        await container.ledger.post(
            uow,
            kind=JournalKind.VOUCHER_REDEEM,
            ref_type=LedgerRef.VOUCHER,
            ref_id=row.id,
            idempotency_key=voucher_redeem_key(row.id),
            lines=[
                PostingLine(pending.id, -row.value_micro),
                PostingLine(redeemed.id, row.value_micro),
            ],
            at=now,
        )
        row.status = VoucherStatus.REDEEMED
        row.redeemed_at = now
        row.redeemed_by = barista.id


async def refund_voucher(container: Container, voucher: Voucher) -> None:
    """What the refund of an expired voucher leaves: `VOUCHER_REFUND` from
    `vis:redemption_pending` back to its user, and the voucher EXPIRED (A.8)."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(voucher.user_id, for_update=True)
        assert account is not None
        row = await uow.vouchers.get_owned(voucher.id, voucher.user_id, for_update=True)
        assert row is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        await container.ledger.post(
            uow,
            kind=JournalKind.VOUCHER_REFUND,
            ref_type=LedgerRef.VOUCHER,
            ref_id=row.id,
            idempotency_key=voucher_refund_key(row.id),
            lines=[
                PostingLine(pending.id, -row.value_micro),
                PostingLine(account.id, row.value_micro),
            ],
            at=now,
        )
        row.status = VoucherStatus.EXPIRED


async def code_batch(
    container: Container,
    creator: User,
    *,
    value_micro: int,
    active: int = 0,
    revoked: int = 0,
    redeemed_by: Sequence[User] = (),
    created_at: datetime | None = None,
    expires_in: timedelta = timedelta(days=30),
) -> CodeBatch:
    """A batch of codes as its creation and what followed left it: `active` codes
    nobody used, `revoked` ones, and one that each of `redeemed_by` redeemed, booked as
    `CODE_REDEMPTION` from `vis:issuance` (A.8). `expires_in` counts from now. An
    active code keeps its plain text, as the database asks; here that is a UUID."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        batch = await uow.code_batches.add(
            CodeBatch(
                label="Risk page test",
                value_micro=value_micro,
                count=active + revoked + len(redeemed_by),
                expires_at=now + expires_in,
                one_per_user=False,
                created_by=creator.id,
                created_at=created_at or now,
            )
        )
        await uow.codes.add_all(
            Code(
                batch_id=batch.id,
                code_hash=sha256_hex(plain),
                code=plain if status is CodeStatus.ACTIVE else None,
                status=status,
            )
            for status, count in ((CodeStatus.ACTIVE, active), (CodeStatus.REVOKED, revoked))
            for plain in (uuid7().hex for _ in range(count))
        )
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        for redeemer in redeemed_by:
            account = await uow.accounts.get_user_account(redeemer.id, for_update=True)
            assert account is not None
            code = await uow.codes.add(
                Code(
                    batch_id=batch.id,
                    code_hash=sha256_hex(str(uuid7())),
                    status=CodeStatus.REDEEMED,
                    redeemed_by=redeemer.id,
                    redeemed_at=now,
                )
            )
            entry = await container.ledger.post(
                uow,
                kind=JournalKind.CODE_REDEMPTION,
                ref_type=LedgerRef.CODE,
                ref_id=code.id,
                idempotency_key=code_redemption_key(code.id),
                lines=[
                    PostingLine(issuance.id, -value_micro),
                    PostingLine(account.id, value_micro),
                ],
                at=now,
            )
            code.entry_id = entry.id
    return batch


async def adjust(
    container: Container, factory: Factory, admin: User, user: User, amount_micro: int
) -> None:
    """An admin's balance adjustment, booked by the service that books them, at the
    clock's now and under a key of its own."""
    await container.admin_users.adjust_balance(
        factory.identity(admin),
        user.id,
        uuid7(),
        AdjustBalanceRequest(amount_cr=to_cr(amount_micro), reason="Booked in a test."),
    )


async def raise_alert(
    container: Container, message: str, severity: AlertSeverity = AlertSeverity.CRITICAL
) -> Alert:
    """An alert as the circuit breaker writes one."""
    async with container.uow_factory() as uow:
        return await uow.alerts.add(
            Alert(
                severity=severity,
                message=message,
                context={"invariant": "ledger_nets_to_zero", "market_id": str(uuid7())},
                created_at=container.clock.now(),
            )
        )


async def audit_rows(container: Container) -> Sequence[AuditLogEntry]:
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))).all()


async def stored_alert(container: Container, alert_id: int) -> Alert:
    async with container.uow_factory() as uow:
        alert = await uow.alerts.get(alert_id)
    assert alert is not None
    return alert


def row_of(page: RiskResponse, market: Market) -> RiskMarketRow:
    (row,) = [row for row in page.markets if row.id == market.id]
    return row


# --- The figures -----------------------------------------------------------------------


async def test_an_empty_book_owes_nothing_and_shows_the_caps_of_the_settings(
    container: Container, settings: Settings
) -> None:
    page = await container.risk.snapshot()

    nothing = to_cr(0)
    assert (page.open_markets, page.loss_budget_cr) == (0, nothing)
    assert page.max_open_markets == settings.risk.max_open_markets
    assert page.max_loss_budget_cr == to_cr(settings.risk.max_aggregate_loss_budget_micro)
    assert page.max_code_issuance_per_month_cr == to_cr(settings.codes.max_issuance_per_month_micro)
    owed = page.obligations
    assert (owed.user_balances_cr, owed.pending_vouchers_cr) == (nothing, nothing)
    assert (owed.market_exposure_cr, owed.total_cr) == (nothing, nothing)
    assert owed.alert_threshold_cr == to_cr(settings.risk.obligations_alert_threshold_micro)
    assert owed.above_threshold is False
    assert (page.unredeemed_code_value_cr, page.code_issuance_this_month_cr) == (nothing, nothing)
    assert page.adjustments_this_month_cr == nothing
    assert (page.markets, page.alerts) == ([], [])


async def test_the_page_shows_open_markets_loss_budgets_and_outstanding_obligations(
    container: Container, factory: Factory, settings: Settings
) -> None:
    """The acceptance criterion, on a book with a market in every status.

    Obligations follow SPEC §5 with "each open market" read as every market that is
    not settled (A.6): the OPEN, the HALTED and the CLOSED one each still owe a payout.
    The two caps count OPEN and HALTED only, as approval does (A.5).
    """
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    carol = await factory.user(balance_micro=120 * MICRO)
    opened = await factory.market(tier=MarketTier.TIGHT, close_in=timedelta(days=3))
    halted = await factory.market(
        status=MarketStatus.HALTED, tier=MarketTier.MEDIUM, close_in=timedelta(days=1)
    )
    closed = await factory.market(status=MarketStatus.CLOSED, close_in=timedelta(days=2))
    for settled_or_hidden in (
        MarketStatus.RESOLVED,
        MarketStatus.VOID,
        MarketStatus.PROPOSED,
        MarketStatus.REJECTED,
    ):
        await factory.market(status=settled_or_hidden, created_by=carol)
    paid_micro = {
        opened.id: await buy(factory, alice, opened, Outcome.YES, 40 * MICRO)
        + await buy(factory, bob, opened, Outcome.NO, 25 * MICRO),
        halted.id: await buy(factory, alice, halted, Outcome.NO, 30 * MICRO),
        closed.id: await buy(factory, bob, closed, Outcome.YES, 60 * MICRO),
    }
    product = settings.vouchers.products[0]
    await issue_voucher(container, alice, product)

    page = await container.risk.snapshot()

    assert page.open_markets == 2, "the OPEN and the HALTED one"
    assert page.loss_budget_cr == to_cr(
        lmsr.max_loss_micro(TIER_B[MarketTier.TIGHT], precision=PRECISION)
        + lmsr.max_loss_micro(TIER_B[MarketTier.MEDIUM], precision=PRECISION)
    )
    exposure_micro = {
        opened.id: 40 * MICRO - paid_micro[opened.id],
        halted.id: 30 * MICRO - paid_micro[halted.id],
        closed.id: 60 * MICRO - paid_micro[closed.id],
    }
    balances_micro = 2 * WALLET_MICRO + 120 * MICRO - sum(paid_micro.values()) - product.price_micro
    owed = page.obligations
    assert owed.user_balances_cr == to_cr(balances_micro)
    assert owed.pending_vouchers_cr == to_cr(product.price_micro)
    assert owed.market_exposure_cr == to_cr(sum(exposure_micro.values()))
    assert owed.total_cr == to_cr(
        balances_micro + product.price_micro + sum(exposure_micro.values())
    )
    assert owed.above_threshold is False
    # One row per market that is not settled, soonest deadline first.
    assert [row.id for row in page.markets] == [halted.id, closed.id, opened.id]
    assert {row.id: row.exposure_cr for row in page.markets} == {
        market_id: to_cr(micro) for market_id, micro in exposure_micro.items()
    }
    assert all(row.exposure_cr > 0 for row in page.markets), "each of the three owes"
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("status", STORED_MARKET_STATUSES, ids=str)
async def test_a_market_is_owed_on_until_it_is_settled_and_capped_while_open_or_halted(
    container: Container, factory: Factory, owner_engine: AsyncEngine, status: MarketStatus
) -> None:
    """One and the same traded market under each stored status. The status is written
    behind the application's back, so that nothing but the status differs: the same
    quantities, the same account, the same budget."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market()
    paid_micro = await buy(factory, alice, market, Outcome.YES, 80 * MICRO)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE markets SET status = :status WHERE id = :id"),
            {"status": status.value, "id": market.id},
        )

    page = await container.risk.snapshot()

    owed_on = status in (MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED)
    capped = status in (MarketStatus.OPEN, MarketStatus.HALTED)
    assert page.obligations.market_exposure_cr == to_cr(80 * MICRO - paid_micro if owed_on else 0)
    assert [row.id for row in page.markets] == ([market.id] if owed_on else [])
    assert page.open_markets == (1 if capped else 0)
    assert page.loss_budget_cr == to_cr(market.budget_micro if capped else 0)
    assert page.obligations.user_balances_cr == to_cr(WALLET_MICRO - paid_micro)
    await assert_books_balance(container.uow_factory)


async def test_the_warning_shows_above_the_threshold_and_not_at_it(
    container: Container, factory: Factory, settings: Settings
) -> None:
    """SPEC §5: "an alert above a configurable threshold". It is a flag of the page;
    reading the page writes no `alerts` row (A.6)."""
    threshold_micro = settings.risk.obligations_alert_threshold_micro
    await factory.user(balance_micro=threshold_micro)

    at_it = await container.risk.snapshot()

    assert at_it.obligations.total_cr == at_it.obligations.alert_threshold_cr
    assert at_it.obligations.alert_threshold_cr == to_cr(threshold_micro)
    assert at_it.obligations.above_threshold is False

    await factory.user(balance_micro=1)
    above = await container.risk.snapshot()

    assert above.obligations.total_cr == to_cr(threshold_micro + 1)
    assert above.obligations.above_threshold is True
    assert above.alerts == []
    assert await count_rows(container.uow_factory, Alert) == 0
    assert await count_rows(container.uow_factory, AuditLogEntry) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_voucher_is_owed_until_it_is_redeemed_or_refunded(
    container: Container, factory: Factory, settings: Settings
) -> None:
    """Pending vouchers are the ACTIVE ones: credit parked in `vis:redemption_pending`.
    Turning credits into a voucher leaves the obligations as they were; handing the
    product over pays the debt; a refund makes it a balance again."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    barista = await factory.barista()
    by_price = sorted(settings.vouchers.products, key=lambda product: product.price_micro)
    cheapest, middle, dearest = by_price[0], by_price[len(by_price) // 2], by_price[-1]
    assert cheapest.price_micro < middle.price_micro < dearest.price_micro

    kept = await issue_voucher(container, alice, dearest)
    scanned = await issue_voucher(container, alice, middle)
    lapsed = await issue_voucher(container, alice, cheapest)
    issued = await container.risk.snapshot()

    all_three_micro = dearest.price_micro + middle.price_micro + cheapest.price_micro
    assert issued.obligations.pending_vouchers_cr == to_cr(all_three_micro)
    assert issued.obligations.user_balances_cr == to_cr(WALLET_MICRO - all_three_micro)
    assert issued.obligations.total_cr == to_cr(WALLET_MICRO)

    await redeem_voucher(container, scanned, barista)
    await refund_voucher(container, lapsed)
    settled = await container.risk.snapshot()

    assert settled.obligations.pending_vouchers_cr == to_cr(dearest.price_micro)
    assert settled.obligations.user_balances_cr == to_cr(
        WALLET_MICRO - dearest.price_micro - middle.price_micro
    )
    assert settled.obligations.total_cr == to_cr(WALLET_MICRO - middle.price_micro)
    # What is pending is what `vis:redemption_pending` holds.
    assert kept.value_micro == await system_balance(
        container.uow_factory, AccountKind.VIS_REDEMPTION_PENDING
    )
    await assert_books_balance(container.uow_factory)


async def test_an_active_voucher_past_its_expiry_is_owed_until_its_refund_is_booked(
    container: Container, factory: Factory, clock: FrozenClock, settings: Settings
) -> None:
    """Nobody refunds a voucher at the instant it expires: its credit stays in
    `vis:redemption_pending` until upkeep or a scan books the refund, and until then it
    is owed."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    product = settings.vouchers.products[0]
    await issue_voucher(container, alice, product)
    clock.advance(settings.vouchers.ttl + timedelta(days=1))

    page = await container.risk.snapshot()

    assert page.obligations.pending_vouchers_cr == to_cr(product.price_micro)
    assert page.obligations.total_cr == to_cr(WALLET_MICRO)
    await assert_books_balance(container.uow_factory)


async def test_the_value_of_codes_still_out_is_shown_beside_the_obligations_not_inside(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """A.6: active, unexpired codes are credit VIS has promised and nobody holds yet.
    A redeemed code is a balance and counts there; a revoked or an expired one is
    nothing."""
    admin = await factory.admin()
    alice = await factory.user()
    await code_batch(
        container, admin, value_micro=35 * MICRO, active=3, revoked=2, redeemed_by=[alice]
    )
    await code_batch(
        container, admin, value_micro=500 * MICRO, active=4, expires_in=timedelta(hours=1)
    )

    page = await container.risk.snapshot()

    assert page.unredeemed_code_value_cr == to_cr((3 * 35 + 4 * 500) * MICRO)
    assert page.obligations.user_balances_cr == to_cr(35 * MICRO), "the code Alice redeemed"
    assert page.obligations.total_cr == to_cr(35 * MICRO)
    # Everything but the two revoked codes counts against this month's issuance.
    assert page.code_issuance_this_month_cr == to_cr((4 * 35 + 4 * 500) * MICRO)

    clock.advance(timedelta(hours=1))
    an_hour_later = await container.risk.snapshot()

    assert an_hour_later.unredeemed_code_value_cr == to_cr(3 * 35 * MICRO), "one batch expired"
    assert an_hour_later.code_issuance_this_month_cr == page.code_issuance_this_month_cr
    await assert_books_balance(container.uow_factory)


async def test_code_issuance_is_counted_from_the_first_of_the_month_in_the_business_timezone(
    container: Container, factory: Factory, settings: Settings
) -> None:
    """`MAX_CODE_ISSUANCE_PER_MONTH` is a calendar month in `APP_TIMEZONE`. Half an hour
    into October in Zurich is still September in UTC."""
    assert settings.app.timezone == ZURICH
    admin = await factory.admin()
    midnight = datetime(TEST_NOW.year, TEST_NOW.month, 1, tzinfo=ZURICH)
    assert midnight.utcoffset() == timedelta(hours=2)
    await code_batch(
        container,
        admin,
        value_micro=10 * MICRO,
        active=1,
        created_at=midnight + timedelta(minutes=30),
    )
    await code_batch(
        container,
        admin,
        value_micro=7 * MICRO,
        active=1,
        created_at=midnight - timedelta(minutes=30),
    )
    await code_batch(container, admin, value_micro=3 * MICRO, active=1, created_at=midnight)

    page = await container.risk.snapshot()

    assert page.code_issuance_this_month_cr == to_cr(13 * MICRO)
    assert page.unredeemed_code_value_cr == to_cr(20 * MICRO), "whenever they were issued"


async def test_the_months_adjustments_are_shown_net_and_signed(
    container: Container, factory: Factory
) -> None:
    """What the admins added to balances this month minus what they took away. No other
    way credits reach a user counts: not the grant a balance of the factory is booked
    as, not a redeemed code. A month in which more was taken than given shows below
    zero."""
    first, second = await factory.admin(), await factory.admin(1)
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    await code_batch(container, first, value_micro=35 * MICRO, redeemed_by=[alice])
    assert (await container.risk.snapshot()).adjustments_this_month_cr == to_cr(0)

    await adjust(container, factory, first, alice, 30 * MICRO)
    await adjust(container, factory, second, alice, 12_500_000)
    await adjust(container, factory, first, bob, -40 * MICRO)
    given = await container.risk.snapshot()
    await adjust(container, factory, second, bob, -100 * MICRO)
    taken = await container.risk.snapshot()

    assert given.adjustments_this_month_cr == to_cr(2_500_000)
    assert taken.adjustments_this_month_cr == to_cr(-97_500_000)
    assert given.code_issuance_this_month_cr == taken.code_issuance_this_month_cr
    assert given.code_issuance_this_month_cr == to_cr(35 * MICRO), "codes are counted apart"
    # The balances the page owes on moved by exactly that much.
    assert taken.obligations.user_balances_cr == to_cr(2 * WALLET_MICRO + 35 * MICRO - 97_500_000)
    await assert_books_balance(container.uow_factory)


async def test_adjustments_are_counted_from_the_first_of_the_month_in_the_business_timezone(
    container: Container, factory: Factory, settings: Settings, clock: FrozenClock
) -> None:
    """A calendar month of `APP_TIMEZONE`, by the instant an adjustment was booked. Half
    an hour into October in Zurich is still September in UTC and counts for October;
    half an hour before it does not. The month ends the instant the next one begins."""
    assert settings.app.timezone == ZURICH
    october = datetime(TEST_NOW.year, TEST_NOW.month, 1, tzinfo=ZURICH)
    november = datetime(TEST_NOW.year, TEST_NOW.month + 1, 1, tzinfo=ZURICH)
    instant = timedelta(microseconds=1)
    assert october.utcoffset() == timedelta(hours=2)
    assert (october + timedelta(minutes=30)).astimezone(UTC).month == TEST_NOW.month - 1
    clock.set(october - timedelta(hours=1))
    admin = await factory.admin()
    alice = await factory.user(balance_micro=WALLET_MICRO)

    clock.set(october - timedelta(minutes=30))
    await adjust(container, factory, admin, alice, 7 * MICRO)
    clock.set(october - instant)
    in_september = await container.risk.snapshot()
    clock.set(october)
    await adjust(container, factory, admin, alice, 3 * MICRO)
    at_the_first_instant = await container.risk.snapshot()
    clock.set(october + timedelta(minutes=30))
    await adjust(container, factory, admin, alice, 10 * MICRO)
    clock.set(TEST_NOW)
    in_october = await container.risk.snapshot()
    clock.set(november - instant)
    at_the_last_instant = await container.risk.snapshot()
    clock.set(november)
    in_november = await container.risk.snapshot()
    await adjust(container, factory, admin, alice, -MICRO)
    after_a_debit_in_november = await container.risk.snapshot()

    assert in_september.adjustments_this_month_cr == to_cr(7 * MICRO)
    assert at_the_first_instant.adjustments_this_month_cr == to_cr(3 * MICRO)
    assert in_october.adjustments_this_month_cr == to_cr(13 * MICRO)
    assert at_the_last_instant.adjustments_this_month_cr == to_cr(13 * MICRO)
    assert in_november.adjustments_this_month_cr == to_cr(0)
    assert after_a_debit_in_november.adjustments_this_month_cr == to_cr(-MICRO)
    await assert_books_balance(container.uow_factory)


# --- The rows --------------------------------------------------------------------------


async def test_a_row_shows_a_market_as_its_book_stands(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market(
        tier=MarketTier.TIGHT, question="Will it snow on the Polyterrasse?"
    )
    paid_micro = await buy(factory, alice, market, Outcome.YES, 40 * MICRO)
    paid_micro += await buy(factory, bob, market, Outcome.NO, 25 * MICRO)
    b_micro = TIER_B[MarketTier.TIGHT]
    book = lmsr.MarketState(q_yes=40 * MICRO, q_no=25 * MICRO, b=b_micro)

    page = await container.risk.snapshot()

    assert page.markets == [
        RiskMarketRow(
            id=market.id,
            question="Will it snow on the Polyterrasse?",
            status=MarketStatus.OPEN,
            tier=MarketTier.TIGHT,
            b_cr=to_cr(b_micro),
            budget_cr=to_cr(lmsr.max_loss_micro(b_micro, precision=PRECISION)),
            p_yes=container.market_view.probabilities(
                lmsr.price_yes_micro(book, precision=PRECISION)
            )[0],
            q_yes=to_cr(40 * MICRO),
            q_no=to_cr(25 * MICRO),
            account_balance_cr=to_cr(paid_micro),
            exposure_cr=to_cr(40 * MICRO - paid_micro),
            void_cost_cr=to_cr(0),
            volume_cr=to_cr(paid_micro),
            close_at=market.close_at,
        )
    ]
    (row,) = page.markets
    assert row.p_yes > 0.5, "more YES than NO was bought"
    assert row.exposure_cr <= row.budget_cr
    await assert_books_balance(container.uow_factory)


async def test_a_market_held_equally_on_both_sides_owes_nothing_and_its_exposure_is_signed(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    """Whichever outcome wins, the account covers the payout: costs are rounded up, so
    it holds the winning shares and a millionth or two beyond. The exposure is that
    dust below zero, which the page shows with its sign."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market()
    paid_micro = await buy(factory, alice, market, Outcome.YES, 100 * MICRO)
    paid_micro += await buy(factory, bob, market, Outcome.NO, 100 * MICRO)
    assert paid_micro > 100 * MICRO

    page = await container.risk.snapshot()

    row = row_of(page, market)
    assert row.exposure_cr == to_cr(100 * MICRO - paid_micro)
    assert row.exposure_cr < 0
    assert page.obligations.market_exposure_cr == row.exposure_cr
    assert page.obligations.total_cr == to_cr(2 * WALLET_MICRO - paid_micro) + row.exposure_cr
    as_user(await factory.admin())
    body = (await client.get(RISK)).json()
    assert body["markets"][0]["exposure_cr"] == format_cr(100 * MICRO - paid_micro)
    assert body["markets"][0]["exposure_cr"].startswith("-0.00000")
    assert body["obligations"]["market_exposure_cr"] == body["markets"][0]["exposure_cr"]
    await assert_books_balance(container.uow_factory)


async def test_a_market_past_its_deadline_is_shown_closed_and_still_counts_as_open_for_the_caps(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The row shows the effective status (A.4). The caps are on stored statuses,
    because that is what approval checks (A.5): the market keeps its place until an
    admin settles it."""
    market = await factory.market(close_in=timedelta(hours=1))
    before = await container.risk.snapshot()
    clock.advance(timedelta(hours=1))

    at_the_deadline = await container.risk.snapshot()

    assert row_of(before, market).status is MarketStatus.OPEN
    assert row_of(at_the_deadline, market).status is MarketStatus.CLOSED
    assert (before.open_markets, at_the_deadline.open_markets) == (1, 1)
    assert at_the_deadline.loss_budget_cr == to_cr(market.budget_micro)


async def test_voiding_a_market_nobody_sold_out_of_costs_nothing(
    container: Container, factory: Factory
) -> None:
    """A void refunds every net stake. While nobody sold, the net stakes are what the
    market's account took in, also when both outcomes were bought."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    traded = await factory.market(close_in=timedelta(days=1))
    untraded = await factory.market(close_in=timedelta(days=2))
    await buy(factory, alice, traded, Outcome.YES, 90 * MICRO)
    await buy(factory, alice, traded, Outcome.NO, 10 * MICRO)
    await buy(factory, bob, traded, Outcome.NO, 55 * MICRO)

    page = await container.risk.snapshot()

    assert [row.void_cost_cr for row in page.markets] == [to_cr(0), to_cr(0)]
    assert row_of(page, untraded).account_balance_cr == to_cr(0)
    assert row_of(page, traded).account_balance_cr > 0
    await assert_books_balance(container.uow_factory)


async def test_voiding_a_market_costs_what_its_sellers_took_out_above_their_cost(
    container: Container, factory: Factory
) -> None:
    """Alice buys, Bob's buy moves the price up, Alice sells at a gain. A void now
    refunds Bob his whole cost; the account holds that minus Alice's gain, and
    `vis:mm_pnl` would have to add the gain. The figure is per market and not part of
    the obligations."""
    alice = await factory.user(balance_micro=WALLET_MICRO)
    bob = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market()
    other = await factory.market(close_in=timedelta(days=30))
    await buy(factory, alice, other, Outcome.NO, 20 * MICRO)
    alice_paid_micro = await buy(factory, alice, market, Outcome.YES, 150 * MICRO)
    bob_paid_micro = await buy(factory, bob, market, Outcome.YES, 150 * MICRO)
    alice_got_micro = await sell(container, alice, market, Outcome.YES, 150 * MICRO)
    gain_micro = alice_got_micro - alice_paid_micro
    assert gain_micro > 0

    page = await container.risk.snapshot()

    row = row_of(page, market)
    assert row.void_cost_cr == to_cr(gain_micro)
    assert row.account_balance_cr == to_cr(bob_paid_micro - gain_micro)
    assert row_of(page, other).void_cost_cr == to_cr(0)
    # The same figure through `MarketView`: the net stakes a void refunds, minus the account.
    async with container.uow_factory() as uow:
        positions = await uow.positions.list_for_market(market.id)
        account = await uow.accounts.get_market_account(market.id)
    assert account is not None
    refunds_micro = sum(
        container.market_view.net_stake_micro([p for p in positions if p.user_id == trader.id])
        for trader in (alice, bob)
    )
    assert refunds_micro == bob_paid_micro
    assert row.void_cost_cr == to_cr(refunds_micro - account.balance_micro)
    assert row.void_cost_cr == to_cr(
        container.market_view.void_cost_micro(refunds_micro, account.balance_micro)
    )
    # The page an admin voids the market from says the same.
    admin = factory.identity(await factory.admin())
    assert (await container.markets.detail(admin, market.id)).void_cost_cr == row.void_cost_cr
    assert (await container.markets.detail(admin, other.id)).void_cost_cr == to_cr(0)
    # Not an obligation: the total is balances, vouchers and exposure, as ever.
    owed = page.obligations
    assert owed.total_cr == owed.user_balances_cr + owed.market_exposure_cr
    assert owed.market_exposure_cr == sum(row.exposure_cr for row in page.markets)
    await assert_books_balance(container.uow_factory)


OPERATION = st.tuples(
    st.integers(min_value=0, max_value=1),
    st.sampled_from(Outcome),
    st.booleans(),
    st.integers(min_value=1, max_value=150),
)
"""One step of a random book: which of two traders, the outcome, whether to sell if
they hold any of it, and how many whole shares."""


@hypothesis_settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(operations=st.lists(OPERATION, min_size=1, max_size=6))
async def test_whatever_is_traded_the_exposure_stays_within_the_budget_and_a_void_never_pays_vis(
    container: Container,
    factory: Factory,
    operations: list[tuple[int, Outcome, bool, int]],
) -> None:
    """For any run of buys and sells on a market: its row shows the account as lmsr's
    costs and proceeds add up, the exposure is at most the budget (the LMSR bound), a
    void costs the net stakes minus the account and never less than nothing, the
    market's own detail tells an admin that same cost, and the page's total is the
    sum of its parts.

    Every example trades a market of its own; the markets of earlier examples stay in
    the book, which the sums over all rows have to cope with.
    """
    traders = [
        await factory.user(balance_micro=DEEP_WALLET_MICRO),
        await factory.user(balance_micro=DEEP_WALLET_MICRO),
    ]
    market = await factory.market()
    held_micro: dict[tuple[int, Outcome], int] = {}
    account_micro = 0
    for who, outcome, would_sell, shares in operations:
        holding = held_micro.get((who, outcome), 0)
        if would_sell and holding:
            sold_micro = min(shares * MICRO, holding)
            account_micro -= await sell(container, traders[who], market, outcome, sold_micro)
            held_micro[who, outcome] = holding - sold_micro
        else:
            account_micro += await buy(factory, traders[who], market, outcome, shares * MICRO)
            held_micro[who, outcome] = holding + shares * MICRO

    page = await container.risk.snapshot()

    row = row_of(page, market)
    async with container.uow_factory() as uow:
        positions = await uow.positions.list_for_market(market.id)
    refunds_micro = sum(
        container.market_view.net_stake_micro([p for p in positions if p.user_id == trader.id])
        for trader in traders
    )
    most_held_micro = max(
        sum(held for (_, outcome), held in held_micro.items() if outcome is side)
        for side in Outcome
    )
    assert row.account_balance_cr == to_cr(account_micro)
    assert row.exposure_cr == to_cr(most_held_micro - account_micro)
    assert row.exposure_cr <= row.budget_cr
    assert row.void_cost_cr == to_cr(refunds_micro - account_micro)
    assert row.void_cost_cr >= 0
    an_admin = factory.identity(await factory.user(role=UserRole.ADMIN))
    assert (await container.markets.detail(an_admin, market.id)).void_cost_cr == row.void_cost_cr
    owed = page.obligations
    assert owed.market_exposure_cr == sum(row.exposure_cr for row in page.markets)
    assert owed.total_cr == (
        owed.user_balances_cr + owed.pending_vouchers_cr + owed.market_exposure_cr
    )
    await assert_books_balance(container.uow_factory)


async def test_a_market_without_its_account_is_a_broken_book_not_a_row_with_a_balance_of_zero(
    container: Container, factory: Factory, owner_engine: AsyncEngine
) -> None:
    """Approval opens the account with the market. The page fails loudly instead of
    showing a balance the market does not have."""
    market = await factory.market()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM accounts WHERE market_id = :id"), {"id": market.id}
        )

    with pytest.raises(RuntimeError, match="has no account"):
        await container.risk.snapshot()


# --- The route -------------------------------------------------------------------------


async def test_the_route_answers_an_admin_with_amounts_as_decimal_strings(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market()
    paid_micro = await buy(factory, alice, market, Outcome.YES, 80 * MICRO)
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    as_user(await factory.admin())

    response = await client.get(RISK)

    assert response.status_code == 200
    body = response.json()
    assert RiskResponse.model_validate(body) == await container.risk.snapshot()
    assert body["open_markets"] == 1
    assert body["obligations"]["above_threshold"] is False
    amounts = (
        body["loss_budget_cr"],
        body["obligations"]["total_cr"],
        body["markets"][0]["exposure_cr"],
        body["markets"][0]["void_cost_cr"],
        body["unredeemed_code_value_cr"],
        body["adjustments_this_month_cr"],
    )
    assert all(isinstance(amount, str) for amount in amounts)
    assert body["markets"][0]["q_yes"] == "80"
    assert body["markets"][0]["void_cost_cr"] == "0"
    assert body["obligations"]["user_balances_cr"] == format_cr(WALLET_MICRO - paid_micro)
    assert [item["id"] for item in body["alerts"]] == [alert.id]
    assert body["alerts"][0]["acked_at"] is None
    await assert_books_balance(container.uow_factory)


async def test_the_risk_page_and_its_alerts_are_for_admins_only(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    """SPEC §12: non-admins get 403 on admin routes, and nothing is written."""
    alert = await raise_alert(container, "Invariant violated: positions_match_market")

    for refused in (await factory.user(), await factory.barista()):
        as_user(refused)
        page = await client.get(RISK)
        acknowledged = await client.post(f"/api/admin/alerts/{alert.id}/ack")

        assert (page.status_code, page.json()["code"]) == (403, "FORBIDDEN")
        assert (acknowledged.status_code, acknowledged.json()["code"]) == (403, "FORBIDDEN")

    assert (await stored_alert(container, alert.id)).acked_at is None
    assert await audit_rows(container) == []


@pytest.mark.postgres_down
async def test_with_postgres_unreachable_the_page_and_an_acknowledgement_are_503(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """SPEC §9: with Postgres down everything answers 503. The service lets the lost
    connection through, and the API says it once for every route."""
    as_user(
        AuthenticatedUser(
            id=uuid7(),
            email="admin@ethz.ch",
            name="Admin",
            username="admin0",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
    )

    for response in (await client.get(RISK), await client.post("/api/admin/alerts/1/ack")):
        assert (response.status_code, response.json()["code"]) == (503, "SERVICE_UNAVAILABLE")


# --- Alerts ----------------------------------------------------------------------------


async def test_alerts_nobody_acknowledged_are_listed_newest_first(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    first = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    clock.advance(timedelta(minutes=5))
    second = await raise_alert(container, "A note", AlertSeverity.INFO)
    clock.advance(timedelta(minutes=5))
    third = await raise_alert(container, "Invariant violated: market_within_budget")
    admin = factory.identity(await factory.admin())
    await container.risk.acknowledge_alert(admin, second.id)

    page = await container.risk.snapshot()

    assert page.alerts == [
        AlertItem(
            id=alert.id,
            severity=AlertSeverity.CRITICAL,
            message=alert.message,
            context=alert.context,
            created_at=alert.created_at,
            acked_at=None,
        )
        for alert in (third, first)
    ]
    assert page.alerts[0].context["invariant"] == "ledger_nets_to_zero"
    assert page.alerts[0].created_at == page.alerts[1].created_at + timedelta(minutes=10)


async def test_acknowledging_an_alert_marks_it_takes_it_off_the_page_and_is_audited(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    admin = await factory.admin()
    clock.advance(timedelta(minutes=7))

    item = await container.risk.acknowledge_alert(factory.identity(admin), alert.id)

    assert item == AlertItem(
        id=alert.id,
        severity=AlertSeverity.CRITICAL,
        message="Invariant violated: ledger_nets_to_zero",
        context=alert.context,
        created_at=alert.created_at,
        acked_at=clock.now(),
    )
    stored = await stored_alert(container, alert.id)
    assert (stored.acked_by, stored.acked_at) == (admin.id, clock.now())
    (audited,) = await audit_rows(container)
    assert (audited.actor_id, audited.action, audited.target) == (
        admin.id,
        "alert.ack",
        f"alert:{alert.id}",
    )
    assert (audited.before, audited.after) == (
        {"acked_at": None},
        {"acked_at": clock.now().isoformat()},
    )
    assert audited.created_at == clock.now()
    assert (await container.risk.snapshot()).alerts == []


async def test_an_alert_is_acknowledged_once(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The second acknowledgement is refused, whoever sends it, and changes nothing:
    the alert keeps who saw it first and when, and no second audit row is written."""
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    first = await factory.admin()
    second = await factory.admin(1)
    await container.risk.acknowledge_alert(factory.identity(first), alert.id)
    acknowledged_at = clock.now()
    clock.advance(timedelta(hours=1))

    for again in (first, second):
        with pytest.raises(ConflictError, match="acknowledged before"):
            await container.risk.acknowledge_alert(factory.identity(again), alert.id)

    stored = await stored_alert(container, alert.id)
    assert (stored.acked_by, stored.acked_at) == (first.id, acknowledged_at)
    assert len(await audit_rows(container)) == 1


async def test_acknowledging_an_alert_that_does_not_exist_is_refused_and_writes_nothing(
    container: Container, factory: Factory
) -> None:
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    admin = factory.identity(await factory.admin())

    with pytest.raises(NotFoundError):
        await container.risk.acknowledge_alert(admin, alert.id + 1)

    assert (await stored_alert(container, alert.id)).acked_at is None
    assert await audit_rows(container) == []


async def test_two_admins_acknowledging_at_once_leave_one_acknowledgement(
    container: Container, factory: Factory
) -> None:
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    admins = [await factory.admin(), await factory.admin(1)]

    results = await asyncio.gather(
        *(container.risk.acknowledge_alert(factory.identity(admin), alert.id) for admin in admins),
        return_exceptions=True,
    )

    taken = [result for result in results if isinstance(result, AlertItem)]
    refused = [result for result in results if isinstance(result, ConflictError)]
    assert (len(taken), len(refused)) == (1, 1), results
    stored = await stored_alert(container, alert.id)
    (audited,) = await audit_rows(container)
    assert audited.actor_id == stored.acked_by
    assert stored.acked_by in {admin.id for admin in admins}
    assert taken[0].acked_at == stored.acked_at


async def test_the_route_acknowledges_once_and_tells_a_used_alert_from_an_unknown_one(
    client: httpx.AsyncClient, as_user: ActAs, container: Container, factory: Factory
) -> None:
    alert = await raise_alert(container, "Invariant violated: ledger_nets_to_zero")
    admin = await factory.admin()
    as_user(admin)
    path = f"/api/admin/alerts/{alert.id}/ack"

    first = await client.post(path)
    second = await client.post(path)
    unknown = await client.post(f"/api/admin/alerts/{alert.id + 1}/ack")

    assert first.status_code == 200
    assert AlertItem.model_validate(first.json()).acked_at == container.clock.now()
    assert (second.status_code, second.json()["code"]) == (409, "CONFLICT")
    assert second.json()["message"] == "This alert was acknowledged before."
    assert (unknown.status_code, unknown.json()["code"]) == (404, "NOT_FOUND")
    assert [row.actor_id for row in await audit_rows(container)] == [admin.id]


async def test_reading_the_page_moves_no_credit_and_writes_no_row(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=WALLET_MICRO)
    market = await factory.market()
    await buy(factory, alice, market, Outcome.YES, 80 * MICRO)
    entries = await count_rows(container.uow_factory, JournalEntry)

    first = await container.risk.snapshot()
    second = await container.risk.snapshot()

    assert first == second
    assert await count_rows(container.uow_factory, JournalEntry) == entries
    assert await count_rows(container.uow_factory, AuditLogEntry) == 0
    assert await count_rows(container.uow_factory, Alert) == 0
    await assert_books_balance(container.uow_factory)
