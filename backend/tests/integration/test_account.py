"""`AccountService`: what a user reads about themselves (SPEC §10, §11, Appendix A.2, A.8).

`/me`, the portfolio, the trades, the ledger and the proposals are reads. What they
show is put in place here the way the services that write it leave it: buys through the
factory, and sells, settlements, vouchers, a redeemed code and an admin's adjustment
through the helpers below, each booked with the postings of Appendix A.8, so the books
balance under every answer. Every price, value, payout and refund a test expects is
asked of `MarketView`.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import event

from app.container import Container
from app.core.config import Settings, VoucherProduct
from app.core.enums import (
    AccountKind,
    DegreeLevel,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
    VoucherStatus,
)
from app.core.errors import ErrorCode, ValidationFailedError
from app.core.security import generate_code, hash_code
from app.core.units import MICRO, format_cr, to_cr
from app.models import Market, Position, Resolution, Trade, User, Voucher
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.common import PageQuery
from app.schemas.me import LedgerItem, PortfolioEntry, TradeItem
from app.services.account import LEDGER_DESCRIPTIONS, MAX_POSTING_ID
from app.services.ledger import (
    LedgerRef,
    PostingLine,
    admin_adjustment_key,
    code_redemption_key,
    resolution_key,
    trade_key,
    void_key,
    voucher_issue_key,
    voucher_redeem_key,
    voucher_refund_key,
)
from app.services.public_config import FIXED_TIERS
from tests.support.books import assert_books_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory, identity_of
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

BASE: Final[Settings] = load_base_settings()
CHEAPEST: Final[int] = BASE.vouchers.lowest_price_micro
"""What the cheapest product of VOUCHER_PRODUCTS takes, in µCR."""

STARTING: Final[int] = 500 * MICRO
"""What a trader of these tests starts with."""

FIRST_GRANT_DUE: Final[datetime] = datetime(2026, 11, 9, 14, 0, tzinfo=BASE.app.timezone)
SECOND_GRANT_DUE: Final[datetime] = datetime(2026, 12, 9, 14, 0, tzinfo=BASE.app.timezone)
"""When the first two monthly grants fall due for somebody who signed up at `TEST_NOW`:
a month and two months later, on its day and at its time of day in Zurich."""


def product(name: str) -> VoucherProduct:
    """The product of VOUCHER_PRODUCTS that is called `name`."""
    (found,) = [listed for listed in BASE.vouchers.products if listed.name == name]
    return found


# --- What the other services leave behind --------------------------------------------


async def sell(
    container: Container, user: User, market: Market, outcome: Outcome, shares_micro: int
) -> Trade:
    """Let `user` sell `shares_micro` of `outcome`, as a committed trade would: the
    counterpart of `Factory.position`. The proceeds are the LMSR's, booked as
    `TRADE_SELL` from the market's account to the user's."""
    now = container.clock.now()
    precision = container.settings.pricing.decimal_precision
    async with container.uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        market_account = await uow.accounts.get_market_account(market.id)
        position = await uow.positions.get(user.id, market.id, outcome, for_update=True)
        assert row is not None
        assert account is not None
        assert market_account is not None
        assert position is not None
        state = lmsr.MarketState(q_yes=row.q_yes_micro, q_no=row.q_no_micro, b=row.b_micro)
        quote = lmsr.quote_sell_shares(state, outcome, shares_micro, precision=precision)
        trade_id = uuid7()
        if quote.amount_micro:
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
        return await uow.trades.add(
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


async def settle(
    container: Container, market: Market, outcome: ResolutionOutcome, by: User
) -> Resolution:
    """Leave `market` as a settlement leaves it (A.5, A.8).

    One `MARKET_RESOLUTION` or `MARKET_VOID` entry: every trader is credited what
    `MarketView` says the market owes them, the market's account is emptied and
    `vis:mm_pnl` takes the difference. A resolution keeps the positions as they are; a
    void sets their shares and the market's quantities to zero.
    """
    view = container.market_view
    now = container.clock.now()
    void = outcome is ResolutionOutcome.VOID
    async with container.uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        assert row is not None
        resolution = await uow.resolutions.add(
            Resolution(
                market_id=market.id,
                outcome=outcome,
                evidence="Settled in a test.",
                resolved_by=by.id,
                created_at=now,
            )
        )
        positions = await uow.positions.list_for_market(market.id)
        by_user: dict[UUID, list[Position]] = {}
        for position in positions:
            by_user.setdefault(position.user_id, []).append(position)
        owed: dict[UUID, int] = {}
        for user_id, own in by_user.items():
            amount = (
                view.refund_micro(own, resolution) if void else view.payout_micro(own, resolution)
            )
            assert amount is not None
            owed[user_id] = amount

        lines: list[PostingLine] = []
        for account in await uow.accounts.lock_user_accounts(list(owed)):
            assert account.owner_id is not None
            lines.append(PostingLine(account.id, owed[account.owner_id]))
        market_account = await uow.accounts.get_market_account(market.id)
        assert market_account is not None
        mm_pnl = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
        held_micro = market_account.balance_micro
        lines.append(PostingLine(market_account.id, -held_micro))
        lines.append(PostingLine(mm_pnl.id, held_micro - sum(owed.values())))
        if any(line.amount_micro for line in lines):
            await container.ledger.post(
                uow,
                kind=JournalKind.MARKET_VOID if void else JournalKind.MARKET_RESOLUTION,
                ref_type=LedgerRef.MARKET,
                ref_id=market.id,
                idempotency_key=void_key(market.id) if void else resolution_key(market.id),
                lines=lines,
                at=now,
            )
        if void:
            for position in positions:
                position.shares_micro = 0
            row.q_yes_micro = 0
            row.q_no_micro = 0
        row.status = MarketStatus.VOID if void else MarketStatus.RESOLVED
        row.closed_at = now
    return resolution


async def issue_voucher(container: Container, user: User, bought: VoucherProduct) -> Voucher:
    """A voucher as `VoucherService.issue` books it: the row keeps what the product
    was, and `VOUCHER_ISSUE` moves its price from the user to `vis:redemption_pending`."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        voucher = await uow.vouchers.add(
            Voucher(
                user_id=user.id,
                product_id=bought.id,
                product_name=bought.name,
                code_hash=hash_code(generate_code(container.settings.vouchers.code_length)),
                status=VoucherStatus.ACTIVE,
                value_micro=bought.price_micro,
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
                PostingLine(account.id, -bought.price_micro),
                PostingLine(pending.id, bought.price_micro),
            ],
            at=now,
        )
    return voucher


