"""The test harness the other integration tests stand on (backend/CLAUDE.md, "Tests").

What `tests/conftest.py` promises is proven here once, so a test that uses it can trust
it: factories that leave the books straight, the accounting assertions every test of a
credit movement ends with (`tests/support/books.py`), bootstrap admins whose role comes
from their address and a barista whose role is in their row, `as_user` standing in for
the portal and `portal_headers` speaking as it, and the same fixtures giving the
application with one dependency down (SPEC §9, and the "Failures" tests of §12).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from uuid import UUID, uuid7

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.config import Settings
from app.core.degrees import is_programme
from app.core.enums import (
    AccountKind,
    DegreeLevel,
    MarketStatus,
    Outcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.identity import Caller, PortalIdentity
from app.core.units import MICRO
from app.db.database import Database
from app.integrations.redis.client import RedisClient, RedisUnavailableError
from app.integrations.redis.rate_limiter import RateLimit
from app.integrations.scylla.client import ScyllaClient
from app.models import Account, JournalEntry, Market, Posting, Trade, User
from app.pricing import lmsr
from tests.support.books import (
    FLOORED_KINDS,
    Books,
    assert_books_balance,
    count_rows,
    read_books,
    replay,
    system_balance,
    violations,
)
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.economy import PRECISION
from tests.support.factories import ActAs, Factory, PortalHeaders, identity_of
from tests.support.network import SwitchableProxy
from tests.support.settings import TEST_ADMIN_EMAILS

pytestmark = pytest.mark.integration

RULE = RateLimit("harness", limit=1, window_seconds=60)
ADMIN_ROUTE = "/api/admin/risk"
BARISTA_ROUTE = "/api/barista/vouchers/redeem"
BARISTA_BODY = {"code": "ABCD-EFGH"}
REFUSED = (401, 403)
"""What a guard answers. The routes called here answer whoever the guards let through
with something else: 200, or 400 for a code that is no voucher's."""
SCYLLA_RECOVERY_SECONDS = 30.0
"""The driver retries a dead node every 5 seconds; this leaves it several attempts."""


async def health(client: httpx.AsyncClient) -> dict[str, str]:
    body: dict[str, str] = (await client.get("/api/health")).json()
    return body


# --- Factories -------------------------------------------------------------------


async def test_factory_users_have_a_profile_an_account_and_their_signup_grant(
    factory: Factory, container: Container
) -> None:
    first = await factory.user()
    second = await factory.user(
        email="neo@ethz.ch",
        username="neo",
        name="",
        degree_level=DegreeLevel.OTHER,
        degree_programme="ETH staff",
        role=UserRole.ADMIN,
        status=UserStatus.SUSPENDED,
    )

    assert (first.email, first.username, first.name) == ("user1@ethz.ch", "user1", "User 1")
    assert (first.role, first.status) == (UserRole.USER, UserStatus.ACTIVE)
    assert first.policy_accepted_at == first.created_at == TEST_NOW
    assert is_programme(first.degree_level, first.degree_programme)
    assert (second.email, second.username, second.name) == ("neo@ethz.ch", "neo", "")
    assert (second.degree_level, second.degree_programme) == (DegreeLevel.OTHER, "ETH staff")
    assert (second.role, second.status) == (UserRole.ADMIN, UserStatus.SUSPENDED)
    async with container.uow_factory() as uow:
        for user in (first, second):
            assert await uow.accounts.get_user_account(user.id) is not None
            assert await uow.grants.list_periods(user.id) == ["SIGNUP"]
        holder = await uow.users.get_by_username("neo")
        assert holder is not None
        assert holder.id == second.id


async def test_factory_data_keeps_the_books_straight(
    factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()

    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)
    await factory.position(alice, market, Outcome.NO, shares_micro=3 * MICRO, cost_micro=2 * MICRO)

    assert await factory.balance_micro(alice) < 3 * MICRO
    assert await factory.accounts_total_micro() == 0
    assert (market.q_yes_micro, market.q_no_micro, market.version) == (MICRO, 3 * MICRO, 2)
    # The four invariants of the circuit breaker (A.4) hold for what the factory wrote.
    async with container.uow_factory() as uow:
        assert not await uow.accounts.has_negative_user_balance()
        assert await uow.positions.sum_shares_by_outcome(market.id) == {
            Outcome.YES: MICRO,
            Outcome.NO: 3 * MICRO,
        }
        account = await uow.accounts.get_market_account(market.id)
        assert account is not None
        exposure_micro = max(market.q_yes_micro, market.q_no_micro) - account.balance_micro
        assert exposure_micro <= market.budget_micro


