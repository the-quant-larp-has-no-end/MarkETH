"""The circuit breaker's four checks, each holding and each broken (SPEC §5, §8 step 6,
Appendix A.4).

`InvariantChecker.check` runs inside a trade's transaction, after its writes. Here it
is asked about books the factory wrote, which are sound, and about the same books once
they were bent behind the application's back, through the owner role. It names the
first check that fails and says, in a context that goes into an alert as JSON, what it
found.

One book cannot be staged that way: Postgres refuses a user balance below zero from
every role. That check is shown inside a transaction of the owner role which first
drops the CHECK, and which is rolled back, so the schema is the same afterwards.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Final

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, async_sessionmaker

from app.container import Container
from app.core.enums import AccountKind, MarketStatus, Outcome
from app.core.errors import InvariantViolationError
from app.core.units import MICRO
from app.models import Market, User
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.invariants import InvariantChecker
from tests.support.books import assert_books_balance, system_balance
from tests.support.factories import Factory

pytestmark = pytest.mark.integration

HELD: Final[int] = 40 * MICRO
"""YES shares Alice holds in the market of these tests."""

USER_BALANCE_FLOOR: Final[str] = "ck_accounts_user_balance_non_negative"
"""The CHECK that keeps a user's balance at zero or above (`app.models.ledger`)."""

GIVE_AWAY: Final[tuple[str, str]] = (
    "UPDATE positions SET shares_micro = shares_micro + :given"
    " WHERE market_id = :market_id AND outcome = :outcome",
    "UPDATE markets SET q_yes_micro = q_yes_micro + :given_yes, q_no_micro = q_no_micro + :given_no"
    " WHERE id = :market_id",
)
"""Hands out shares nobody paid for: the market's q and its positions still agree, and
the market holds too little to pay them."""


async def check(uow_factory: UowFactory, market: Market) -> None:
    """Run the four checks on `market` as a trade does: in a transaction that holds
    the market's row."""
    async with uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        assert row is not None
        await InvariantChecker().check(uow, row)


async def broken(uow_factory: UowFactory, market: Market) -> tuple[str, dict[str, Any]]:
    """The check that fails for `market`, and the context it reports."""
    with pytest.raises(InvariantViolationError) as violation:
        await check(uow_factory, market)
    context = violation.value.context
    # It is stored in `alerts.context`: JSON, with ids as text and amounts as integers.
    assert json.loads(json.dumps(context)) == context
    assert all(type(value) in (str, int) for value in context.values())
    return violation.value.invariant, context


async def corrupt(owner_engine: AsyncEngine, statement: str, **parameters: Any) -> None:
    """Change the database as the owner role, behind the application's back."""
    async with owner_engine.begin() as connection:
        await connection.execute(text(statement), parameters)


def given_away(market: Market, outcome: Outcome, shares_micro: int) -> dict[str, Any]:
    """The parameters of `GIVE_AWAY` for shares of one outcome of `market`."""
    return {
        "market_id": market.id,
        "outcome": outcome.value,
        "given": shares_micro,
        "given_yes": shares_micro if outcome is Outcome.YES else 0,
        "given_no": shares_micro if outcome is Outcome.NO else 0,
    }


async def give_away(
    owner_engine: AsyncEngine, market: Market, outcome: Outcome, shares_micro: int
) -> None:
    for statement in GIVE_AWAY:
        await corrupt(owner_engine, statement, **given_away(market, outcome, shares_micro))


async def market_balance(uow_factory: UowFactory, market: Market) -> int:
    async with uow_factory() as uow:
        account = await uow.accounts.get_market_account(market.id)
    assert account is not None
    return account.balance_micro