async def refund_voucher(container: Container, voucher: Voucher) -> None:
    """An expired voucher as `VoucherService.expire_due` leaves it: EXPIRED, and
    `VOUCHER_REFUND` from `vis:redemption_pending` back to its owner."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(voucher.user_id, for_update=True)
        row = await uow.vouchers.get_owned(voucher.id, voucher.user_id, for_update=True)
        assert account is not None
        assert row is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        row.status = VoucherStatus.EXPIRED
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


async def redeem_voucher(container: Container, voucher: Voucher, barista: User) -> None:
    """A scanned voucher as `VoucherService.redeem` leaves it: REDEEMED, and
    `VOUCHER_REDEEM` between two accounts of VIS."""
    now = container.clock.now()
    async with container.uow_factory() as uow:
        await uow.accounts.get_user_account(voucher.user_id, for_update=True)
        row = await uow.vouchers.get_owned(voucher.id, voucher.user_id, for_update=True)
        assert row is not None
        pending = await uow.accounts.get_system_account(AccountKind.VIS_REDEMPTION_PENDING)
        redeemed = await uow.accounts.get_system_account(AccountKind.VIS_REDEEMED)
        row.status = VoucherStatus.REDEEMED
        row.redeemed_by = barista.id
        row.redeemed_at = now
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


async def redeem_code(container: Container, user: User, value_micro: int) -> None:
    """The credit of a redeemed code as `CodeService.redeem` books it: `CODE_REDEMPTION`
    from `vis:issuance` to the user. The entry refers to its code by id only."""
    code_id = uuid7()
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        await container.ledger.post(
            uow,
            kind=JournalKind.CODE_REDEMPTION,
            ref_type=LedgerRef.CODE,
            ref_id=code_id,
            idempotency_key=code_redemption_key(code_id),
            lines=[PostingLine(issuance.id, -value_micro), PostingLine(account.id, value_micro)],
            at=container.clock.now(),
        )


async def adjust_balance(container: Container, user: User, by: User, amount_micro: int) -> None:
    """An admin's adjustment as `AdminUserService.adjust_balance` books it:
    `ADMIN_ADJUSTMENT` between `vis:issuance` and the user, to the user for an amount
    above zero and back to `vis:issuance` for one below."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
        await container.ledger.post(
            uow,
            kind=JournalKind.ADMIN_ADJUSTMENT,
            ref_type=LedgerRef.USER,
            ref_id=user.id,
            idempotency_key=admin_adjustment_key(by.id, uuid7()),
            lines=[PostingLine(issuance.id, -amount_micro), PostingLine(account.id, amount_micro)],
            at=container.clock.now(),
        )


async def book_owed_months(container: Container, user: User) -> int:
    """The monthly grants `user` is owed, booked the way upkeep books them."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_user_account(user.id, for_update=True)
        assert account is not None
        return await container.grants.grant_owed_months(uow, user, account)


# --- Reading what is there ---------------------------------------------------------------


async def market_now(container: Container, market: Market) -> Market:
    """The market's row as it is, after whoever traded or settled it."""
    async with container.uow_factory() as uow:
        row = await uow.markets.get(market.id)
    assert row is not None
    return row


async def positions_in(container: Container, user: User, market: Market) -> list[Position]:
    async with container.uow_factory() as uow:
        return list(await uow.positions.list_for_user_market(user.id, market.id))


async def resolution_of(container: Container, market: Market) -> Resolution | None:
    async with container.uow_factory() as uow:
        return await uow.resolutions.get_by_market(market.id)


async def trades_of(container: Container, user: User) -> list[Trade]:
    """Every trade of a user, newest first."""
    async with container.uow_factory() as uow:
        return list(await uow.trades.list_for_user(user.id, limit=1000))


async def as_market_view_shows_it(
    container: Container, user: User, market: Market
) -> PortfolioEntry:
    """The entry of `market` in the portfolio of `user`, built from the rows as they
    are now and from nothing but `MarketView`."""
    view = container.market_view
    row = await market_now(container, market)
    positions = await positions_in(container, user, market)
    resolution = await resolution_of(container, market)
    holdings = view.holdings(row, positions, resolution)
    payout_micro = view.payout_micro(positions, resolution)
    refund_micro = view.refund_micro(positions, resolution)
    trades = await trades_of(container, user)
    return PortfolioEntry(
        market=view.summary(row, container.clock.now(), resolution),
        holdings=holdings,
        entered_at=min(trade.created_at for trade in trades if trade.market_id == market.id),
        value_cr=sum((holding.value_cr for holding in holdings), Decimal(0)),
        cost_cr=to_cr(sum(position.cost_micro for position in positions)),
        proceeds_cr=to_cr(sum(position.proceeds_micro for position in positions)),
        net_stake_cr=to_cr(view.net_stake_micro(positions)),
        payout_cr=None if payout_micro is None else to_cr(payout_micro),
        refund_cr=None if refund_micro is None else to_cr(refund_micro),
    )


@contextmanager
def recorded_selects(container: Container) -> Iterator[list[str]]:
    """The SELECT statements the application sends to Postgres inside the block."""
    selects: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    engine = container.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield selects
    finally:
        event.remove(engine, "before_cursor_execute", record)


def refused_cursor(error: pytest.ExceptionInfo[ValidationFailedError]) -> None:
    """The error is the 422 that names the cursor."""
    assert (error.value.status_code, error.value.code) == (422, ErrorCode.VALIDATION_ERROR)
    assert error.value.details == {"field": "cursor"}


# --- /me ---------------------------------------------------------------------------------


async def test_me_reports_the_profile_the_balance_and_the_next_monthly_grant(
    container: Container, factory: Factory, settings: Settings, clock: FrozenClock
) -> None:
    user = await factory.user(
        balance_micro=12_500_000,
        email="neo@ethz.ch",
        username="neo",
        name="Thomas Anderson",
        degree_level=DegreeLevel.OTHER,
        degree_programme="ETH staff",
    )
    clock.advance(timedelta(days=2))

    me = await container.account.me(factory.identity(user))

    assert (me.id, me.email, me.name, me.username) == (
        user.id,
        "neo@ethz.ch",
        "Thomas Anderson",
        "neo",
    )
    assert (me.degree_level, me.degree_programme) == (DegreeLevel.OTHER, "ETH staff")
    assert (me.role, me.created_at) == (UserRole.USER, TEST_NOW)
    assert me.policy_accepted_at == TEST_NOW
    assert me.balance_cr == to_cr(12_500_000)
    assert me.monthly_grant_cr == to_cr(settings.grants.monthly_grant_micro)
    assert me.next_monthly_grant_at == container.grants.next_grant_at(user) == FIRST_GRANT_DUE
    assert me.notices == []
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    ("balance_micro", "can_get_voucher"),
    [(0, False), (CHEAPEST - 1, False), (CHEAPEST, True), (CHEAPEST + 1, True)],
    ids=["nothing", "one-micro-short", "exactly-the-cheapest", "more"],
)
async def test_can_get_voucher_says_whether_the_balance_covers_the_cheapest_product(
    container: Container,
    factory: Factory,
    settings: Settings,
    balance_micro: int,
    can_get_voucher: bool,
) -> None:
    prices = [listed.price_micro for listed in settings.vouchers.products]
    assert min(prices) == CHEAPEST
    assert max(prices) > CHEAPEST, "the cheapest product is not simply every product"
    user = await factory.user(balance_micro=balance_micro)

    me = await container.account.me(factory.identity(user))

    assert (me.balance_cr, me.can_get_voucher) == (to_cr(balance_micro), can_get_voucher)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("role", list(UserRole))
async def test_me_reports_the_role_of_the_request(
    container: Container, factory: Factory, role: UserRole
) -> None:
    """Whatever the request resolved to is what `/me` says: the role of the row for
    most, ADMIN for a bootstrap admin. The service does not work it out again."""
    user = await factory.user()

    assert (await container.account.me(identity_of(user, role))).role is role


async def test_me_reports_the_role_an_admin_gave_and_admin_for_a_bootstrap_admin(
    container: Container, factory: Factory
) -> None:
    student, barista = await factory.user(), await factory.barista()
    made_admin = await factory.user(role=UserRole.ADMIN)
    bootstrap = await factory.admin()

    roles = [
        (await container.account.me(factory.identity(user))).role
        for user in (student, barista, made_admin, bootstrap)
    ]

    assert roles == [UserRole.USER, UserRole.BARISTA, UserRole.ADMIN, UserRole.ADMIN]
    assert bootstrap.role is UserRole.USER, "its row says USER: the address makes the admin"