async def test_factory_refuses_a_position_sold_below_its_cost(factory: Factory) -> None:
    """Shares given away would let a market lose more than its budget."""
    alice = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()

    with pytest.raises(ValueError, match="loss bound"):
        await factory.position(alice, market, Outcome.YES, shares_micro=5 * MICRO, cost_micro=1)

    assert await factory.balance_micro(alice) == 5 * MICRO


# --- The books (tests/support/books.py) -----------------------------------------------
#
# Every rule is shown twice: holding for what the factory wrote, and found when it is
# broken. A book is broken through the owner role, behind the application's back; the one
# thing Postgres refuses to store even then, a balance below its floor, is put into the
# book after it was read.


async def one_traded_market(factory: Factory) -> tuple[User, Market]:
    """Alice holds one YES share of an open market, bought at its LMSR price."""
    alice = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)
    return alice, market


def with_balance(books: Books, account_id: UUID, balance_micro: int) -> Books:
    """`books` as they would be if one account stood at `balance_micro`."""
    return replace(
        books,
        accounts=[
            Account(
                id=account.id,
                kind=account.kind,
                owner_id=account.owner_id,
                market_id=account.market_id,
                balance_micro=balance_micro if account.id == account_id else account.balance_micro,
            )
            for account in books.accounts
        ],
    )


def account_of(books: Books, kind: AccountKind) -> Account:
    """The first account of `kind` in `books`."""
    return next(account for account in books.accounts if account.kind is kind)


async def test_the_books_of_factory_made_data_balance(
    factory: Factory, container: Container
) -> None:
    """Users with and without a balance, markets in every kind of status, positions on
    both outcomes, one of them bought above its LMSR price."""
    alice = await factory.user(balance_micro=500 * MICRO)
    bob = await factory.user(balance_micro=50 * MICRO)
    await factory.user()
    traded = await factory.market()
    await factory.position(alice, traded, Outcome.YES, shares_micro=40 * MICRO)
    await factory.position(bob, traded, Outcome.NO, shares_micro=15 * MICRO)
    await factory.position(bob, traded, Outcome.YES, shares_micro=MICRO, cost_micro=2 * MICRO)
    closed = await factory.market(status=MarketStatus.CLOSED)
    await factory.position(alice, closed, Outcome.NO, shares_micro=7 * MICRO)
    for status in (
        MarketStatus.PROPOSED,
        MarketStatus.REJECTED,
        MarketStatus.HALTED,
        MarketStatus.RESOLVED,
        MarketStatus.VOID,
    ):
        await factory.market(status=status, created_by=alice)

    await assert_books_balance(container.uow_factory)

    books = await read_books(container.uow_factory)
    assert violations(books) == []
    assert len(books.markets) == 7
    assert books.shares_held_micro == {
        (traded.id, Outcome.YES): 41 * MICRO,
        (traded.id, Outcome.NO): 15 * MICRO,
        (closed.id, Outcome.NO): 7 * MICRO,
    }
    # Two signup grants and four buys, each an entry of its own that sums to zero.
    assert list(books.entry_totals_micro.values()) == [0] * 6
    assert len(books.entry_totals_micro) == await count_rows(container.uow_factory, JournalEntry)
    # `vis:issuance` is where the credits came from: it is below zero and may be.
    assert account_of(books, AccountKind.VIS_ISSUANCE).balance_micro == -550 * MICRO


async def test_the_books_of_an_empty_database_balance(container: Container) -> None:
    books = await read_books(container.uow_factory)

    assert violations(books) == []
    assert [account.balance_micro for account in books.accounts] == [0, 0, 0, 0]
    assert (books.entry_totals_micro, books.markets, books.shares_held_micro) == ({}, [], {})


async def test_credit_that_came_from_nowhere_is_found(
    factory: Factory, container: Container, owner_engine: AsyncEngine
) -> None:
    await one_traded_market(factory)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE accounts SET balance_micro = balance_micro + 1 WHERE kind = 'VIS_MM_PNL'")
        )

    assert violations(await read_books(container.uow_factory)) == [
        "ledger_nets_to_zero: all accounts sum to 1 µCR, not to 0"
    ]
    with pytest.raises(AssertionError, match="the books do not balance:\n  ledger_nets_to_zero"):
        await assert_books_balance(container.uow_factory)


