"""The books, checked from outside (SPEC §1, §5, §12, Appendix A.4, A.8).

A test of anything that moves credits ends with

    await assert_books_balance(container.uow_factory)

which reads the whole database and holds it against the rules no operation may break:

    ledger_nets_to_zero      all accounts sum to zero: no credit was made or lost
    entry_sums_to_zero       every journal entry sums to zero
    positions_match_market   a market's q is the sum of the shares its positions hold
    balance_not_below_zero   no floor is broken: a user, a market and the two voucher
                             accounts are never below zero
    market_within_budget     a market that is not settled could not lose more than its
                             budget, whichever outcome wins

The first, third and last are what the circuit breaker checks for the market of a trade
(`InvariantChecker`), and the breaker's user-balance check is the fourth. Here they are
checked for every account and every market at once, from the rows as they are, so a
test finds a broken book whichever service broke it.

The rules are a pure function, `violations`, of what `read_books` found. A test that
wants to see more than pass or fail reads the list.

`replay` proves something the rules above cannot: that the trades of a market were
priced one after another, each on the quantities the one before it left.

Call these when nothing else is writing. The book is read with several statements, and
a trade that commits between two of them shows in one and not in the other.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import func, select

from app.core.enums import AccountKind, Outcome, TradeSide
from app.db.base import Base
from app.models import Account, Market, Position, Posting, Trade
from app.pricing import lmsr
from app.repositories.base import sum_micro
from app.services import UowFactory
from app.services.market_view import UNSETTLED_STATUSES

FLOORED_KINDS: Final[frozenset[AccountKind]] = frozenset(
    {
        AccountKind.USER,
        AccountKind.MARKET,
        AccountKind.VIS_REDEMPTION_PENDING,
        AccountKind.VIS_REDEEMED,
    }
)
"""The accounts that never go below zero (A.8). `vis:issuance` is where every credit
comes from and `vis:mm_pnl` takes the market maker's losses: both may."""


@dataclass(frozen=True, slots=True)
class Books:
    """Everything the rules are about, as `read_books` found it."""

    accounts: Sequence[Account]
    """Every account, with its balance."""

    entry_totals_micro: Mapping[UUID, int]
    """Per journal entry that has postings, what they sum to."""

    markets: Sequence[Market]
    """Every market, in every status."""

    shares_held_micro: Mapping[tuple[UUID, Outcome], int]
    """Per market and outcome that somebody has a position in, the shares held."""


async def read_books(uow_factory: UowFactory) -> Books:
    """The accounts, entries, markets and positions of the whole database."""
    async with uow_factory() as uow:
        session = uow.session
        accounts = (await session.scalars(select(Account).order_by(Account.id))).all()
        entry_totals = await session.execute(
            select(Posting.entry_id, sum_micro(Posting.amount_micro)).group_by(Posting.entry_id)
        )
        markets = (await session.scalars(select(Market).order_by(Market.id))).all()
        shares_held = await session.execute(
            select(Position.market_id, Position.outcome, sum_micro(Position.shares_micro)).group_by(
                Position.market_id, Position.outcome
            )
        )
        return Books(
            accounts=accounts,
            entry_totals_micro=dict(entry_totals.all()),
            markets=markets,
            shares_held_micro={
                (market_id, outcome): shares for market_id, outcome, shares in shares_held
            },
        )