async def test_me_follows_the_balance_and_the_calendar(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    user = await factory.user(balance_micro=CHEAPEST)
    await redeem_code(container, user, 5 * MICRO)
    clock.set(TEST_NOW + timedelta(days=40))

    me = await container.account.me(factory.identity(user))

    assert me.balance_cr == to_cr(CHEAPEST + 5 * MICRO)
    assert me.next_monthly_grant_at == SECOND_GRANT_DUE
    await assert_books_balance(container.uow_factory)


# --- The portfolio -----------------------------------------------------------------------


async def test_a_user_without_positions_has_an_empty_portfolio(
    container: Container, factory: Factory
) -> None:
    user = await factory.user(balance_micro=STARTING)
    await factory.market()

    portfolio = await container.account.portfolio(factory.identity(user))

    assert (portfolio.items, portfolio.total_value_cr) == ([], to_cr(0))
    assert portfolio.balance_cr == portfolio.total_equity_cr == to_cr(STARTING)
    assert portfolio.equity_history[-1].total_equity_cr == portfolio.total_equity_cr
    await assert_books_balance(container.uow_factory)


async def test_equity_history_includes_other_traders_sales_settlement_and_voucher_spending(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    admin = await factory.admin()
    market = await factory.market()
    snapshots = [(clock.now(), to_cr(STARTING))]

    async def remember() -> None:
        portfolio = await container.account.portfolio(factory.identity(alice))
        assert portfolio.total_equity_cr == portfolio.balance_cr + portfolio.total_value_cr
        snapshots.append((clock.now(), portfolio.total_equity_cr))

    clock.advance(timedelta(hours=1))
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    await remember()
    clock.advance(timedelta(hours=1))
    await factory.position(bob, market, Outcome.NO, shares_micro=80 * MICRO)
    await remember()
    assert snapshots[-1][1] < snapshots[-2][1]
    clock.advance(timedelta(hours=1))
    await sell(container, alice, market, Outcome.YES, 20 * MICRO)
    await remember()
    clock.advance(timedelta(hours=1))
    await settle(container, market, ResolutionOutcome.YES, admin)
    await remember()
    clock.advance(timedelta(hours=1))
    await issue_voucher(container, alice, product("Mate"))
    await remember()
    clock.advance(timedelta(hours=1))

    portfolio = await container.account.portfolio(factory.identity(alice))
    history = {point.t: point.total_equity_cr for point in portfolio.equity_history}
    for at, total in snapshots:
        assert history[at] == total
    assert portfolio.total_value_cr == 0
    assert portfolio.total_equity_cr == to_cr(await factory.balance_micro(alice))
    assert history[clock.now()] == portfolio.total_equity_cr
    await assert_books_balance(container.uow_factory)


async def test_equity_does_not_mix_an_old_balance_with_new_positions(
    container: Container, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    traded = False

    def interleaved_read() -> UnitOfWork:
        uow = container.uow_factory()
        list_positions = uow.positions.list_for_user

        async def positions_after_trade(user_id: UUID) -> Sequence[Position]:
            nonlocal traded
            if not traded:
                # Commit on another connection after the reader has seen the balance,
                # but before it asks for positions and historical trade events.
                await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
                traded = True
            return await list_positions(user_id)

        monkeypatch.setattr(uow.positions, "list_for_user", positions_after_trade)
        return uow

    monkeypatch.setattr(container.account, "_uow_factory", interleaved_read)
    before = await container.account.portfolio(factory.identity(alice))
    assert before.items == []
    assert before.total_equity_cr == before.balance_cr == to_cr(STARTING)
    assert before.equity_history[-1].total_equity_cr == to_cr(STARTING)

    after = await container.account.portfolio(factory.identity(alice))
    assert len(after.items) == 1
    assert after.balance_cr < before.balance_cr
    assert after.total_value_cr > 0
    assert after.total_equity_cr == after.balance_cr + after.total_value_cr
    await assert_books_balance(container.uow_factory)


async def test_a_holding_is_worth_its_shares_at_the_price_market_view_reports(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    paid_micro = STARTING - await factory.balance_micro(alice)
    view = container.market_view

    (before,) = (await container.account.portfolio(factory.identity(alice))).items
    await factory.position(bob, market, Outcome.NO, shares_micro=90 * MICRO)
    portfolio = await container.account.portfolio(factory.identity(alice))

    (entry,) = portfolio.items
    assert entry == await as_market_view_shows_it(container, alice, market)
    row = await market_now(container, market)
    yes, no = entry.holdings
    assert (yes.outcome, yes.shares) == (Outcome.YES, to_cr(40 * MICRO))
    assert yes.value_cr == to_cr(view.holding_value_micro(row, Outcome.YES, 40 * MICRO, None))
    assert (no.outcome, no.shares, no.value_cr) == (Outcome.NO, to_cr(0), to_cr(0))
    assert entry.value_cr == yes.value_cr == portfolio.total_value_cr
    assert (entry.cost_cr, entry.proceeds_cr) == (to_cr(paid_micro), to_cr(0))
    assert entry.net_stake_cr == to_cr(paid_micro)
    assert (entry.payout_cr, entry.refund_cr) == (None, None)
    assert (entry.market.id, entry.market.status) == (market.id, MarketStatus.OPEN)
    assert entry.market.version == 2
    # Bob bought NO, so YES is cheaper than when Alice looked first.
    assert entry.market.p_yes < before.market.p_yes
    assert entry.value_cr < before.value_cr
    assert before.cost_cr == entry.cost_cr
    await assert_books_balance(container.uow_factory)


async def test_holdings_of_both_outcomes_are_one_entry_of_the_market(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=30 * MICRO)
    await factory.position(alice, market, Outcome.NO, shares_micro=12 * MICRO)
    paid_micro = STARTING - await factory.balance_micro(alice)

    portfolio = await container.account.portfolio(factory.identity(alice))

    (entry,) = portfolio.items
    assert entry == await as_market_view_shows_it(container, alice, market)
    assert [(holding.outcome, holding.shares) for holding in entry.holdings] == [
        (Outcome.YES, to_cr(30 * MICRO)),
        (Outcome.NO, to_cr(12 * MICRO)),
    ]
    assert entry.value_cr == entry.holdings[0].value_cr + entry.holdings[1].value_cr
    assert entry.cost_cr == entry.net_stake_cr == to_cr(paid_micro)
    assert portfolio.total_value_cr == entry.value_cr
    await assert_books_balance(container.uow_factory)


async def test_a_market_whose_shares_were_all_sold_is_still_listed(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    paid_micro = STARTING - await factory.balance_micro(alice)
    await factory.position(bob, market, Outcome.YES, shares_micro=60 * MICRO)
    sold = await sell(container, alice, market, Outcome.YES, 40 * MICRO)
    assert sold.amount_micro > paid_micro, "Bob's buy made her shares worth more"

    portfolio = await container.account.portfolio(factory.identity(alice))

    (entry,) = portfolio.items
    assert entry == await as_market_view_shows_it(container, alice, market)
    assert [(holding.shares, holding.value_cr) for holding in entry.holdings] == [
        (to_cr(0), to_cr(0)),
        (to_cr(0), to_cr(0)),
    ]
    assert (entry.cost_cr, entry.proceeds_cr) == (to_cr(paid_micro), to_cr(sold.amount_micro))
    assert entry.net_stake_cr == to_cr(0), "never below zero"
    assert (entry.value_cr, portfolio.total_value_cr) == (to_cr(0), to_cr(0))
    await assert_books_balance(container.uow_factory)


async def test_a_sale_of_a_part_lowers_the_net_stake_by_what_it_brought_in(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    sold = await sell(container, alice, market, Outcome.YES, 15 * MICRO)
    positions = await positions_in(container, alice, market)

    (entry,) = (await container.account.portfolio(factory.identity(alice))).items

    assert entry == await as_market_view_shows_it(container, alice, market)
    assert entry.holdings[0].shares == to_cr(25 * MICRO)
    assert entry.proceeds_cr == to_cr(sold.amount_micro)
    assert entry.net_stake_cr == to_cr(container.market_view.net_stake_micro(positions))
    assert entry.net_stake_cr == entry.cost_cr - entry.proceeds_cr
    await assert_books_balance(container.uow_factory)


async def test_a_resolved_market_reports_what_it_paid(
    container: Container, factory: Factory
) -> None:
    """1 CR for every share of the winning outcome, and nothing for the other."""
    admin = await factory.admin()
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    await factory.position(alice, market, Outcome.NO, shares_micro=5 * MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=25 * MICRO)
    before_micro = await factory.balance_micro(alice)

    resolution = await settle(container, market, ResolutionOutcome.YES, admin)
    winner = await container.account.portfolio(factory.identity(alice))
    loser = await container.account.portfolio(factory.identity(bob))

    view = container.market_view
    (won,) = winner.items
    assert won == await as_market_view_shows_it(container, alice, market)
    payout_micro = view.payout_micro(await positions_in(container, alice, market), resolution)
    assert payout_micro is not None
    assert won.payout_cr == to_cr(payout_micro) == to_cr(40 * MICRO)
    assert won.refund_cr is None
    assert (won.market.status, won.market.outcome) == (
        MarketStatus.RESOLVED,
        ResolutionOutcome.YES,
    )
    assert won.market.trading_open is False
    # The shares are still there to read, and now they are worth what they paid.
    assert [(holding.shares, holding.value_cr) for holding in won.holdings] == [
        (to_cr(40 * MICRO), to_cr(40 * MICRO)),
        (to_cr(5 * MICRO), to_cr(0)),
    ]
    assert won.value_cr == won.payout_cr
    assert await factory.balance_micro(alice) == before_micro + 40 * MICRO

    (lost,) = loser.items
    assert lost == await as_market_view_shows_it(container, bob, market)
    assert (lost.payout_cr, lost.refund_cr, lost.value_cr) == (to_cr(0), None, to_cr(0))
    assert lost.holdings[1].shares == to_cr(25 * MICRO)
    # What a portfolio is worth is what is not settled yet: nothing, for both of them.
    assert (winner.total_value_cr, loser.total_value_cr) == (to_cr(0), to_cr(0))
    await assert_books_balance(container.uow_factory)


async def test_a_void_market_reports_what_it_gave_back(
    container: Container, factory: Factory
) -> None:
    """The net stake: what the user paid minus what they got for selling."""
    admin = await factory.admin()
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=40 * MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=25 * MICRO)
    await sell(container, alice, market, Outcome.YES, 10 * MICRO)
    stake_micro = STARTING - await factory.balance_micro(alice)

    resolution = await settle(container, market, ResolutionOutcome.VOID, admin)
    portfolio = await container.account.portfolio(factory.identity(alice))

    view = container.market_view
    (entry,) = portfolio.items
    assert entry == await as_market_view_shows_it(container, alice, market)
    refund_micro = view.refund_micro(await positions_in(container, alice, market), resolution)
    assert refund_micro is not None
    assert entry.refund_cr == to_cr(refund_micro) == to_cr(stake_micro)
    assert entry.net_stake_cr == entry.refund_cr
    assert entry.payout_cr is None
    assert (entry.market.status, entry.market.outcome) == (
        MarketStatus.VOID,
        ResolutionOutcome.VOID,
    )
    assert [(holding.shares, holding.value_cr) for holding in entry.holdings] == [
        (to_cr(0), to_cr(0)),
        (to_cr(0), to_cr(0)),
    ]
    assert (entry.value_cr, portfolio.total_value_cr) == (to_cr(0), to_cr(0))
    # Nobody won or lost: Alice has again what she started with.
    assert await factory.balance_micro(alice) == STARTING
    await assert_books_balance(container.uow_factory)


async def test_unsettled_markets_come_first_soonest_close_first_then_settled_ones_latest_first(
    container: Container, factory: Factory
) -> None:
    admin = await factory.admin()
    alice = await factory.user(balance_micro=STARTING)
    closes_in = {
        "open, in 5 days": 5,
        "resolved, closing in 3 days": 3,
        "open, in 2 days": 2,
        "void, closing in 6 days": 6,
        "past its deadline": -1,
        "halted, in 4 days": 4,
        "resolved, closing in 9 days": 9,
    }
    markets: dict[str, Market] = {}
    for question, days in closes_in.items():
        markets[question] = await factory.market(
            close_in=timedelta(days=days), question=f"Will it be {question}?"
        )
        await factory.position(alice, markets[question], Outcome.YES, shares_micro=3 * MICRO)
    async with container.uow_factory() as uow:
        halted = await uow.markets.get_for_update(markets["halted, in 4 days"].id)
        assert halted is not None
        halted.status = MarketStatus.HALTED
    for question, outcome in (
        ("resolved, closing in 3 days", ResolutionOutcome.NO),
        ("void, closing in 6 days", ResolutionOutcome.VOID),
        ("resolved, closing in 9 days", ResolutionOutcome.YES),
    ):
        await settle(container, markets[question], outcome, admin)

    portfolio = await container.account.portfolio(factory.identity(alice))

    in_order = [
        "past its deadline",
        "open, in 2 days",
        "halted, in 4 days",
        "open, in 5 days",
        "resolved, closing in 9 days",
        "void, closing in 6 days",
        "resolved, closing in 3 days",
    ]
    assert [entry.market.id for entry in portfolio.items] == [
        markets[question].id for question in in_order
    ]
    assert [entry.market.status for entry in portfolio.items] == [
        MarketStatus.CLOSED,
        MarketStatus.OPEN,
        MarketStatus.HALTED,
        MarketStatus.OPEN,
        MarketStatus.RESOLVED,
        MarketStatus.VOID,
        MarketStatus.RESOLVED,
    ]
    for entry, question in zip(portfolio.items, in_order, strict=True):
        assert entry == await as_market_view_shows_it(container, alice, markets[question])
    # What the portfolio is worth is what is not settled: the first four.
    unsettled = portfolio.items[:4]
    assert portfolio.total_value_cr == sum((entry.value_cr for entry in unsettled), Decimal(0))
    assert portfolio.total_value_cr > 0
    assert [entry.payout_cr for entry in portfolio.items[4:]] == [to_cr(3 * MICRO), None, to_cr(0)]
    await assert_books_balance(container.uow_factory)


async def test_markets_that_close_at_the_same_instant_are_ordered_by_their_id(
    container: Container, factory: Factory
) -> None:
    admin = await factory.admin()
    alice = await factory.user(balance_micro=STARTING)
    together = [await factory.market() for _ in range(6)]
    assert len({market.close_at for market in together}) == 1
    for market in reversed(together):
        await factory.position(alice, market, Outcome.NO, shares_micro=MICRO)
    for market in together[1::2]:
        await settle(container, market, ResolutionOutcome.NO, admin)
    by_id = sorted(market.id for market in together)
    settled = {market.id for market in together[1::2]}

    portfolio = await container.account.portfolio(factory.identity(alice))

    assert [entry.market.id for entry in portfolio.items] == [
        *(market_id for market_id in by_id if market_id not in settled),
        *(market_id for market_id in by_id if market_id in settled),
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_position_is_entered_at_the_users_first_trade_in_its_market(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Buying more, the other outcome or selling everything leaves the instant where
    it is, and somebody else's trade in the market is not the user's."""
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    lunch = await factory.market(question="Will the mensa serve rösti on Monday?")
    exam = await factory.market(question="Will the analysis exam be moved?")
    await factory.position(bob, lunch, Outcome.NO, shares_micro=70 * MICRO)
    clock.advance(timedelta(minutes=5))
    entered_lunch = clock.now()
    await factory.position(alice, lunch, Outcome.YES, shares_micro=40 * MICRO)
    clock.advance(timedelta(minutes=5))
    entered_exam = clock.now()
    await factory.position(alice, exam, Outcome.NO, shares_micro=8 * MICRO)
    clock.advance(timedelta(minutes=5))
    await factory.position(alice, lunch, Outcome.NO, shares_micro=5 * MICRO)
    await sell(container, alice, lunch, Outcome.YES, 40 * MICRO)
    await sell(container, alice, exam, Outcome.NO, 8 * MICRO)

    portfolio = await container.account.portfolio(factory.identity(alice))

    assert {entry.market.id: entry.entered_at for entry in portfolio.items} == {
        lunch.id: entered_lunch,
        exam.id: entered_exam,
    }
    await assert_books_balance(container.uow_factory)


async def test_the_portfolio_uses_a_fixed_number_of_queries_for_one_market_as_for_many(
    container: Container, factory: Factory
) -> None:
    """Profile, account, positions, markets, resolutions, first trades and equity
    events: one query each, whatever the number of markets."""
    admin = await factory.admin()
    one = await factory.user(balance_micro=STARTING)
    many = await factory.user(balance_micro=STARTING)
    markets = [await factory.market() for _ in range(5)]
    await factory.position(one, markets[0], Outcome.YES, shares_micro=MICRO)
    for market in markets:
        await factory.position(many, market, Outcome.YES, shares_micro=MICRO)
    await settle(container, markets[3], ResolutionOutcome.YES, admin)
    await settle(container, markets[4], ResolutionOutcome.VOID, admin)

    with recorded_selects(container) as for_one:
        assert len((await container.account.portfolio(factory.identity(one))).items) == 1
    with recorded_selects(container) as for_many:
        assert len((await container.account.portfolio(factory.identity(many))).items) == 5

    assert len(for_one) == len(for_many) == 7
    await assert_books_balance(container.uow_factory)


# --- The trades --------------------------------------------------------------------------


async def test_trades_are_listed_newest_first_with_their_market_and_the_prices_market_view_shows(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    lunch = await factory.market(question="Will the mensa serve rösti on Monday?")
    exam = await factory.market(question="Will the analysis exam be moved?", tier=MarketTier.TIGHT)
    await factory.position(alice, lunch, Outcome.YES, shares_micro=40 * MICRO)
    clock.advance(timedelta(minutes=5))
    await factory.position(bob, lunch, Outcome.NO, shares_micro=70 * MICRO)
    await factory.position(alice, exam, Outcome.NO, shares_micro=8 * MICRO)
    clock.advance(timedelta(minutes=5))
    await sell(container, alice, lunch, Outcome.YES, 15 * MICRO)
    rows = await trades_of(container, alice)
    view = container.market_view

    page = await container.account.trades(factory.identity(alice), PageQuery())

    assert page.next_cursor is None
    assert [item.id for item in page.items] == [row.id for row in rows]
    assert [(item.side, item.outcome, item.market_id) for item in page.items] == [
        (TradeSide.SELL, Outcome.YES, lunch.id),
        (TradeSide.BUY, Outcome.NO, exam.id),
        (TradeSide.BUY, Outcome.YES, lunch.id),
    ]
    assert [item.market_question for item in page.items] == [
        "Will the mensa serve rösti on Monday?",
        "Will the analysis exam be moved?",
        "Will the mensa serve rösti on Monday?",
    ]
    assert page.items == [
        TradeItem(
            id=row.id,
            market_id=row.market_id,
            market_question=(lunch if row.market_id == lunch.id else exam).question,
            side=row.side,
            outcome=row.outcome,
            shares=to_cr(row.shares_micro),
            amount_cr=to_cr(row.amount_micro),
            average_price=view.average_price(row.amount_micro, row.shares_micro),
            p_yes_before=view.probabilities(row.p_yes_before_micro)[0],
            p_yes_after=view.probabilities(row.p_yes_after_micro)[0],
            market_version=row.market_version,
            created_at=row.created_at,
        )
        for row in rows
    ]
    sold, _, bought = page.items
    assert (sold.shares, bought.shares) == (to_cr(15 * MICRO), to_cr(40 * MICRO))
    assert (sold.market_version, bought.market_version) == (3, 1)
    assert (sold.created_at, bought.created_at) == (TEST_NOW + timedelta(minutes=10), TEST_NOW)
    # A market opens at even odds; her buy of YES moved it up, her sale back down.
    assert bought.p_yes_before == view.probabilities(MICRO // 2)[0]
    assert bought.p_yes_after > bought.p_yes_before
    assert sold.p_yes_after < sold.p_yes_before
    assert 0 < bought.average_price < 1
    await assert_books_balance(container.uow_factory)


async def test_trades_page_by_limit_and_cursor_without_a_gap_or_a_repeat(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    for _ in range(5):
        await factory.position(alice, market, Outcome.YES, shares_micro=2 * MICRO)
    newest_first = [row.id for row in await trades_of(container, alice)]
    assert len(newest_first) == 5
    caller = factory.identity(alice)

    first = await container.account.trades(caller, PageQuery(limit=2))
    second = await container.account.trades(caller, PageQuery(limit=2, cursor=first.next_cursor))
    third = await container.account.trades(caller, PageQuery(limit=2, cursor=second.next_cursor))

    assert [item.id for item in first.items] == newest_first[:2]
    assert first.next_cursor == str(newest_first[1])
    assert [item.id for item in second.items] == newest_first[2:4]
    assert second.next_cursor == str(newest_first[3])
    assert [item.id for item in third.items] == newest_first[4:]
    assert third.next_cursor is None
    # A page that ends exactly where the list ends has nothing after it.
    exact = await container.account.trades(caller, PageQuery(limit=5))
    assert (len(exact.items), exact.next_cursor) == (5, None)
    beyond = await container.account.trades(caller, PageQuery(cursor=str(newest_first[-1])))
    assert (beyond.items, beyond.next_cursor) == ([], None)
    await assert_books_balance(container.uow_factory)


async def test_a_user_sees_their_own_trades_only(container: Container, factory: Factory) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=MICRO)
    (hers,) = await trades_of(container, alice)

    page = await container.account.trades(factory.identity(alice), PageQuery())

    assert [item.id for item in page.items] == [hers.id]
    # A cursor is a place in one's own list: somebody else's trade id shows none of theirs.
    (his,) = await trades_of(container, bob)
    assert his.id > hers.id
    after_his = await container.account.trades(
        factory.identity(alice), PageQuery(cursor=str(his.id))
    )
    assert [item.id for item in after_his.items] == [hers.id]
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "cursor",
    [
        "nope",
        "12",
        "0199c1d4",
        "0199c1d4-7b5e-7c3a-9f41-2d6a8e5b4c1",
        "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
    ],
    ids=["a-word", "a-posting-id", "the-start-of-a-uuid", "a-digit-short", "not-hex"],
)
async def test_a_trade_cursor_that_is_no_uuid_is_422_on_the_cursor(
    container: Container, factory: Factory, cursor: str
) -> None:
    user = await factory.user()

    with pytest.raises(ValidationFailedError) as refused:
        await container.account.trades(factory.identity(user), PageQuery(cursor=cursor))

    refused_cursor(refused)


# --- The ledger --------------------------------------------------------------------------


def line(item: LedgerItem) -> tuple[JournalKind, Decimal, str]:
    return item.kind, item.amount_cr, item.description


async def test_the_ledger_lists_every_movement_on_the_balance_newest_first_under_its_label(
    container: Container, factory: Factory, settings: Settings, clock: FrozenClock
) -> None:
    """One of each of the ten ways a user's balance can move, and an eleventh entry
    that is none of the user's business."""
    admin, barista = await factory.admin(), await factory.barista()
    alice = await factory.user(balance_micro=STARTING)
    resolved, voided = await factory.market(), await factory.market()
    moments = [TEST_NOW]

    def later() -> None:
        moments.append(clock.advance(timedelta(hours=1)))

    await factory.position(alice, resolved, Outcome.YES, shares_micro=40 * MICRO)
    bought_micro = STARTING - await factory.balance_micro(alice)
    later()
    sold = await sell(container, alice, resolved, Outcome.YES, 10 * MICRO)
    later()
    await settle(container, resolved, ResolutionOutcome.YES, admin)
    later()
    await factory.position(alice, voided, Outcome.NO, shares_micro=20 * MICRO)
    staked_micro = (await positions_in(container, alice, voided))[0].cost_micro
    later()
    await settle(container, voided, ResolutionOutcome.VOID, admin)
    later()
    await redeem_code(container, alice, 3 * MICRO)
    later()
    await adjust_balance(container, alice, admin, -2 * MICRO)
    later()
    mate = await issue_voucher(container, alice, product("Mate"))
    twix = await issue_voucher(container, alice, product("Twix"))
    later()
    await redeem_voucher(container, twix, barista)
    # A month on, as upkeep finds her: a grant has fallen due and the Mate was never
    # fetched.
    clock.set(mate.expires_at + timedelta(days=1))
    moments.append(clock.now())
    assert FIRST_GRANT_DUE <= clock.now() < SECOND_GRANT_DUE
    assert await book_owed_months(container, alice) == 1
    await refund_voucher(container, mate)
    monthly_micro = settings.grants.monthly_grant_micro
    mate_micro, twix_micro = product("Mate").price_micro, product("Twix").price_micro

    page = await container.account.ledger(factory.identity(alice), PageQuery())

    assert page.next_cursor is None
    assert [line(item) for item in page.items] == [
        (JournalKind.VOUCHER_REFUND, to_cr(mate_micro), "Voucher expired, refunded: Mate"),
        (JournalKind.MONTHLY_GRANT, to_cr(monthly_micro), "Monthly tokens"),
        (JournalKind.VOUCHER_ISSUE, to_cr(-twix_micro), "Voucher: Twix"),
        (JournalKind.VOUCHER_ISSUE, to_cr(-mate_micro), "Voucher: Mate"),
        (JournalKind.ADMIN_ADJUSTMENT, to_cr(-2 * MICRO), "Adjustment by VIS"),
        (JournalKind.CODE_REDEMPTION, to_cr(3 * MICRO), "Code redeemed"),
        (JournalKind.MARKET_VOID, to_cr(staked_micro), "Market voided, stake refunded"),
        (JournalKind.TRADE_BUY, to_cr(-staked_micro), "Bought shares"),
        (JournalKind.MARKET_RESOLUTION, to_cr(30 * MICRO), "Market payout"),
        (JournalKind.TRADE_SELL, to_cr(sold.amount_micro), "Sold shares"),
        (JournalKind.TRADE_BUY, to_cr(-bought_micro), "Bought shares"),
        (JournalKind.SIGNUP_GRANT, to_cr(STARTING), "Welcome tokens"),
    ]
    # Every way a balance can move is among them, except the three no step above books:
    # a scan at the bar, and the tokens and the bonus of a streak.
    assert {item.kind for item in page.items} == set(LEDGER_DESCRIPTIONS) - {
        JournalKind.VOUCHER_REDEEM,
        JournalKind.STREAK_REWARD,
        JournalKind.STREAK_BONUS,
    }
    assert [item.created_at for item in page.items] == [
        moments[9],
        moments[9],
        moments[7],
        moments[7],
        moments[6],
        moments[5],
        moments[4],
        moments[3],
        moments[2],
        moments[1],
        moments[0],
        moments[0],
    ]
    ids = [item.id for item in page.items]
    assert ids == sorted(ids, reverse=True)
    # The lines are the whole story of the balance.
    balance_micro = await factory.balance_micro(alice)
    assert sum((item.amount_cr for item in page.items), Decimal(0)) == to_cr(balance_micro)
    assert (await container.account.me(factory.identity(alice))).balance_cr == to_cr(balance_micro)
    await assert_books_balance(container.uow_factory)


async def test_a_line_about_a_voucher_names_the_product_the_voucher_was_issued_for(
    container: Container, factory: Factory, settings: Settings
) -> None:
    """What the row kept, whatever VOUCHER_PRODUCTS lists today."""
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    off_the_list = VoucherProduct(id="kurbissuppe", name="Kürbissuppe", price_micro=7 * MICRO)
    assert settings.vouchers.product(off_the_list.id) is None
    iced_tea, sweets = product("Iced Tea"), product("M&Ms")
    soup = await issue_voucher(container, alice, off_the_list)
    await issue_voucher(container, alice, iced_tea)
    await issue_voucher(container, bob, sweets)
    await refund_voucher(container, soup)

    hers = await container.account.ledger(factory.identity(alice), PageQuery())
    his = await container.account.ledger(factory.identity(bob), PageQuery())

    assert [line(item) for item in hers.items] == [
        (JournalKind.VOUCHER_REFUND, to_cr(7 * MICRO), "Voucher expired, refunded: Kürbissuppe"),
        (JournalKind.VOUCHER_ISSUE, to_cr(-iced_tea.price_micro), "Voucher: Iced Tea"),
        (JournalKind.VOUCHER_ISSUE, to_cr(-7 * MICRO), "Voucher: Kürbissuppe"),
        (JournalKind.SIGNUP_GRANT, to_cr(STARTING), "Welcome tokens"),
    ]
    assert [line(item) for item in his.items] == [
        (JournalKind.VOUCHER_ISSUE, to_cr(-sweets.price_micro), "Voucher: M&Ms"),
        (JournalKind.SIGNUP_GRANT, to_cr(STARTING), "Welcome tokens"),
    ]
    await assert_books_balance(container.uow_factory)


async def test_the_ledger_reads_the_products_of_a_page_in_one_query_however_many_vouchers(
    container: Container, factory: Factory
) -> None:
    """The account, the postings and the product names: three queries with vouchers on
    the page, two without."""
    plain = await factory.user(balance_micro=STARTING)
    one = await factory.user(balance_micro=STARTING)
    many = await factory.user(balance_micro=STARTING)
    await issue_voucher(container, one, product("Tea"))
    for name in ("Coffee", "Mate", "Tea", "Twix", "Toblerone"):
        await issue_voucher(container, many, product(name))

    with recorded_selects(container) as without_vouchers:
        no_voucher = await container.account.ledger(factory.identity(plain), PageQuery())
    with recorded_selects(container) as for_one:
        one_voucher = await container.account.ledger(factory.identity(one), PageQuery())
    with recorded_selects(container) as for_many:
        page = await container.account.ledger(factory.identity(many), PageQuery())

    assert [len(ledger.items) for ledger in (no_voucher, one_voucher, page)] == [1, 2, 6]
    assert [item.description for item in page.items[:5]] == [
        "Voucher: Toblerone",
        "Voucher: Twix",
        "Voucher: Tea",
        "Voucher: Mate",
        "Voucher: Coffee",
    ]
    assert (len(without_vouchers), len(for_one), len(for_many)) == (2, 3, 3)
    await assert_books_balance(container.uow_factory)


async def test_the_ledger_pages_by_limit_and_cursor_without_a_gap_or_a_repeat(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    bob = await factory.user(balance_micro=STARTING)
    for value in range(1, 7):
        await redeem_code(container, alice, value * MICRO)
        await redeem_code(container, bob, 50 * MICRO)
    caller = factory.identity(alice)
    everything = await container.account.ledger(caller, PageQuery())
    assert [item.amount_cr for item in everything.items] == [
        *(to_cr(value * MICRO) for value in range(6, 0, -1)),
        to_cr(STARTING),
    ]

    pages = [await container.account.ledger(caller, PageQuery(limit=3))]
    while pages[-1].next_cursor is not None:
        cursor = pages[-1].next_cursor
        assert cursor == str(pages[-1].items[-1].id)
        pages.append(await container.account.ledger(caller, PageQuery(limit=3, cursor=cursor)))

    assert [len(page.items) for page in pages] == [3, 3, 1]
    assert [item for page in pages for item in page.items] == everything.items
    exact = await container.account.ledger(caller, PageQuery(limit=7))
    assert (len(exact.items), exact.next_cursor) == (7, None)
    # The largest id there can be is a cursor like any other: everything is before it.
    from_the_top = await container.account.ledger(caller, PageQuery(cursor=str(MAX_POSTING_ID)))
    assert from_the_top.items == everything.items
    oldest = everything.items[-1]
    beyond = await container.account.ledger(caller, PageQuery(cursor=str(oldest.id)))
    assert (beyond.items, beyond.next_cursor) == ([], None)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "cursor",
    [
        "nope",
        "0",
        "-3",
        "+3",
        "1.5",
        "1e3",
        "1_000",
        "0x10",
        "\u0663",
        "\uff13",
        str(MAX_POSTING_ID + 1),
        "9" * 64,
        str(uuid7()),
    ],
    ids=[
        "a-word",
        "zero",
        "negative",
        "a-plus-sign",
        "a-fraction",
        "an-exponent",
        "an-underscore",
        "hexadecimal",
        "an-arabic-digit",
        "a-fullwidth-digit",
        "one-above-a-bigint",
        "sixty-four-nines",
        "a-trade-id",
    ],
)
async def test_a_ledger_cursor_that_is_no_posting_id_is_422_on_the_cursor(
    container: Container, factory: Factory, cursor: str
) -> None:
    user = await factory.user()

    with pytest.raises(ValidationFailedError) as refused:
        await container.account.ledger(factory.identity(user), PageQuery(cursor=cursor))

    refused_cursor(refused)


# --- The proposals -----------------------------------------------------------------------


async def test_proposals_are_listed_newest_first_in_every_status_with_their_effective_status(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    alice = await factory.user()
    bob = await factory.user()
    proposed: list[Market] = []
    for status, close_in, tier in (
        (MarketStatus.PROPOSED, timedelta(days=7), MarketTier.DEFAULT),
        (MarketStatus.PROPOSED, timedelta(hours=1), MarketTier.DEFAULT),
        (MarketStatus.REJECTED, timedelta(days=7), MarketTier.DEFAULT),
        (MarketStatus.REJECTED, timedelta(hours=1), MarketTier.DEFAULT),
        (MarketStatus.OPEN, timedelta(days=7), MarketTier.MEDIUM),
        (MarketStatus.OPEN, timedelta(hours=1), MarketTier.TIGHT),
        (MarketStatus.HALTED, timedelta(days=7), MarketTier.DEFAULT),
        (MarketStatus.CLOSED, timedelta(days=7), MarketTier.DEFAULT),
        (MarketStatus.RESOLVED, timedelta(days=7), MarketTier.DEFAULT),
        (MarketStatus.VOID, timedelta(days=7), MarketTier.DEFAULT),
    ):
        proposed.append(
            await factory.market(
                status=status,
                close_in=close_in,
                tier=tier,
                created_by=alice,
                question=f"Will proposal {len(proposed)} be accepted?",
            )
        )
        clock.advance(timedelta(minutes=10))
    theirs = await factory.market(created_by=bob)
    await factory.market()
    # The three that close an hour after they were proposed are past their deadline now.
    clock.advance(timedelta(hours=1))
    view = container.market_view

    listed = await container.account.proposals(factory.identity(alice))

    newest_first = list(reversed(proposed))
    assert [item.id for item in listed.items] == [market.id for market in newest_first]
    assert [item.status for item in listed.items] == [
        MarketStatus.VOID,
        MarketStatus.RESOLVED,
        MarketStatus.CLOSED,
        MarketStatus.HALTED,
        MarketStatus.CLOSED,  # stored OPEN, and past its deadline
        MarketStatus.OPEN,
        MarketStatus.REJECTED,  # turned down in time: it stays rejected past its deadline
        MarketStatus.REJECTED,
        MarketStatus.EXPIRED,  # stored PROPOSED, and past its deadline
        MarketStatus.PROPOSED,
    ]
    for item, market in zip(listed.items, newest_first, strict=True):
        assert item.status is view.effective_status(market, clock.now())
        assert (item.question, item.yes_definition, item.no_definition) == (
            market.question,
            market.yes_def,
            market.no_def,
        )
        assert (item.resolution_criteria, item.resolution_source) == (
            market.resolution_criteria,
            market.resolution_source,
        )
        assert (item.close_at, item.resolve_by, item.created_at) == (
            market.close_at,
            market.resolve_by,
            market.created_at,
        )
        assert item.rejection_reason == market.rejection_reason
    assert [item.rejection_reason for item in listed.items[-4:]] == [
        "Rejected in a test.",
        "Rejected in a test.",
        None,
        None,
    ]
    # How a market ended, for the two that did; the factory resolves to YES.
    assert [item.outcome for item in listed.items] == [
        ResolutionOutcome.VOID,
        ResolutionOutcome.YES,
        *[None] * 8,
    ]
    # Whatever tier VIS opened a market with, its proposer is told nothing of it.
    assert {market.tier for market in proposed} == {None, *FIXED_TIERS}
    told = {field for item in listed.items for field in item.model_dump()}
    assert told.isdisjoint({"requested_tier", "tier", "b_cr", "max_loss_cr", "budget_cr"})
    his = await container.account.proposals(factory.identity(bob))
    assert [item.id for item in his.items] == [theirs.id]


@pytest.mark.parametrize(
    "outcome", [ResolutionOutcome.YES, ResolutionOutcome.NO], ids=lambda outcome: outcome.value
)
async def test_a_proposal_says_how_its_market_ended_and_nothing_before_that(
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
    outcome: ResolutionOutcome,
) -> None:
    """The outcome a market card carries, in the list of the person who proposed it:
    YES or NO once it is RESOLVED, VOID once it is VOID, null while it is neither."""
    alice = await factory.user()
    resolved = await factory.market(
        status=MarketStatus.RESOLVED, created_by=alice, outcome=outcome, question="Resolved?"
    )
    voided = await factory.market(status=MarketStatus.VOID, created_by=alice, question="Voided?")
    closed = await factory.market(status=MarketStatus.CLOSED, created_by=alice, question="Closed?")
    # A market of somebody else that ended the other way changes nothing here.
    await factory.market(status=MarketStatus.VOID, question="Somebody else's?")
    as_user(alice)

    listed = (await client.get("/api/me/proposals")).json()["items"]
    detail = (await client.get(f"/api/markets/{resolved.id}")).json()

    assert {item["id"]: item["outcome"] for item in listed} == {
        str(resolved.id): outcome.value,
        str(voided.id): "VOID",
        str(closed.id): None,
    }
    assert detail["outcome"] == outcome.value, "the list says what the market itself says"
    assert all("outcome" in item for item in listed)


async def test_my_proposals_show_a_proposal_as_expired_from_its_deadline_on(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """An expired proposal is out of review: from the instant of `close_at` itself the list
    says EXPIRED, with no outcome and no reason, and the row still says PROPOSED."""
    alice = await factory.user()
    proposal = await factory.market(
        status=MarketStatus.PROPOSED, created_by=alice, close_in=timedelta(hours=1)
    )
    waiting = await factory.market(status=MarketStatus.PROPOSED, created_by=alice)
    as_user(alice)

    async def statuses() -> dict[str, str]:
        listed = (await client.get("/api/me/proposals")).json()["items"]
        assert all(item["outcome"] is None for item in listed)
        assert all(item["rejection_reason"] is None for item in listed)
        return {item["id"]: item["status"] for item in listed}

    clock.set(proposal.close_at - timedelta(microseconds=1))
    assert await statuses() == {str(proposal.id): "PROPOSED", str(waiting.id): "PROPOSED"}
    clock.set(proposal.close_at)
    assert await statuses() == {str(proposal.id): "EXPIRED", str(waiting.id): "PROPOSED"}
    clock.set(proposal.close_at + timedelta(days=1))
    assert await statuses() == {str(proposal.id): "EXPIRED", str(waiting.id): "PROPOSED"}
    async with container.uow_factory() as uow:
        row = await uow.markets.get(proposal.id)
    assert row is not None
    assert row.status is MarketStatus.PROPOSED


async def test_the_outcomes_of_any_number_of_proposals_are_read_with_one_statement(
    container: Container, factory: Factory
) -> None:
    """One query for the markets and one for the resolutions of the settled ones,
    however many there are; none for somebody whose proposals are all unsettled."""
    alice = await factory.user()
    bob = await factory.user()
    for number in range(4):
        await factory.market(
            status=MarketStatus.RESOLVED, created_by=alice, question=f"Settled {number}?"
        )
    await factory.market(created_by=bob)

    with recorded_selects(container) as for_alice:
        settled = await container.account.proposals(factory.identity(alice))
    with recorded_selects(container) as for_bob:
        unsettled = await container.account.proposals(factory.identity(bob))

    assert [item.outcome for item in settled.items] == [ResolutionOutcome.YES] * 4
    assert [item.outcome for item in unsettled.items] == [None]
    assert len([statement for statement in for_alice if "FROM resolutions" in statement]) == 1
    assert len([statement for statement in for_alice if "FROM markets" in statement]) == 1
    assert [statement for statement in for_bob if "FROM resolutions" in statement] == []


async def test_somebody_who_proposed_nothing_has_no_proposals(
    container: Container, factory: Factory
) -> None:
    alice = await factory.user(balance_micro=STARTING)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)

    assert (await container.account.proposals(factory.identity(alice))).items == []
    await assert_books_balance(container.uow_factory)


# --- Through the routes ------------------------------------------------------------------


async def test_the_five_routes_answer_with_amounts_as_strings_and_prices_as_numbers(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    settings: Settings,
) -> None:
    admin = await factory.admin(balance_micro=STARTING)
    market = await factory.market(created_by=await factory.user())
    await factory.position(admin, market, Outcome.YES, shares_micro=40 * MICRO)
    await issue_voucher(container, admin, product("Mate"))
    balance_micro = await factory.balance_micro(admin)
    (trade,) = await trades_of(container, admin)
    as_user(admin)

    me = await client.get("/api/me")
    positions = await client.get("/api/me/positions")
    trades = await client.get("/api/me/trades", params={"limit": 1})
    ledger = await client.get("/api/me/ledger", params={"limit": 2})
    proposals = await client.get("/api/me/proposals")

    for response in (me, positions, trades, ledger, proposals):
        assert response.status_code == 200, response.text
    assert me.json() == {
        "id": str(admin.id),
        "email": admin.email,
        "name": admin.name,
        "username": admin.username,
        "degree_level": admin.degree_level.value,
        "degree_programme": admin.degree_programme,
        "role": "ADMIN",
        "created_at": TEST_NOW.isoformat().replace("+00:00", "Z"),
        "policy_accepted_at": TEST_NOW.isoformat().replace("+00:00", "Z"),
        "balance_cr": format_cr(balance_micro),
        "can_get_voucher": True,
        "monthly_grant_cr": format_cr(settings.grants.monthly_grant_micro),
        # 9 November at 14:00 in Zurich, sent as the instant it is.
        "next_monthly_grant_at": "2026-11-09T13:00:00Z",
        "notices": [],
    }
    assert FIRST_GRANT_DUE.astimezone(UTC).isoformat() == "2026-11-09T13:00:00+00:00"
    (entry,) = positions.json()["items"]
    assert entry["holdings"][0] == {
        "outcome": "YES",
        "shares": "40",
        "value_cr": positions.json()["total_value_cr"],
    }
    assert (entry["cost_cr"], entry["proceeds_cr"]) == (format_cr(trade.amount_micro), "0")
    assert (entry["payout_cr"], entry["refund_cr"]) == (None, None)
    assert isinstance(entry["market"]["p_yes"], float)
    (item,) = trades.json()["items"]
    assert (item["id"], item["shares"], item["amount_cr"]) == (
        str(trade.id),
        "40",
        format_cr(trade.amount_micro),
    )
    assert isinstance(item["average_price"], float)
    assert trades.json()["next_cursor"] is None
    assert [
        (row["kind"], row["amount_cr"], row["description"]) for row in ledger.json()["items"]
    ] == [
        ("VOUCHER_ISSUE", format_cr(-product("Mate").price_micro), "Voucher: Mate"),
        ("TRADE_BUY", format_cr(-trade.amount_micro), "Bought shares"),
    ]
    assert ledger.json()["next_cursor"] == str(ledger.json()["items"][-1]["id"])
    assert proposals.json() == {"items": []}
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("path", ["/api/me/trades", "/api/me/ledger"])
async def test_the_routes_answer_a_cursor_they_never_handed_out_with_422_on_the_cursor(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs, path: str
) -> None:
    as_user(await factory.user())

    response = await client.get(path, params={"cursor": "page-2"})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"] == {"field": "cursor"}


async def test_a_suspended_user_reads_nothing_about_themselves(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    """The guard in front of all five routes."""
    as_user(await factory.user(status=UserStatus.SUSPENDED))

    for path in ("", "/positions", "/trades", "/ledger", "/proposals"):
        response = await client.get(f"/api/me{path}")
        assert (response.status_code, response.json()["code"]) == (403, "FORBIDDEN")