async def test_a_journal_entry_that_does_not_sum_to_zero_is_found(
    factory: Factory, container: Container, owner_engine: AsyncEngine
) -> None:
    """Postgres checks every entry at commit, also for the owner role. The check is a
    trigger, and here it is switched off for one transaction to leave a book that only
    a restored dump or a dropped trigger could leave."""
    await one_traded_market(factory)
    async with owner_engine.begin() as connection:
        await connection.execute(text("SET LOCAL session_replication_role = replica"))
        entry_id = await connection.scalar(
            text(
                "UPDATE postings SET amount_micro = amount_micro + 3"
                " WHERE id = (SELECT max(id) FROM postings) RETURNING entry_id"
            )
        )

    # No balance moved: the trigger that applies a posting was off too.
    assert violations(await read_books(container.uow_factory)) == [
        f"entry_sums_to_zero: journal entry {entry_id} sums to 3 µCR"
    ]
    with pytest.raises(AssertionError, match="entry_sums_to_zero"):
        await assert_books_balance(container.uow_factory)
    assert await count_rows(container.uow_factory, Posting) == 4


@pytest.mark.parametrize(
    ("corrupt", "q_micro", "held_micro"),
    [
        (
            "UPDATE markets SET q_yes_micro = q_yes_micro + 1 WHERE id = :market_id",
            (MICRO + 1, 0),
            (MICRO, 0),
        ),
        (
            "UPDATE markets SET q_no_micro = 5 WHERE id = :market_id",
            (MICRO, 5),
            (MICRO, 0),
        ),
        (
            "UPDATE positions SET shares_micro = shares_micro - 1 WHERE market_id = :market_id",
            (MICRO, 0),
            (MICRO - 1, 0),
        ),
        (
            "DELETE FROM positions WHERE market_id = :market_id",
            (MICRO, 0),
            (0, 0),
        ),
    ],
    ids=["q-yes-too-high", "q-no-without-a-position", "a-share-missing", "position-gone"],
)
async def test_a_market_whose_q_is_not_the_sum_of_its_positions_is_found(
    factory: Factory,
    container: Container,
    owner_engine: AsyncEngine,
    corrupt: str,
    q_micro: tuple[int, int],
    held_micro: tuple[int, int],
) -> None:
    _, market = await one_traded_market(factory)
    untouched = await factory.market()
    async with owner_engine.begin() as connection:
        await connection.execute(text(corrupt), {"market_id": market.id})

    found = violations(await read_books(container.uow_factory))

    assert found == [
        f"positions_match_market: market {market.id} has q = {q_micro} µshares (YES, NO), "
        f"its positions hold {held_micro}"
    ]
    assert str(untouched.id) not in found[0]
    with pytest.raises(AssertionError, match="positions_match_market"):
        await assert_books_balance(container.uow_factory)


async def test_a_market_that_could_lose_more_than_its_budget_is_found(
    factory: Factory, container: Container, owner_engine: AsyncEngine
) -> None:
    """Shares that were given away: q and the position agree, and the market holds too
    little to pay them. It is what the factory refuses to write."""
    alice, market = await one_traded_market(factory)
    paid_micro = 5 * MICRO - await factory.balance_micro(alice)
    given_micro = market.budget_micro + paid_micro
    async with owner_engine.begin() as connection:
        for statement in (
            "UPDATE positions SET shares_micro = shares_micro + :given WHERE market_id = :id",
            "UPDATE markets SET q_yes_micro = q_yes_micro + :given WHERE id = :id",
        ):
            await connection.execute(text(statement), {"given": given_micro, "id": market.id})

    assert violations(await read_books(container.uow_factory)) == [
        f"market_within_budget: market {market.id} could lose "
        f"{MICRO + market.budget_micro} µCR, its budget is {market.budget_micro} µCR"
    ]
    with pytest.raises(AssertionError, match="market_within_budget"):
        await assert_books_balance(container.uow_factory)


async def test_a_settled_market_owes_nothing_whatever_its_q_says(
    factory: Factory, container: Container, owner_engine: AsyncEngine
) -> None:
    """A resolved market keeps its q and its positions and has paid its account out, so
    the loss bound is asked of markets that are not settled only."""
    alice, market = await one_traded_market(factory)
    paid_micro = 5 * MICRO - await factory.balance_micro(alice)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE markets SET status = 'RESOLVED', q_yes_micro = :q WHERE id = :id"),
            {"q": 2 * market.budget_micro, "id": market.id},
        )
        await connection.execute(
            text("UPDATE positions SET shares_micro = :q WHERE market_id = :id"),
            {"q": 2 * market.budget_micro, "id": market.id},
        )

    books = await read_books(container.uow_factory)

    assert violations(books) == []
    assert account_of(books, AccountKind.MARKET).balance_micro == paid_micro