@asynccontextmanager
async def without_the_user_balance_floor(
    owner_engine: AsyncEngine,
) -> AsyncIterator[tuple[AsyncConnection, UowFactory]]:
    """The database as it would be without the CHECK on a user's balance, for as long
    as the block is open.

    Yields a connection of the owner role, inside a transaction that has dropped the
    CHECK, and a factory of units of work that run on that connection, each in a
    savepoint. Leaving the block rolls everything back, the dropped CHECK included.
    """
    async with owner_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                text(f"ALTER TABLE accounts DROP CONSTRAINT {USER_BALANCE_FLOOR}")
            )
            sessions = async_sessionmaker(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            yield connection, lambda: UnitOfWork(sessions)
        finally:
            await transaction.rollback()


async def overdraw(connection: AsyncConnection, user: User, by_micro: int) -> None:
    """Put the user's balance at `by_micro` below zero. What is taken from the user is
    put on `vis:issuance`, so all accounts still sum to zero."""
    balance_micro = await connection.scalar(
        text("SELECT balance_micro FROM accounts WHERE owner_id = :user_id"), {"user_id": user.id}
    )
    assert isinstance(balance_micro, int)
    await connection.execute(
        text("UPDATE accounts SET balance_micro = :balance WHERE owner_id = :user_id"),
        {"balance": -by_micro, "user_id": user.id},
    )
    await connection.execute(
        text("UPDATE accounts SET balance_micro = balance_micro + :moved WHERE kind = :kind"),
        {"moved": balance_micro + by_micro, "kind": AccountKind.VIS_ISSUANCE.value},
    )


@pytest.fixture
async def alice(factory: Factory) -> User:
    return await factory.user(balance_micro=5 * HELD)


@pytest.fixture
async def market(factory: Factory, alice: User) -> Market:
    """An OPEN market in which Alice holds `HELD` YES shares, bought at their price."""
    market = await factory.market()
    await factory.position(alice, market, Outcome.YES, shares_micro=HELD)
    return market


# --- A sound book ------------------------------------------------------------------


async def test_books_the_factory_wrote_pass_all_four_checks(
    container: Container, factory: Factory, alice: User, market: Market
) -> None:
    """Both outcomes held, by several users, in several markets, beside a user who holds
    nothing and a market nobody traded."""
    bob = await factory.user(balance_micro=5 * HELD)
    await factory.user()
    await factory.position(bob, market, Outcome.NO, shares_micro=HELD // 2)
    await factory.position(bob, market, Outcome.YES, shares_micro=MICRO)
    halted = await factory.market(status=MarketStatus.HALTED)
    await factory.position(alice, halted, Outcome.NO, shares_micro=HELD)
    untraded = await factory.market()

    for sound in (market, halted, untraded):
        await check(container.uow_factory, sound)

    await assert_books_balance(container.uow_factory)


# --- 1. The ledger nets to zero -------------------------------------------------------


@pytest.mark.parametrize("off_by_micro", [1, -7], ids=["credit-from-nowhere", "credit-lost"])
async def test_accounts_that_do_not_sum_to_zero_break_ledger_nets_to_zero(
    container: Container, market: Market, owner_engine: AsyncEngine, off_by_micro: int
) -> None:
    await corrupt(
        owner_engine,
        "UPDATE accounts SET balance_micro = balance_micro + :off_by WHERE kind = 'VIS_MM_PNL'",
        off_by=off_by_micro,
    )

    assert await broken(container.uow_factory, market) == (
        "ledger_nets_to_zero",
        {"total_balance_micro": off_by_micro},
    )


async def test_the_ledger_is_checked_as_a_whole_whichever_market_is_traded(
    container: Container, factory: Factory, market: Market, owner_engine: AsyncEngine
) -> None:
    """Credit that appeared on the account of a user who never traded stops a trade on
    any market: the books are wrong, wherever."""
    stranger = await factory.user(balance_micro=MICRO)
    elsewhere = await factory.market()
    await corrupt(
        owner_engine,
        "UPDATE accounts SET balance_micro = balance_micro + 3 WHERE owner_id = :user_id",
        user_id=stranger.id,
    )

    for traded in (market, elsewhere):
        assert await broken(container.uow_factory, traded) == (
            "ledger_nets_to_zero",
            {"total_balance_micro": 3},
        )


# --- 2. The positions of a market sum to its q -----------------------------------------------


@pytest.mark.parametrize(
    ("statement", "q_micro", "positions_micro"),
    [
        (
            "UPDATE markets SET q_yes_micro = q_yes_micro + 1 WHERE id = :market_id",
            (HELD + 1, 0),
            (HELD, 0),
        ),
        (
            "UPDATE markets SET q_no_micro = 5 WHERE id = :market_id",
            (HELD, 5),
            (HELD, 0),
        ),
        (
            "UPDATE positions SET shares_micro = shares_micro - 1 WHERE market_id = :market_id",
            (HELD, 0),
            (HELD - 1, 0),
        ),
        (
            "DELETE FROM positions WHERE market_id = :market_id",
            (HELD, 0),
            (0, 0),
        ),
    ],
    ids=["q-yes-too-high", "q-no-without-a-position", "a-share-missing", "position-gone"],
)
async def test_a_q_that_is_not_the_sum_of_the_positions_breaks_positions_match_market(
    container: Container,
    market: Market,
    owner_engine: AsyncEngine,
    statement: str,
    q_micro: tuple[int, int],
    positions_micro: tuple[int, int],
) -> None:
    await corrupt(owner_engine, statement, market_id=market.id)

    assert await broken(container.uow_factory, market) == (
        "positions_match_market",
        {
            "market_id": str(market.id),
            "q_yes_micro": q_micro[0],
            "q_no_micro": q_micro[1],
            "positions_yes_micro": positions_micro[0],
            "positions_no_micro": positions_micro[1],
        },
    )


async def test_what_is_wrong_with_another_market_does_not_fail_this_one(
    container: Container, factory: Factory, alice: User, market: Market, owner_engine: AsyncEngine
) -> None:
    """The second and the fourth check are about the market of the trade alone."""
    mismatched = await factory.market()
    await factory.position(alice, mismatched, Outcome.YES, shares_micro=HELD)
    await corrupt(
        owner_engine,
        "UPDATE markets SET q_yes_micro = 0 WHERE id = :market_id",
        market_id=mismatched.id,
    )
    overstretched = await factory.market()
    await factory.position(alice, overstretched, Outcome.NO, shares_micro=MICRO)
    await give_away(owner_engine, overstretched, Outcome.NO, 2 * overstretched.budget_micro)

    await check(container.uow_factory, market)

    invariant, context = await broken(container.uow_factory, mismatched)
    assert (invariant, context["market_id"]) == ("positions_match_market", str(mismatched.id))
    assert (context["q_yes_micro"], context["positions_yes_micro"]) == (0, HELD)
    invariant, context = await broken(container.uow_factory, overstretched)
    assert (invariant, context["market_id"]) == ("market_within_budget", str(overstretched.id))


# --- 3. No user balance is negative ------------------------------------------------------


async def test_a_user_balance_below_zero_breaks_no_negative_user_balance(
    container: Container, alice: User, market: Market, owner_engine: AsyncEngine
) -> None:
    """The ledger still nets to zero and q still matches the positions: the third check
    is the first that fails."""
    async with without_the_user_balance_floor(owner_engine) as (connection, uow_factory):
        await check(uow_factory, market)

        await overdraw(connection, alice, by_micro=1)

        assert await broken(uow_factory, market) == ("no_negative_user_balance", {})

    # Nothing of it was kept, and the CHECK is back.
    await check(container.uow_factory, market)
    await assert_books_balance(container.uow_factory)
    with pytest.raises(IntegrityError, match=USER_BALANCE_FLOOR):
        await corrupt(
            owner_engine,
            "UPDATE accounts SET balance_micro = -1 WHERE owner_id = :user_id",
            user_id=alice.id,
        )


async def test_the_balance_of_any_user_counts_not_only_the_trader_s(
    factory: Factory, market: Market, owner_engine: AsyncEngine
) -> None:
    """Like the ledger, balances are checked as a whole."""
    stranger = await factory.user(balance_micro=MICRO)
    elsewhere = await factory.market()

    async with without_the_user_balance_floor(owner_engine) as (connection, uow_factory):
        await overdraw(connection, stranger, by_micro=250_000)

        for traded in (market, elsewhere):
            assert await broken(uow_factory, traded) == ("no_negative_user_balance", {})


async def test_a_user_balance_of_exactly_zero_passes(
    container: Container, factory: Factory, market: Market
) -> None:
    spent_out = await factory.user(balance_micro=MICRO)
    await factory.position(spent_out, market, Outcome.NO, shares_micro=MICRO, cost_micro=MICRO)
    assert await factory.balance_micro(spent_out) == 0

    await check(container.uow_factory, market)


async def test_an_account_that_may_be_below_zero_does_not_fail_the_third_check(
    container: Container, market: Market
) -> None:
    """`vis:issuance` is below zero as soon as anybody was granted anything (A.8)."""
    assert await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE) < 0

    await check(container.uow_factory, market)


# --- 4. The market could not lose more than its budget -------------------------------------


async def test_a_market_that_could_lose_more_than_its_budget_breaks_market_within_budget(
    container: Container, market: Market, owner_engine: AsyncEngine
) -> None:
    """What VIS would have to add if the more heavily held outcome won may be the whole
    budget, and not one micro-credit more."""
    paid_micro = await market_balance(container.uow_factory, market)
    exposure_micro = HELD - paid_micro
    assert 0 < exposure_micro < market.budget_micro, "sound so far: the LMSR keeps it there"

    await give_away(owner_engine, market, Outcome.YES, market.budget_micro - exposure_micro)
    await check(container.uow_factory, market)

    await give_away(owner_engine, market, Outcome.YES, 1)
    assert await broken(container.uow_factory, market) == (
        "market_within_budget",
        {
            "market_id": str(market.id),
            "exposure_micro": market.budget_micro + 1,
            "budget_micro": market.budget_micro,
        },
    )


async def test_the_loss_is_that_of_whichever_outcome_is_held_more(
    container: Container, factory: Factory, market: Market, owner_engine: AsyncEngine
) -> None:
    """max(q_yes, q_no): here NO, which trailed, is handed out until it leads by enough."""
    bob = await factory.user(balance_micro=MICRO)
    await factory.position(bob, market, Outcome.NO, shares_micro=MICRO)
    paid_micro = await market_balance(container.uow_factory, market)
    no_shares_the_budget_covers = market.budget_micro + paid_micro
    assert no_shares_the_budget_covers > HELD, "NO leads YES once that many are held"

    await give_away(owner_engine, market, Outcome.NO, no_shares_the_budget_covers - MICRO)
    await check(container.uow_factory, market)

    await give_away(owner_engine, market, Outcome.NO, 1)
    assert await broken(container.uow_factory, market) == (
        "market_within_budget",
        {
            "market_id": str(market.id),
            "exposure_micro": market.budget_micro + 1,
            "budget_micro": market.budget_micro,
        },
    )


# --- Which check is reported, and what the checker sees ---------------------------------------


async def test_the_first_check_that_fails_is_the_one_reported(
    container: Container, market: Market, owner_engine: AsyncEngine
) -> None:
    """In the order ledger, positions, balances, budget."""
    await give_away(owner_engine, market, Outcome.YES, 2 * market.budget_micro)
    assert (await broken(container.uow_factory, market))[0] == "market_within_budget"

    await corrupt(
        owner_engine,
        "UPDATE markets SET q_no_micro = q_no_micro + 1 WHERE id = :market_id",
        market_id=market.id,
    )
    assert (await broken(container.uow_factory, market))[0] == "positions_match_market"

    await corrupt(
        owner_engine,
        "UPDATE accounts SET balance_micro = balance_micro + 1 WHERE kind = 'VIS_ISSUANCE'",
    )
    assert (await broken(container.uow_factory, market))[0] == "ledger_nets_to_zero"


async def test_a_negative_user_balance_is_reported_before_a_broken_budget_and_after_the_rest(
    alice: User, market: Market, owner_engine: AsyncEngine
) -> None:
    async with without_the_user_balance_floor(owner_engine) as (connection, uow_factory):
        for statement in GIVE_AWAY:
            await connection.execute(
                text(statement), given_away(market, Outcome.YES, 2 * market.budget_micro)
            )
        assert (await broken(uow_factory, market))[0] == "market_within_budget"

        await overdraw(connection, alice, by_micro=1)
        assert await broken(uow_factory, market) == ("no_negative_user_balance", {})

        await connection.execute(
            text("UPDATE markets SET q_no_micro = q_no_micro + 1 WHERE id = :market_id"),
            {"market_id": market.id},
        )
        assert (await broken(uow_factory, market))[0] == "positions_match_market"


async def test_the_checks_see_what_their_transaction_wrote_and_has_not_sent_yet(
    container: Container, alice: User, market: Market
) -> None:
    """The checker flushes before it asks. With the session's own flush before every
    query switched off: a position and a q that moved together in memory pass, and a
    position that moved alone does not."""
    checker = container.invariants
    async with container.uow_factory() as uow:
        row = await uow.markets.get_for_update(market.id)
        position = await uow.positions.get(alice.id, market.id, Outcome.YES)
        assert row is not None
        assert position is not None
        with uow.session.no_autoflush:
            position.shares_micro += MICRO
            row.q_yes_micro += MICRO
            await checker.check(uow, row)

            position.shares_micro += 1
            with pytest.raises(InvariantViolationError) as violation:
                await checker.check(uow, row)
        # Keep nothing of it: the block then commits an empty transaction.
        await uow.session.rollback()

    assert violation.value.invariant == "positions_match_market"
    assert violation.value.context["q_yes_micro"] == HELD + MICRO
    assert violation.value.context["positions_yes_micro"] == HELD + MICRO + 1
    await check(container.uow_factory, market)
    await assert_books_balance(container.uow_factory)