def violations(books: Books) -> list[str]:
    """Every rule `books` breaks, one line each, starting with the name of the rule.
    Empty when the books balance."""
    found: list[str] = []

    total_micro = sum(account.balance_micro for account in books.accounts)
    if total_micro != 0:
        found.append(f"ledger_nets_to_zero: all accounts sum to {total_micro} µCR, not to 0")

    for entry_id in sorted(books.entry_totals_micro):
        entry_total_micro = books.entry_totals_micro[entry_id]
        if entry_total_micro != 0:
            found.append(
                f"entry_sums_to_zero: journal entry {entry_id} sums to {entry_total_micro} µCR"
            )

    for account in books.accounts:
        if account.kind in FLOORED_KINDS and account.balance_micro < 0:
            found.append(
                f"balance_not_below_zero: the {account.kind} account {account.id} "
                f"is at {account.balance_micro} µCR"
            )

    market_balances = {
        account.market_id: account.balance_micro
        for account in books.accounts
        if account.kind is AccountKind.MARKET
    }
    for market in books.markets:
        q_micro = (market.q_yes_micro, market.q_no_micro)
        held_micro = (
            books.shares_held_micro.get((market.id, Outcome.YES), 0),
            books.shares_held_micro.get((market.id, Outcome.NO), 0),
        )
        if q_micro != held_micro:
            found.append(
                f"positions_match_market: market {market.id} has q = {q_micro} µshares "
                f"(YES, NO), its positions hold {held_micro}"
            )
        if market.status not in UNSETTLED_STATUSES:
            continue
        # What VIS would have to add if the more heavily held outcome won.
        exposure_micro = max(q_micro) - market_balances.get(market.id, 0)
        if exposure_micro > market.budget_micro:
            found.append(
                f"market_within_budget: market {market.id} could lose {exposure_micro} µCR, "
                f"its budget is {market.budget_micro} µCR"
            )
    return found


async def assert_books_balance(uow_factory: UowFactory) -> None:
    """Fail with every broken rule if the database breaks one. See the module docstring."""
    found = violations(await read_books(uow_factory))
    if found:
        raise AssertionError("the books do not balance:\n  " + "\n  ".join(found))


def replay(start: lmsr.MarketState, trades: Iterable[Trade], *, precision: int) -> lmsr.MarketState:
    """Price `trades` again, one after another, and return the state they end in.

    `start` is the market before the first of them: its b, and q as it was then (zero
    for a market's whole history). The trades are taken in the order of their
    `market_version`, whatever order they come in. Each is priced by `app.pricing.lmsr`
    on the state the one before it left, at `precision` digits, as a buy or a sell of
    its shares.

    Fails for the first trade whose amount or whose YES price before or after it is
    not what lmsr gives there, and for a version that does not follow the one before
    it. When every trade passes, they ran one after another on fresh quantities: a
    trade priced on stale ones would not reproduce.

    A buy sized by an amount reproduces too: it costs exactly what its shares cost
    (`lmsr.quote_buy_for_budget`).

    Compare the result with the market's own q to prove that nothing but these trades
    moved it.
    """
    state = start
    previous_version: int | None = None
    for trade in sorted(trades, key=lambda trade: trade.market_version):
        if previous_version is not None and trade.market_version != previous_version + 1:
            raise AssertionError(
                f"trade {trade.id} has version {trade.market_version}, "
                f"which does not follow {previous_version}"
            )
        previous_version = trade.market_version
        price = lmsr.quote_buy_shares if trade.side is TradeSide.BUY else lmsr.quote_sell_shares
        quote = price(state, trade.outcome, trade.shares_micro, precision=precision)
        recorded = (trade.amount_micro, trade.p_yes_before_micro, trade.p_yes_after_micro)
        priced = (quote.amount_micro, quote.p_yes_before_micro, quote.p_yes_after_micro)
        if recorded != priced:
            raise AssertionError(
                f"trade {trade.id} (version {trade.market_version}) recorded "
                f"(amount, p before, p after) = {recorded}; priced on the state before it, "
                f"{trade.shares_micro} µshares give {priced}"
            )
        state = quote.after
    return state


async def count_rows(uow_factory: UowFactory, model: type[Base]) -> int:
    """How many rows the table of `model` holds."""
    async with uow_factory() as uow:
        rows = await uow.session.scalar(select(func.count()).select_from(model))
    assert rows is not None
    return rows


async def system_balance(uow_factory: UowFactory, kind: AccountKind) -> int:
    """The balance of one of the four VIS_* accounts, in µCR."""
    async with uow_factory() as uow:
        return (await uow.accounts.get_system_account(kind)).balance_micro