@pytest.mark.parametrize("kind", sorted(FLOORED_KINDS))
async def test_an_account_below_its_floor_is_found(
    factory: Factory, container: Container, kind: AccountKind
) -> None:
    """A user, a market and the two voucher accounts never go below zero. Postgres
    refuses to store such a balance for any role (test_schema.py), so it is put into
    the book here after the book was read. The total stays zero: only the floor breaks."""
    await one_traded_market(factory)
    books = await read_books(container.uow_factory)
    victim = account_of(books, kind)
    issuance = account_of(books, AccountKind.VIS_ISSUANCE)
    taken_micro = victim.balance_micro + 1

    overdrawn = with_balance(books, victim.id, -1)
    overdrawn = with_balance(overdrawn, issuance.id, issuance.balance_micro + taken_micro)

    assert violations(books) == []
    assert violations(overdrawn) == [
        f"balance_not_below_zero: the {kind} account {victim.id} is at -1 µCR"
    ]


@pytest.mark.parametrize("kind", [AccountKind.VIS_ISSUANCE, AccountKind.VIS_MM_PNL])
async def test_the_two_accounts_without_a_floor_may_be_below_zero(
    factory: Factory, container: Container, kind: AccountKind
) -> None:
    """`vis:issuance` hands out every credit and `vis:mm_pnl` takes the market maker's
    losses (A.8)."""
    alice, _ = await one_traded_market(factory)
    books = await read_books(container.uow_factory)
    below = account_of(books, kind)
    alices = next(account for account in books.accounts if account.owner_id == alice.id)

    moved = with_balance(books, below.id, below.balance_micro - 7)
    moved = with_balance(moved, alices.id, alices.balance_micro + 7)

    assert account_of(moved, kind).balance_micro < 0
    assert violations(moved) == []


async def test_every_broken_rule_is_reported_at_once(
    factory: Factory, container: Container, owner_engine: AsyncEngine
) -> None:
    _, market = await one_traded_market(factory)
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE accounts SET balance_micro = balance_micro - 2 WHERE kind = 'VIS_ISSUANCE'"
            )
        )
        await connection.execute(
            text("UPDATE markets SET q_no_micro = 1 WHERE id = :id"), {"id": market.id}
        )

    with pytest.raises(AssertionError) as failed:
        await assert_books_balance(container.uow_factory)

    lines = str(failed.value).splitlines()
    assert lines[0] == "the books do not balance:"
    assert [line.split(":")[0].strip() for line in lines[1:]] == [
        "ledger_nets_to_zero",
        "positions_match_market",
    ]


def trade_from(quote: lmsr.TradeQuote, market: Market, user: User, version: int) -> Trade:
    """The row a trade leaves when it executes at `quote` as the market's `version`."""
    return Trade(
        id=uuid7(),
        market_id=market.id,
        user_id=user.id,
        side=quote.side,
        outcome=quote.outcome,
        shares_micro=quote.shares_micro,
        amount_micro=quote.amount_micro,
        p_yes_before_micro=quote.p_yes_before_micro,
        p_yes_after_micro=quote.p_yes_after_micro,
        market_version=version,
        created_at=TEST_NOW,
    )


async def test_replay_reproduces_the_trades_of_a_market_from_its_first_one(
    factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=500 * MICRO)
    bob = await factory.user(balance_micro=500 * MICRO)
    market = await factory.market()
    opened = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
    await factory.position(alice, market, Outcome.YES, shares_micro=30 * MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=55 * MICRO)
    # A buy sized by an amount: the shares a budget pays for cost exactly that budget.
    now = lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro)
    for_25_cr = lmsr.quote_buy_for_budget(now, Outcome.YES, 25 * MICRO, precision=PRECISION)
    await factory.position(bob, market, Outcome.YES, shares_micro=for_25_cr.shares_micro)
    async with container.uow_factory() as uow:
        trades = list(await uow.trades.list_for_market(market.id, limit=10))
    assert [trade.market_version for trade in trades] == [1, 2, 3]
    assert trades[2].amount_micro == 25 * MICRO

    end = replay(opened, reversed(trades), precision=PRECISION)

    assert (end.q_yes, end.q_no, end.b) == (market.q_yes_micro, market.q_no_micro, market.b_micro)
    # Any stretch of the history replays from the state before it.
    after_the_first = replay(opened, trades[:1], precision=PRECISION)
    assert replay(after_the_first, trades[1:], precision=PRECISION) == end
    assert replay(end, [], precision=PRECISION) == end

    # A sell is priced as a sell: half of Bob's NO shares, then all of Alice's YES ones.
    half = lmsr.quote_sell_shares(end, Outcome.NO, 27_500_000, precision=PRECISION)
    rest = lmsr.quote_sell_shares(half.after, Outcome.YES, 30 * MICRO, precision=PRECISION)
    sells = [trade_from(half, market, bob, 4), trade_from(rest, market, alice, 5)]
    assert {trade.side for trade in sells} == {TradeSide.SELL}
    assert replay(opened, [*sells, *trades], precision=PRECISION) == rest.after


async def test_replay_finds_a_trade_that_was_priced_on_stale_quantities(
    factory: Factory,
) -> None:
    """What two trades that did not wait for each other would leave: both priced on the
    market as it was before either."""
    alice, bob = await factory.user(), await factory.user()
    market = await factory.market()
    opened = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
    stale = lmsr.quote_buy_shares(opened, Outcome.YES, 50 * MICRO, precision=PRECISION)
    first, second = trade_from(stale, market, alice, 1), trade_from(stale, market, bob, 2)

    assert replay(opened, [first], precision=PRECISION) == stale.after
    with pytest.raises(AssertionError, match=rf"trade {second.id} \(version 2\) recorded"):
        replay(opened, [first, second], precision=PRECISION)


async def test_replay_finds_a_trade_that_did_not_cost_what_lmsr_says(
    factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=500 * MICRO)
    market = await factory.market()
    opened = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
    await factory.position(alice, market, Outcome.YES, shares_micro=10 * MICRO)
    cost_micro = 500 * MICRO - await factory.balance_micro(alice)
    await factory.position(
        alice, market, Outcome.NO, shares_micro=10 * MICRO, cost_micro=cost_micro + 1
    )
    async with container.uow_factory() as uow:
        trades = await uow.trades.list_for_market(market.id, limit=10)

    with pytest.raises(AssertionError, match=r"version 2\) recorded"):
        replay(opened, trades, precision=PRECISION)
    # The books still balance: a trade above its price breaks no rule of the ledger.
    await assert_books_balance(container.uow_factory)


async def test_replay_finds_a_version_that_does_not_follow_the_one_before(
    factory: Factory, container: Container
) -> None:
    alice = await factory.user(balance_micro=500 * MICRO)
    market = await factory.market()
    opened = lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro)
    for _ in range(3):
        await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)
    async with container.uow_factory() as uow:
        first, second, third = await uow.trades.list_for_market(market.id, limit=10)

    with pytest.raises(AssertionError, match=f"trade {third.id} has version 3, which does not"):
        replay(opened, [first, third], precision=PRECISION)
    assert replay(opened, [third, first, second], precision=PRECISION).q_yes == 3 * MICRO


async def test_count_rows_and_system_balance_read_what_the_factory_wrote(
    factory: Factory, container: Container
) -> None:
    uow_factory = container.uow_factory
    assert await count_rows(uow_factory, User) == 0
    alice, _ = await factory.user(balance_micro=5 * MICRO), await factory.user()
    market = await factory.market(created_by=alice)
    await factory.position(alice, market, Outcome.YES, shares_micro=MICRO)

    assert await count_rows(uow_factory, User) == 2
    assert await count_rows(uow_factory, Market) == 1
    assert await count_rows(uow_factory, Trade) == 1
    # One signup grant that moved credits, and the buy.
    assert await count_rows(uow_factory, JournalEntry) == 2
    assert await count_rows(uow_factory, Account) == 4 + 2 + 1
    assert await system_balance(uow_factory, AccountKind.VIS_ISSUANCE) == -5 * MICRO
    for untouched in (
        AccountKind.VIS_MM_PNL,
        AccountKind.VIS_REDEMPTION_PENDING,
        AccountKind.VIS_REDEEMED,
    ):
        assert await system_balance(uow_factory, untouched) == 0
    with pytest.raises(ValueError, match="not a system account"):
        await system_balance(uow_factory, AccountKind.USER)


# --- Admins and baristas --------------------------------------------------------------


async def test_the_factory_registers_the_bootstrap_admins_of_the_test_settings_and_baristas(
    factory: Factory, container: Container
) -> None:
    """Two admins, so that one can settle what the other traded (A.4) and act on the
    other's account, and as many baristas as a test asks for."""
    first, second = await factory.admin(), await factory.admin(1)
    barista = await factory.barista(balance_micro=3 * MICRO)
    another = await factory.barista(1)

    assert (first.email, second.email) == TEST_ADMIN_EMAILS
    assert (barista.email, another.email) == ("barista0@ethz.ch", "barista1@ethz.ch")
    assert [user.username for user in (first, second, barista, another)] == [
        "admin0",
        "admin1",
        "barista0",
        "barista1",
    ]
    # A bootstrap admin is one by its address: the row says USER, as a profile starts.
    assert first.role is second.role is UserRole.USER
    assert barista.role is another.role is UserRole.BARISTA
    assert first.status is second.status is barista.status is UserStatus.ACTIVE
    assert await factory.balance_micro(barista) == 3 * MICRO
    async with container.uow_factory() as uow:
        for user in (first, second, barista, another):
            assert await uow.accounts.get_user_account(user.id) is not None
            assert await uow.grants.list_periods(user.id) == ["SIGNUP"]
    assert await factory.accounts_total_micro() == 0


async def test_the_factory_has_no_more_bootstrap_admins_than_the_settings_name(
    factory: Factory,
) -> None:
    for index in (2, -1):
        with pytest.raises(ValueError, match="there is none with index"):
            await factory.admin(index)
    with pytest.raises(ValueError, match="no barista with index -1"):
        await factory.barista(-1)


async def test_factory_identity_gives_each_user_the_role_they_have_in_effect(
    factory: Factory,
) -> None:
    """The role of the row, and ADMIN for a bootstrap admin whatever its row says."""
    student = await factory.user()
    first, second = await factory.admin(), await factory.admin(1)
    barista = await factory.barista()
    made_admin = await factory.user(role=UserRole.ADMIN)

    assert factory.identity(student).role is UserRole.USER
    assert factory.identity(first).role is factory.identity(second).role is UserRole.ADMIN
    assert factory.identity(barista).role is UserRole.BARISTA
    assert factory.identity(made_admin).role is UserRole.ADMIN
    registered = factory.identity(first)
    assert (registered.id, registered.email, registered.username, registered.status) == (
        first.id,
        first.email,
        first.username,
        first.status,
    )


async def test_factory_identity_follows_the_row_and_the_bootstrap_admins_of_the_settings(
    factory: Factory, settings: Settings, database: Database, clock: FrozenClock
) -> None:
    """The way the application works it out: under another list of bootstrap admins the
    same rows have the roles their rows say, and whoever that list names is an admin."""
    admin, barista, student = await factory.admin(), await factory.barista(), await factory.user()
    swapped = settings.identity.model_copy(update={"admin_emails": [student.email]})
    elsewhere = Container.build(
        settings.model_copy(update={"identity": swapped}), clock=clock, database=database
    )
    try:
        under_another_list = Factory(elsewhere)

        assert under_another_list.identity(student).role is UserRole.ADMIN
        assert under_another_list.identity(admin).role is UserRole.USER
        assert under_another_list.identity(barista).role is UserRole.BARISTA
    finally:
        await elsewhere.stop()
    assert factory.identity(admin).role is UserRole.ADMIN


async def test_identity_of_gives_a_row_whatever_role_a_guard_test_names(
    factory: Factory,
) -> None:
    student = await factory.user()

    for role in UserRole:
        named = identity_of(student, role)
        assert (named.id, named.role) == (student.id, role)


# --- Acting as somebody ------------------------------------------------------------


async def test_without_as_user_nobody_is_calling(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401


async def test_as_user_makes_a_user_of_the_factory_a_plain_user_unless_a_role_is_given(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    """A guard test names the role it means, whatever the row says."""
    user = await factory.user()

    as_user(user)
    assert (await client.get("/api/me")).status_code not in REFUSED
    assert (await client.get(ADMIN_ROUTE)).status_code == 403
    assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code == 403

    as_user(user, UserRole.BARISTA)
    assert (await client.get(ADMIN_ROUTE)).status_code == 403
    assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code not in REFUSED

    as_user(user, UserRole.ADMIN)
    assert (await client.get(ADMIN_ROUTE)).status_code not in REFUSED
    assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code not in REFUSED


async def test_as_user_gives_an_admin_and_a_barista_the_role_they_have_in_effect(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    """`as_user(await factory.admin())` is an admin without the test saying so twice,
    and so is a user whose row an admin set to ADMIN."""
    admin, second_admin = await factory.admin(), await factory.admin(1)
    barista = await factory.barista()
    made_admin = await factory.user(role=UserRole.ADMIN)

    for one_of_them in (admin, second_admin, made_admin):
        as_user(one_of_them)
        assert (await client.get(ADMIN_ROUTE)).status_code not in REFUSED
        assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code not in REFUSED

    as_user(barista)
    assert (await client.get(ADMIN_ROUTE)).status_code == 403
    assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code not in REFUSED


async def test_a_role_named_to_as_user_wins_over_the_address_and_the_row(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    admin, barista = await factory.admin(), await factory.barista()

    for staff in (admin, barista):
        as_user(staff, UserRole.USER)

        assert (await client.get(ADMIN_ROUTE)).status_code == 403
        assert (await client.post(BARISTA_ROUTE, json=BARISTA_BODY)).status_code == 403


async def test_as_user_takes_a_registered_caller_as_it_is(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    user, admin = await factory.user(), await factory.admin()

    as_user(identity_of(user, UserRole.ADMIN))
    assert (await client.get(ADMIN_ROUTE)).status_code not in REFUSED

    as_user(factory.identity(user))
    assert (await client.get(ADMIN_ROUTE)).status_code == 403

    as_user(factory.identity(admin))
    assert (await client.get(ADMIN_ROUTE)).status_code not in REFUSED


async def test_as_user_with_a_portal_identity_is_somebody_without_a_profile(
    client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(PortalIdentity(email="fresh@ethz.ch", name="Fresh Face", role=UserRole.USER))

    response = await client.get("/api/me")
    assert response.status_code == 403
    assert response.json()["code"] == "PROFILE_REQUIRED"
    assert response.json()["details"] == {"name": "Fresh Face", "email": "fresh@ethz.ch"}

    profile = {
        "username": "fresh",
        "degree_level": "OTHER",
        "degree_programme": "ETH staff",
        "policy_accepted": True,
    }
    saved = await client.put("/api/me/profile", json=profile)
    assert saved.status_code not in REFUSED, "the one route such a caller may use"


async def test_as_user_gives_a_role_to_a_user_row_only(factory: Factory, as_user: ActAs) -> None:
    """A caller built by hand already says what it is; a second role would be ignored."""
    user = await factory.user()
    newcomer = PortalIdentity(email="fresh@ethz.ch", name="", role=UserRole.USER)

    for actor in (factory.identity(user), newcomer, None):
        with pytest.raises(ValueError, match="only a `User` row takes a role"):
            as_user(actor, UserRole.ADMIN)


async def test_as_user_none_is_nobody_again(
    client: httpx.AsyncClient, factory: Factory, as_user: ActAs
) -> None:
    as_user(await factory.user())
    assert (await client.get("/api/me")).status_code not in REFUSED

    as_user(None)

    assert (await client.get("/api/me")).status_code == 401


# --- Speaking as the portal ----------------------------------------------------------


class ResolvedIdentity:
    """Stands in for `IdentityService`: remembers what the seam asked it and answers
    with the registered caller a test prepared."""

    def __init__(self, caller: Caller) -> None:
        self._caller = caller
        self.asked: list[tuple[str, str]] = []

    async def authenticate(self, user_id: str, user_name: str) -> Caller:
        self.asked.append((user_id, user_name))
        return self._caller


async def test_portal_headers_are_the_two_headers_the_portal_sends(
    factory: Factory, settings: Settings, portal_headers: PortalHeaders
) -> None:
    """The id as the address, the name percent-encoded, under the names of the settings."""
    zoe = "Zo" + chr(0xEB) + " M" + chr(0xFC) + "ller"
    user = await factory.user(email="zoe@ethz.ch", name=zoe)
    nameless = await factory.user(name="")
    newcomer = PortalIdentity(email="fresh@ethz.ch", name="Fresh Face", role=UserRole.USER)
    names = settings.identity

    assert portal_headers(user) == {
        names.header_user_id: "zoe@ethz.ch",
        names.header_user_name: "Zo%C3%AB%20M%C3%BCller",
    }
    assert portal_headers(factory.identity(user)) == portal_headers(user)
    assert portal_headers(nameless) == {
        names.header_user_id: nameless.email,
        names.header_user_name: "",
    }
    assert portal_headers(newcomer) == {
        names.header_user_id: "fresh@ethz.ch",
        names.header_user_name: "Fresh%20Face",
    }


async def test_a_request_with_portal_headers_goes_through_the_real_seam(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    portal_headers: PortalHeaders,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `as_user`: `authenticate_request` reads the headers and asks the identity
    service, which gets the address and the name as the portal meant them."""
    zoe = "Zo" + chr(0xEB) + " M" + chr(0xFC) + "ller"
    admin = await factory.admin()
    student = await factory.user(name=zoe)
    for user, admin_route_refuses in ((admin, False), (student, True)):
        registered = factory.identity(user)
        portal = PortalIdentity(email=user.email, name=user.name, role=registered.role)
        identity = ResolvedIdentity(Caller(identity=portal, user=registered))
        monkeypatch.setattr(container, "identity", identity)

        response = await client.get(ADMIN_ROUTE, headers=portal_headers(user))

        assert identity.asked == [(user.email, user.name)]
        assert (response.status_code == 403) == admin_route_refuses
        assert response.status_code != 401


# --- One dependency down -----------------------------------------------------------


@pytest.mark.redis_down
async def test_redis_down_gives_the_same_fixtures_an_application_without_redis(
    client: httpx.AsyncClient,
    container: Container,
    factory: Factory,
    as_user: ActAs,
    redis_client: RedisClient,
) -> None:
    assert await health(client) == {
        "status": "degraded",
        "postgres": "up",
        "redis": "down",
        "scylla": "down",
        "media": "down",
    }
    with pytest.raises(RedisUnavailableError):
        await container.rate_limiter.hit(RULE, "someone")
    assert (await container.fallback_rate_limiter.hit(RULE, "someone")).allowed

    # Postgres is the shared, clean one, and the guards work as ever.
    user = await factory.user(balance_micro=MICRO)
    assert await factory.balance_micro(user) == MICRO
    as_user(user)
    assert (await client.get("/api/admin/risk")).status_code == 403

    # The real Redis of the test run is untouched and still there for other tests.
    assert await redis_client.ping()


async def test_without_the_marker_redis_is_up(client: httpx.AsyncClient) -> None:
    assert (await health(client))["redis"] == "up"


@pytest.mark.postgres_down
async def test_postgres_down_gives_an_application_whose_database_is_unreachable(
    client: httpx.AsyncClient, container: Container
) -> None:
    assert not await container.database.ping()
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["details"] == {
        "postgres": "down",
        "redis": "up",
        "scylla": "down",
        "media": "down",
    }


@pytest.mark.redis_down
@pytest.mark.postgres_down
async def test_the_outage_markers_combine(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["details"] == {
        "postgres": "down",
        "redis": "down",
        "scylla": "down",
        "media": "down",
    }


async def wait_for_scylla(client: httpx.AsyncClient, wanted: str) -> None:
    """Poll `/health` until it reports ScyllaDB as `wanted`; fail after the recovery time."""
    async with asyncio.timeout(SCYLLA_RECOVERY_SECONDS):
        while True:
            if (await health(client))["scylla"] == wanted:
                return
            await asyncio.sleep(0.5)


@pytest.mark.scylla
async def test_scylla_proxy_puts_the_application_on_a_scylla_that_can_die_and_return(
    client: httpx.AsyncClient,
    app: FastAPI,
    factory: Factory,
    scylla_proxy: SwitchableProxy,
    scylla_client: ScyllaClient,
    flush_redis: Callable[[], Awaitable[None]],
) -> None:
    """The shape of §12's "with Scylla stopped trades succeed and the chart catches up
    after restart": the node goes away, Postgres work goes on, the node comes back."""
    assert app.state.container.settings.scylla.port == scylla_proxy.port
    # Nothing listens on the socket of the media box in the suite, so the status is
    # `degraded` with every store up.
    assert await health(client) == {
        "status": "degraded",
        "postgres": "up",
        "redis": "up",
        "scylla": "up",
        "media": "down",
    }

    scylla_proxy.cut()
    await wait_for_scylla(client, "down")
    user = await factory.user(balance_micro=MICRO)
    assert await factory.balance_micro(user) == MICRO

    scylla_proxy.restore()
    await wait_for_scylla(client, "up")
    # The node itself never stopped: a client that bypasses the proxy saw no outage.
    assert await scylla_client.execute("SELECT now() FROM system.local")
    await flush_redis()
