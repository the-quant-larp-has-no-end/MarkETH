"""The leaderboard, from the stored rows to the answer (SPEC §10, Appendix A.2).

`LeaderboardService.ranking` and the one statement behind it
(`LeaderboardRepository.rank`) run here on a real Postgres. The statement reads rows, so
the tests put rows in place: buys are made by the factory, and a sale, a resolution and
a void by `Desk`, each with the ledger entry it books. No rule of trading or of settling
a market is applied on the way; those have tests of their own.

The rules are proven three ways:

* The league: four traders whose figures are worked out by hand in the docstring of the
  `league` fixture. Its tests say what they expect in so many words: who comes first
  under each order, whom each bound keeps.
* Scenes: a few rows that show one rule each, such as a sale before the resolution, a
  market nobody won, a void, a tie.
* The census: a larger population, and Hypothesis asking any query about it. The answer
  has to be the one `answer_of` works out in Python from the rows as they are stored.

What a figure should be is read from the stored rows (`standings`), with the payout of a
position taken from `MarketView`; nothing here prices a trade a second time.
"""

from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal
from itertools import groupby
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import event, select

from app.container import Container
from app.core.degrees import CATALOGUE
from app.core.enums import (
    AccountKind,
    DegreeLevel,
    JournalKind,
    LeaderboardSort,
    MarketStatus,
    Outcome,
    ResolutionOutcome,
    SortOrder,
    TradeSide,
    UserStatus,
)
from app.core.errors import ValidationFailedError
from app.core.units import MICRO, display_probability, format_cr, to_cr, to_micro
from app.models import Account, Market, Position, Resolution, Trade, User
from app.pricing import lmsr
from app.repositories.leaderboard import LeaderboardFilter, LeaderboardRow
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.leaderboard import (
    MEASURES_WITH_BOUNDS,
    PAYOUT_RATIO_FILTER_MAX,
    TRADES_FILTER_MAX,
    LeaderboardEntry,
    LeaderboardQuery,
    LeaderboardResponse,
)
from app.services.leaderboard import CURSOR_MAX, PAYOUT_RATIO_PLACES
from app.services.ledger import LedgerRef, PostingLine, resolution_key, trade_key, void_key
from tests.support.books import assert_books_balance
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import ActAs, Factory

pytestmark = pytest.mark.integration

LEADERBOARD: Final[str] = "/api/leaderboard"

MINUTE: Final[timedelta] = timedelta(minutes=1)
HOUR: Final[timedelta] = timedelta(hours=1)

BIGINT_MAX: Final[int] = 2**63 - 1

BACHELOR: Final[DegreeLevel] = DegreeLevel.BACHELOR
MASTER: Final[DegreeLevel] = DegreeLevel.MASTER
DOCTORATE: Final[DegreeLevel] = DegreeLevel.DOCTORATE
COMPUTER_SCIENCE_BSC: Final[str] = "BSc Computer Science"
MATHEMATICS_BSC: Final[str] = "BSc Mathematics"
COMPUTER_SCIENCE_MSC: Final[str] = "MSc Computer Science"
PHYSICS_DOCTORATE: Final[str] = "Doctorate Physics"
PROGRAMME_WITH_A_DASH: Final[str] = next(
    label for label in CATALOGUE[0].programmes if not label.isascii()
)
"""A Bachelor label ETH writes with an en dash: it has to match as it is stored."""

NOBODYS_PROGRAMME: Final[str] = "MSc Underwater Basket Weaving"

NUL: Final[str] = "\x00"
"""A character a query string can carry and no text of Postgres can hold."""

checks = settings(
    max_examples=100,
    deadline=None,
    # The examples of one test share its rows on purpose: a fixture writes them once and
    # every example only reads them.
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def cr(amount: int | str) -> int:
    """An amount of credits or shares in micro-units: `cr(30)`, `cr("7.5")`."""
    return to_micro(amount)


# --- Trades and settlements, written as rows ------------------------------------------


class Desk:
    """Trades and settlements as a trade, a resolution and a void leave them behind,
    written as rows and with the books kept straight.

    A buy goes through the factory. A sale, a resolution and a void are put in place
    here: the `Trade` and the `Position` of a sale, the `Resolution` of a settlement,
    the status of the market, and the one ledger entry each of them books (A.8),
    posted by `LedgerService` like every other credit movement. No rule of the
    trading or the lifecycle service is applied: no stake limit, no deadline, no
    check of who may settle.

    Every market is proposed by the host, a suspended user. A suspended user is not
    ranked, so a ranking lists exactly the people a test names.
    """

    def __init__(self, container: Container, factory: Factory) -> None:
        self._container = container
        self._factory = factory
        self._host: User | None = None

    async def market(
        self, *, status: MarketStatus = MarketStatus.OPEN, b_micro: int | None = None
    ) -> Market:
        """A market with nobody in it yet, of the default tier unless `b_micro` is given."""
        if self._host is None:
            self._host = await self._factory.user(username="host", status=UserStatus.SUSPENDED)
        return await self._factory.market(status=status, b_micro=b_micro, created_by=self._host)

    async def buy(
        self,
        user: User,
        market: Market,
        outcome: Outcome,
        shares_micro: int,
        cost_micro: int | None = None,
    ) -> None:
        """One buy. `cost_micro` is what the user is charged: the price of the shares
        when left out, never less than that."""
        await self._factory.position(
            user, market, outcome, shares_micro=shares_micro, cost_micro=cost_micro
        )

    async def sell(self, user: User, market: Market, outcome: Outcome, shares_micro: int) -> None:
        """One sale at the market's price: the proceeds move from the market's account
        to the user's, the position gives up the shares and records the proceeds, and
        the market's quantities, version and volume follow."""
        now = self._container.clock.now()
        precision = self._container.settings.pricing.decimal_precision
        async with self._container.uow_factory() as uow:
            row = await uow.markets.get_for_update(market.id)
            account = await uow.accounts.get_user_account(user.id, for_update=True)
            market_account = await uow.accounts.get_market_account(market.id)
            position = await uow.positions.get(user.id, market.id, outcome, for_update=True)
            assert row is not None
            assert account is not None
            assert market_account is not None
            assert position is not None
            assert position.shares_micro >= shares_micro
            state = lmsr.MarketState(q_yes=row.q_yes_micro, q_no=row.q_no_micro, b=row.b_micro)
            quote = lmsr.quote_sell_shares(state, outcome, shares_micro, precision=precision)

            trade_id = uuid7()
            if quote.amount_micro:
                await self._container.ledger.post(
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

    async def resolve(self, market: Market, outcome: Outcome) -> None:
        """RESOLVED to `outcome`: every share of it pays 1 CR, the market's account is
        emptied and the difference goes to `vis:mm_pnl`. The positions keep their
        shares, cost and proceeds (A.5)."""
        view = self._container.market_view
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            resolution = await uow.resolutions.add(
                Resolution(
                    market_id=row.id,
                    outcome=ResolutionOutcome(outcome.value),
                    evidence="Settled in a test.",
                    resolved_by=row.created_by,
                    created_at=now,
                )
            )
            positions = await uow.positions.list_for_market(row.id)
            owed = {
                user_id: view.payout_micro(held, resolution) or 0
                for user_id, held in _by_user(positions)
            }
            await self._settle(
                uow, row, JournalKind.MARKET_RESOLUTION, resolution_key(row.id), owed
            )
            row.status = MarketStatus.RESOLVED
            row.closed_at = now
        market.status = MarketStatus.RESOLVED

    async def void(self, market: Market) -> None:
        """VOID: every net stake is refunded, the shares of every position and the
        market's quantities go to zero, cost and proceeds stay (A.5)."""
        view = self._container.market_view
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            row = await uow.markets.get_for_update(market.id)
            assert row is not None
            resolution = await uow.resolutions.add(
                Resolution(
                    market_id=row.id,
                    outcome=ResolutionOutcome.VOID,
                    evidence="Voided in a test.",
                    resolved_by=row.created_by,
                    created_at=now,
                )
            )
            positions = await uow.positions.list_for_market(row.id)
            owed = {
                user_id: view.refund_micro(held, resolution) or 0
                for user_id, held in _by_user(positions)
            }
            await self._settle(uow, row, JournalKind.MARKET_VOID, void_key(row.id), owed)
            for position in positions:
                position.shares_micro = 0
            row.q_yes_micro = 0
            row.q_no_micro = 0
            row.status = MarketStatus.VOID
            row.closed_at = now
        market.status = MarketStatus.VOID
        market.q_yes_micro = 0
        market.q_no_micro = 0

    async def _settle(
        self,
        uow: UnitOfWork,
        market: Market,
        kind: JournalKind,
        idempotency_key: str,
        owed_micro: Mapping[UUID, int],
    ) -> None:
        """The one entry of a settlement: each user their due, the market's account
        emptied, the rest to `vis:mm_pnl`. Nothing is posted when nothing moves."""
        paid = {user_id: amount for user_id, amount in owed_micro.items() if amount}
        accounts = await uow.accounts.lock_user_accounts(list(paid))
        market_account = await uow.accounts.get_market_account(market.id)
        pnl = await uow.accounts.get_system_account(AccountKind.VIS_MM_PNL)
        assert market_account is not None
        balance_micro = market_account.balance_micro
        lines = [
            PostingLine(account.id, paid[account.owner_id])
            for account in accounts
            if account.owner_id is not None
        ]
        lines.append(PostingLine(market_account.id, -balance_micro))
        lines.append(PostingLine(pnl.id, balance_micro - sum(paid.values())))
        if any(line.amount_micro for line in lines):
            await self._container.ledger.post(
                uow,
                kind=kind,
                ref_type=LedgerRef.MARKET,
                ref_id=market.id,
                idempotency_key=idempotency_key,
                lines=lines,
                at=self._container.clock.now(),
            )


def _by_user(positions: Sequence[Position]) -> list[tuple[UUID, list[Position]]]:
    """The positions of a market per user. `list_for_market` hands them over sorted by
    user."""
    return [(user_id, list(held)) for user_id, held in groupby(positions, lambda row: row.user_id)]


@pytest.fixture
def desk(container: Container, factory: Factory) -> Desk:
    return Desk(container, factory)


# --- The figures, worked out from the stored rows ------------------------------------


@dataclass(frozen=True, slots=True)
class Figures:
    """A user's measures in integers. A rate is in millionths, and None for a user
    without a settled market."""

    credits_micro: int
    trades: int
    settled_markets: int
    win_rate_micro: int | None
    avg_payout_ratio_micro: int | None


@dataclass(frozen=True, slots=True)
class Standing:
    """A user and the figures the stored rows give them."""

    user: User
    figures: Figures


def figures_of(row: LeaderboardRow) -> Figures:
    return Figures(
        credits_micro=row.credits_micro,
        trades=row.trades,
        settled_markets=row.settled_markets,
        win_rate_micro=row.win_rate_micro,
        avg_payout_ratio_micro=row.avg_payout_ratio_micro,
    )


async def standings(container: Container) -> list[Standing]:
    """Every ACTIVE user with their figures, worked out in Python from the rows as they
    are stored, by the definitions in the docstring of `LeaderboardService.ranking`.

    In the order of registration. What a resolved position paid is `MarketView`'s
    answer, so the figures rest on the same reading of a position as the portfolio.
    """
    view = container.market_view
    async with container.uow_factory() as uow:
        session = uow.session
        users = (await session.scalars(select(User).order_by(User.created_at, User.id))).all()
        accounts = (
            await session.scalars(select(Account).where(Account.kind == AccountKind.USER))
        ).all()
        trades = Counter((await session.scalars(select(Trade.user_id))).all())
        positions = (await session.scalars(select(Position))).all()
        markets = {market.id: market for market in (await session.scalars(select(Market))).all()}
        resolutions = {
            resolution.market_id: resolution
            for resolution in (await session.scalars(select(Resolution))).all()
        }
    balances = {account.owner_id: account.balance_micro for account in accounts}
    held: defaultdict[UUID, defaultdict[UUID, list[Position]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for position in positions:
        held[position.user_id][position.market_id].append(position)

    result: list[Standing] = []
    for user in users:
        if user.status is not UserStatus.ACTIVE:
            continue
        settled = wins = cost_micro = returned_micro = 0
        for market_id, rows in held[user.id].items():
            put_in = sum(row.cost_micro for row in rows)
            if markets[market_id].status is not MarketStatus.RESOLVED or put_in == 0:
                continue
            payout = view.payout_micro(rows, resolutions[market_id])
            assert payout is not None
            got_back = sum(row.proceeds_micro for row in rows) + payout
            settled += 1
            wins += got_back > put_in
            cost_micro += put_in
            returned_micro += got_back
        figures = Figures(
            credits_micro=balances[user.id],
            trades=trades[user.id],
            settled_markets=settled,
            win_rate_micro=wins * MICRO // settled if settled else None,
            avg_payout_ratio_micro=returned_micro * MICRO // cost_micro if settled else None,
        )
        result.append(Standing(user=user, figures=figures))
    return result


async def figures_from_the_rows(container: Container) -> dict[str, Figures]:
    """`standings`, by username."""
    return {standing.user.username: standing.figures for standing in await standings(container)}


async def figures_by_name(container: Container) -> dict[str, Figures]:
    """The figures of every listed user as `LeaderboardRepository.rank` reads them."""
    async with container.uow_factory() as uow:
        rows = await uow.leaderboard.rank(
            sort=LeaderboardSort.CREDITS,
            order=SortOrder.DESC,
            filters=LeaderboardFilter(),
            after_rank=0,
            limit=MAX_PAGE_SIZE,
            viewer_id=uuid7(),
        )
    return {row.username: figures_of(row) for row in rows}


async def stored_positions(container: Container, user: User, market: Market) -> list[Position]:
    """A user's positions in one market, as they are stored now."""
    async with container.uow_factory() as uow:
        return list(await uow.positions.list_for_user_market(user.id, market.id))


async def ask(
    container: Container, factory: Factory, viewer: User, **query: object
) -> LeaderboardResponse:
    """`GET /leaderboard` as `viewer`, with the query a client would send."""
    return await container.leaderboard.ranking(
        factory.identity(viewer), LeaderboardQuery.model_validate(query)
    )


def names(answer: LeaderboardResponse) -> list[str]:
    return [item.username for item in answer.items]


def ranks(answer: LeaderboardResponse) -> list[int]:
    return [item.rank for item in answer.items]


def entry_named(answer: LeaderboardResponse, username: str) -> LeaderboardEntry:
    (entry,) = [item for item in answer.items if item.username == username]
    return entry


# --- The league: four traders whose figures are worked out by hand -------------------


@dataclass(frozen=True, slots=True)
class League:
    ada: User
    ben: User
    cleo: User
    dan: User


LEAGUE_FIGURES: Final[Mapping[str, Figures]] = {
    "ada": Figures(
        credits_micro=cr(108),
        trades=3,
        settled_markets=3,
        win_rate_micro=333_333,
        avg_payout_ratio_micro=1_363_636,
    ),
    "ben": Figures(
        credits_micro=cr(98),
        trades=1,
        settled_markets=1,
        win_rate_micro=1_000_000,
        avg_payout_ratio_micro=1_666_666,
    ),
    "cleo": Figures(
        credits_micro=cr(86),
        trades=4,
        settled_markets=3,
        win_rate_micro=666_666,
        avg_payout_ratio_micro=222_222,
    ),
    "dan": Figures(
        credits_micro=cr(494),
        trades=2,
        settled_markets=1,
        win_rate_micro=0,
        avg_payout_ratio_micro=0,
    ),
}
"""The table of the `league` fixture, in micro-units."""

LEAGUE_ORDERS: Final[Mapping[LeaderboardSort, Sequence[str]]] = {
    LeaderboardSort.CREDITS: ("dan", "ada", "ben", "cleo"),
    LeaderboardSort.WIN_RATE: ("ben", "cleo", "ada", "dan"),
    LeaderboardSort.TRADES: ("cleo", "ada", "dan", "ben"),
    LeaderboardSort.AVG_PAYOUT_RATIO: ("ben", "ada", "cleo", "dan"),
}
"""The league under each measure, highest first, as that table gives it."""


@pytest.fixture
async def league(desk: Desk, factory: Factory, clock: FrozenClock) -> League:
    """Four traders, registered a minute apart, in four markets that are all resolved.

    `won_a` and `won_b` resolve YES, `lost_a` and `lost_b` resolve NO. Everybody buys
    YES at a cost the fixture sets, so a share of a `won` market returns 1 CR and a
    share of a `lost` one nothing, and every figure can be worked out by hand:

        trader  starts  buys: shares for credits           put in  back  wins  ends
        ada        100  won_a 30 for 18, lost_a 2 for 2,       22    30   1/3   108
                        lost_b 2 for 2
        ben         90  won_a 20 for 12                        12    20   1/1    98
        cleo       100  won_a 2 for 1.5, won_b 2 for 1.5,      18     4   2/3    86
                        lost_a 10 for 7.5, twice
        dan        500  lost_a 5 for 3, twice                   6     0   0/1   494

        win rate      ada 0.333333    ben 1           cleo 0.666666    dan 0
        payout ratio  ada 30 / 22     ben 20 / 12     cleo 4 / 18      dan 0
                          = 1.363636      = 1.666666       = 0.222222
        trades        ada 3           ben 1           cleo 4           dan 2

    The four measures put them in four different orders, highest first:

        credits       dan, ada, ben, cleo
        win rate      ben, cleo, ada, dan
        trades        cleo, ada, dan, ben
        payout ratio  ben, ada, cleo, dan
    """
    ada = await factory.user(
        username="ada",
        balance_micro=cr(100),
        degree_level=BACHELOR,
        degree_programme=COMPUTER_SCIENCE_BSC,
    )
    clock.advance(MINUTE)
    ben = await factory.user(
        username="ben",
        balance_micro=cr(90),
        degree_level=MASTER,
        degree_programme=COMPUTER_SCIENCE_MSC,
    )
    clock.advance(MINUTE)
    cleo = await factory.user(
        username="cleo",
        balance_micro=cr(100),
        degree_level=BACHELOR,
        degree_programme=MATHEMATICS_BSC,
    )
    clock.advance(MINUTE)
    dan = await factory.user(
        username="dan",
        balance_micro=cr(500),
        degree_level=DOCTORATE,
        degree_programme=PHYSICS_DOCTORATE,
    )
    clock.advance(MINUTE)

    won_a, won_b, lost_a, lost_b = [await desk.market() for _ in range(4)]
    await desk.buy(ada, won_a, Outcome.YES, cr(30), cr(18))
    await desk.buy(ada, lost_a, Outcome.YES, cr(2), cr(2))
    await desk.buy(ada, lost_b, Outcome.YES, cr(2), cr(2))
    await desk.buy(ben, won_a, Outcome.YES, cr(20), cr(12))
    await desk.buy(cleo, won_a, Outcome.YES, cr(2), cr("1.5"))
    await desk.buy(cleo, won_b, Outcome.YES, cr(2), cr("1.5"))
    await desk.buy(cleo, lost_a, Outcome.YES, cr(10), cr("7.5"))
    await desk.buy(cleo, lost_a, Outcome.YES, cr(10), cr("7.5"))
    await desk.buy(dan, lost_a, Outcome.YES, cr(5), cr(3))
    await desk.buy(dan, lost_a, Outcome.YES, cr(5), cr(3))
    for market in (won_a, won_b):
        await desk.resolve(market, Outcome.YES)
    for market in (lost_a, lost_b):
        await desk.resolve(market, Outcome.NO)
    return League(ada=ada, ben=ben, cleo=cleo, dan=dan)


async def test_the_figures_of_the_league_are_the_ones_worked_out_by_hand(
    league: League, container: Container
) -> None:
    """The statement, the stored rows and the table of the fixture say the same."""
    assert await figures_by_name(container) == LEAGUE_FIGURES
    assert await figures_from_the_rows(container) == LEAGUE_FIGURES
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize("order", list(SortOrder))
@pytest.mark.parametrize("sort", list(LeaderboardSort))
async def test_each_measure_orders_the_league_in_both_directions(
    league: League,
    container: Container,
    factory: Factory,
    sort: LeaderboardSort,
    order: SortOrder,
) -> None:
    highest_first = list(LEAGUE_ORDERS[sort])
    other_orders = [list(LEAGUE_ORDERS[other]) for other in LeaderboardSort if other is not sort]
    # No other measure gives this order, so ordering by the wrong column would show.
    assert highest_first not in other_orders

    answer = await ask(container, factory, league.ada, sort=sort, order=order)

    assert names(answer) == (highest_first if order is SortOrder.DESC else highest_first[::-1])
    assert ranks(answer) == [1, 2, 3, 4]
    assert answer.next_cursor is None
    await assert_books_balance(container.uow_factory)


async def test_the_api_sends_the_league_by_credits_with_rounded_win_rates_and_cut_ratios(
    league: League, container: Container, client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """Without a query the richest comes first. Credits are decimal strings and the two
    rates JSON numbers. A win rate is rounded like every probability: two wins in three
    markets, 0.666666, go up. A payout ratio is cut to two places: twenty credits back
    for twelve, 1.666666, are sent as 1.66 and not as 1.67."""
    decimals = container.settings.pricing.price_display_decimals
    as_user(league.cleo)

    def row(rank: int, username: str, level: str, programme: str, credits_cr: str) -> Any:
        figures = LEAGUE_FIGURES[username]
        assert figures.win_rate_micro is not None
        return {
            "rank": rank,
            "username": username,
            "degree_level": level,
            "degree_programme": programme,
            "credits_cr": credits_cr,
            "trades": figures.trades,
            "settled_markets": figures.settled_markets,
            "win_rate": display_probability(figures.win_rate_micro, decimals),
            "avg_payout_ratio": {"ada": 1.36, "ben": 1.66, "cleo": 0.22, "dan": 0.0}[username],
            "is_viewer": username == "cleo",
        }

    response = await client.get(LEADERBOARD)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            row(1, "dan", "DOCTORATE", PHYSICS_DOCTORATE, "494"),
            row(2, "ada", "BACHELOR", COMPUTER_SCIENCE_BSC, "108"),
            row(3, "ben", "MASTER", COMPUTER_SCIENCE_MSC, "98"),
            row(4, "cleo", "BACHELOR", MATHEMATICS_BSC, "86"),
        ],
        "next_cursor": None,
        "viewer": row(4, "cleo", "BACHELOR", MATHEMATICS_BSC, "86"),
    }
    for sent in response.json()["items"]:
        assert isinstance(sent["credits_cr"], str)
        assert isinstance(sent["win_rate"], float)
        assert isinstance(sent["avg_payout_ratio"], float)
    await assert_books_balance(container.uow_factory)


async def test_the_route_orders_filters_and_pages_as_the_query_string_says(
    league: League, container: Container, client: httpx.AsyncClient, as_user: ActAs
) -> None:
    """From the text of a query string to the rows. A win rate of at most 0.666666 and
    at least 86 credits leave cleo, ada and dan, in that order by trades; the second
    page of one holds ada."""
    as_user(league.cleo)

    response = await client.get(
        LEADERBOARD,
        params={
            "sort": "trades",
            "order": "desc",
            "max_win_rate": "0.666666",
            "min_credits_cr": "86",
            "limit": "1",
            "cursor": "1",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert [(row["rank"], row["username"]) for row in body["items"]] == [(2, "ada")]
    assert body["next_cursor"] == "2"
    assert (body["viewer"]["rank"], body["viewer"]["username"]) == (1, "cleo")
    await assert_books_balance(container.uow_factory)


# --- Filters -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bound", "on_the_bound", "kept", "a_step_beyond", "still_kept"),
    [
        ("min_credits_cr", "98", ["dan", "ada", "ben"], "98.000001", ["dan", "ada"]),
        ("max_credits_cr", "98", ["ben", "cleo"], "97.999999", ["cleo"]),
        ("min_trades", 3, ["ada", "cleo"], 4, ["cleo"]),
        ("max_trades", 2, ["dan", "ben"], 1, ["ben"]),
        ("min_win_rate", 0.666666, ["ben", "cleo"], 0.666667, ["ben"]),
        ("max_win_rate", 0.333333, ["dan", "ada"], 0.333332, ["dan"]),
        ("min_avg_payout_ratio", 1.363636, ["ada", "ben"], 1.363637, ["ben"]),
        ("max_avg_payout_ratio", 1.363636, ["dan", "ada", "cleo"], 1.363635, ["dan", "cleo"]),
    ],
    ids=[f"{side}_{measure}" for measure in MEASURES_WITH_BOUNDS for side in ("min", "max")],
)
async def test_each_bound_keeps_who_is_exactly_on_it_and_drops_them_one_step_beyond(
    league: League,
    container: Container,
    factory: Factory,
    bound: str,
    on_the_bound: object,
    kept: list[str],
    a_step_beyond: object,
    still_kept: list[str],
) -> None:
    """Every bound is inclusive, and compares the exact figure: a millionth of a
    credit, one trade, a millionth of a rate decide."""
    inclusive = await ask(container, factory, league.ben, **{bound: on_the_bound})
    exclusive = await ask(container, factory, league.ben, **{bound: a_step_beyond})

    assert names(inclusive) == kept
    assert names(exclusive) == still_kept
    assert ranks(inclusive) == list(range(1, len(kept) + 1))
    assert ranks(exclusive) == list(range(1, len(still_kept) + 1))
    await assert_books_balance(container.uow_factory)


async def test_a_rate_is_filtered_by_its_exact_value_not_by_the_one_a_row_shows(
    league: League, container: Container, factory: Factory
) -> None:
    """Ben's row shows a payout ratio of 1.66; what he has is 1.666666."""
    shown = entry_named(await ask(container, factory, league.ada), "ben").avg_payout_ratio
    assert shown == 1.66

    at_most_the_shown = await ask(container, factory, league.ada, max_avg_payout_ratio=shown)
    at_least_the_shown = await ask(container, factory, league.ada, min_avg_payout_ratio=shown)
    above_the_shown = await ask(container, factory, league.ada, min_avg_payout_ratio=1.67)

    assert "ben" not in names(at_most_the_shown)
    assert names(at_least_the_shown) == ["ben"]
    assert names(above_the_shown) == []
    await assert_books_balance(container.uow_factory)


async def test_a_bound_finer_than_a_millionth_decides_as_the_exact_numbers_do(
    league: League, container: Container, factory: Factory
) -> None:
    """Cleo's win rate is 0.666666 and Ada's 0.333333. Half a millionth more is above
    what they have, half a millionth less is below it: a lower bound is not rounded down
    onto them, and an upper bound is not rounded up onto them."""
    at_least_a_hair_more = await ask(container, factory, league.ada, min_win_rate=0.6666665)
    at_least_a_hair_less = await ask(container, factory, league.ada, min_win_rate=0.6666655)
    at_most_a_hair_less = await ask(container, factory, league.ada, max_win_rate=0.3333325)
    at_most_a_hair_more = await ask(container, factory, league.ada, max_win_rate=0.3333335)

    assert names(at_least_a_hair_more) == ["ben"]
    assert names(at_least_a_hair_less) == ["ben", "cleo"]
    assert names(at_most_a_hair_less) == ["dan"]
    assert names(at_most_a_hair_more) == ["dan", "ada"]
    await assert_books_balance(container.uow_factory)


async def test_any_bound_on_a_rate_leaves_out_everybody_who_has_none(
    league: League, container: Container, factory: Factory
) -> None:
    """The widest bound there is still asks for a rate."""
    newcomer = await factory.user(username="newcomer", balance_micro=cr(50))
    widest_bounds: list[dict[str, object]] = [
        {"min_win_rate": 0},
        {"max_win_rate": 1},
        {"min_avg_payout_ratio": 0},
        {"max_avg_payout_ratio": PAYOUT_RATIO_FILTER_MAX},
    ]

    everyone = await ask(container, factory, newcomer)

    assert names(everyone) == ["dan", "ada", "ben", "cleo", "newcomer"]
    for bound in widest_bounds:
        rated = await ask(container, factory, newcomer, **bound)
        assert names(rated) == ["dan", "ada", "ben", "cleo"], bound
        assert rated.viewer is None, bound
    await assert_books_balance(container.uow_factory)


async def test_every_filter_that_is_given_must_hold(
    league: League, container: Container, factory: Factory
) -> None:
    two = await ask(container, factory, league.ada, min_trades=2, max_credits_cr="200")
    one = await ask(
        container, factory, league.ada, min_trades=2, max_credits_cr="200", min_win_rate=0.5
    )
    none = await ask(
        container,
        factory,
        league.ada,
        min_trades=2,
        max_credits_cr="200",
        min_win_rate=0.5,
        degree_level=MASTER,
    )

    assert names(two) == ["ada", "cleo"]
    assert names(one) == ["cleo"]
    assert names(none) == []
    await assert_books_balance(container.uow_factory)


async def test_a_degree_level_lists_its_users_and_ranks_them_among_themselves(
    league: League, container: Container, factory: Factory
) -> None:
    """The first of the Bachelor students is rank 1 of that list."""
    everyone = await ask(container, factory, league.cleo)
    bachelors = await ask(container, factory, league.cleo, degree_level=BACHELOR)

    assert [(item.rank, item.username) for item in everyone.items] == [
        (1, "dan"),
        (2, "ada"),
        (3, "ben"),
        (4, "cleo"),
    ]
    assert [(item.rank, item.username) for item in bachelors.items] == [(1, "ada"), (2, "cleo")]
    assert bachelors.viewer is not None
    assert bachelors.viewer.rank == 2
    await assert_books_balance(container.uow_factory)


async def test_a_degree_programme_lists_the_users_of_that_programme(
    league: League, container: Container, factory: Factory
) -> None:
    same_programme = await factory.user(
        username="grace",
        balance_micro=cr(90),
        degree_level=BACHELOR,
        degree_programme=MATHEMATICS_BSC,
    )

    answer = await ask(container, factory, same_programme, degree_programme=MATHEMATICS_BSC)

    assert [(item.rank, item.username) for item in answer.items] == [(1, "grace"), (2, "cleo")]
    await assert_books_balance(container.uow_factory)


async def test_a_level_and_a_programme_must_both_match(
    league: League, container: Container, factory: Factory
) -> None:
    both = await ask(
        container, factory, league.ada, degree_level=BACHELOR, degree_programme=MATHEMATICS_BSC
    )
    mismatch = await ask(
        container, factory, league.ada, degree_level=MASTER, degree_programme=MATHEMATICS_BSC
    )

    assert names(both) == ["cleo"]
    assert mismatch == LeaderboardResponse(items=[], next_cursor=None, viewer=None)
    await assert_books_balance(container.uow_factory)


async def test_a_degree_nobody_has_gives_an_empty_list_and_no_error(
    league: League, container: Container, factory: Factory
) -> None:
    """Neither value is checked against the catalogue."""
    no_programme = await ask(container, factory, league.ada, degree_programme=NOBODYS_PROGRAMME)
    no_level = await ask(container, factory, league.ada, degree_level=DegreeLevel.OTHER)

    assert no_programme == LeaderboardResponse(items=[], next_cursor=None, viewer=None)
    assert no_level == LeaderboardResponse(items=[], next_cursor=None, viewer=None)
    await assert_books_balance(container.uow_factory)


async def test_a_programme_with_a_nul_in_it_is_a_programme_nobody_has(
    league: League,
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """A query string can carry a NUL, no column holds one, and Postgres refuses even to
    compare with it. The answer is the one for a label nobody has, not an error of the
    database: asked through the route, which is where such a text comes from, and with
    everything else a query can say beside it."""
    as_user(league.ada)
    unstorable = f"BSc Computer{NUL}Science"

    alone = await client.get(LEADERBOARD, params={"degree_programme": unstorable})
    with_more = await client.get(
        LEADERBOARD,
        params={
            "degree_programme": unstorable,
            "degree_level": BACHELOR.value,
            "sort": LeaderboardSort.WIN_RATE.value,
            "min_trades": "1",
            "cursor": "1",
        },
    )

    for response in (alone, with_more):
        assert response.status_code == 200, response.text
        assert response.json() == {"items": [], "next_cursor": None, "viewer": None}
    asked_directly = await ask(container, factory, league.ada, degree_programme=NUL)
    assert asked_directly == LeaderboardResponse(items=[], next_cursor=None, viewer=None)
    # The rule about the cursor comes first, as for every other query.
    refused = await client.get(
        LEADERBOARD, params={"degree_programme": unstorable, "cursor": "second-page"}
    )
    assert (refused.status_code, refused.json()["details"]) == (422, {"field": "cursor"})
    await assert_books_balance(container.uow_factory)


async def test_a_programme_written_with_a_dash_matches_as_it_is_stored(
    league: League,
    container: Container,
    factory: Factory,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """Through the route, so the label makes the trip in a query string."""
    biochemist = await factory.user(
        username="bea",
        balance_micro=cr(10),
        degree_level=BACHELOR,
        degree_programme=PROGRAMME_WITH_A_DASH,
    )
    as_user(biochemist)

    response = await client.get(LEADERBOARD, params={"degree_programme": PROGRAMME_WITH_A_DASH})

    assert response.status_code == 200
    assert [(row["rank"], row["username"]) for row in response.json()["items"]] == [(1, "bea")]
    assert response.json()["items"][0]["degree_programme"] == PROGRAMME_WITH_A_DASH
    await assert_books_balance(container.uow_factory)


async def test_the_largest_bounds_a_query_may_name_are_compared_without_overflow(
    league: League, container: Container, factory: Factory
) -> None:
    """A ratio bound of a million is 10^12 millionths: more than an INTEGER holds."""
    answer = await ask(
        container,
        factory,
        league.ada,
        max_credits_cr="999999999999.999999",
        max_trades=TRADES_FILTER_MAX,
        max_win_rate=1,
        max_avg_payout_ratio=PAYOUT_RATIO_FILTER_MAX,
    )
    beyond_everything = await ask(
        container,
        factory,
        league.ada,
        min_credits_cr="999999999999.999999",
        min_trades=TRADES_FILTER_MAX,
        min_avg_payout_ratio=PAYOUT_RATIO_FILTER_MAX,
    )

    assert names(answer) == ["dan", "ada", "ben", "cleo"]
    assert names(beyond_everything) == []
    await assert_books_balance(container.uow_factory)


# --- The viewer's own row ------------------------------------------------------------


async def test_the_viewer_inside_the_page_is_marked_there_and_comes_back_as_viewer(
    league: League, container: Container, factory: Factory
) -> None:
    answer = await ask(container, factory, league.ada, limit=2)

    assert names(answer) == ["dan", "ada"]
    assert [item.is_viewer for item in answer.items] == [False, True]
    assert answer.viewer == answer.items[1]
    await assert_books_balance(container.uow_factory)


async def test_the_viewer_beyond_the_page_comes_back_with_their_rank(
    league: League, container: Container, factory: Factory
) -> None:
    first_page = await ask(container, factory, league.cleo, limit=2)
    second_page = await ask(container, factory, league.cleo, limit=2, cursor="2")

    assert names(first_page) == ["dan", "ada"]
    assert not any(item.is_viewer for item in first_page.items)
    assert first_page.viewer is not None
    assert (first_page.viewer.rank, first_page.viewer.username) == (4, "cleo")
    assert first_page.viewer.is_viewer
    # The row is the one the page that holds it shows.
    assert first_page.viewer == second_page.items[1]
    await assert_books_balance(container.uow_factory)


async def test_the_viewer_before_the_page_comes_back_with_their_rank(
    league: League, container: Container, factory: Factory
) -> None:
    second_page = await ask(container, factory, league.dan, limit=2, cursor="2")

    assert names(second_page) == ["ben", "cleo"]
    assert second_page.viewer is not None
    assert (second_page.viewer.rank, second_page.viewer.username) == (1, "dan")
    # Their row is one more than the page holds, and is not taken for a next page.
    assert second_page.next_cursor is None
    await assert_books_balance(container.uow_factory)


async def test_the_viewer_on_the_first_row_of_the_next_page_is_no_item_of_this_one(
    league: League, container: Container, factory: Factory
) -> None:
    """Ben is rank 3: the row read ahead to know whether a next page exists."""
    answer = await ask(container, factory, league.ben, limit=2)

    assert names(answer) == ["dan", "ada"]
    assert answer.next_cursor == "2"
    assert answer.viewer is not None
    assert (answer.viewer.rank, answer.viewer.username) == (3, "ben")
    await assert_books_balance(container.uow_factory)


async def test_the_viewer_is_ranked_in_the_list_that_was_asked_for(
    league: League, container: Container, factory: Factory
) -> None:
    """Same filters, same order, same ranks as the items."""
    by_credits = await ask(container, factory, league.cleo)
    by_trades = await ask(container, factory, league.cleo, sort="trades")
    fewest_trades_first = await ask(container, factory, league.cleo, sort="trades", order="asc")
    among_bachelors = await ask(container, factory, league.cleo, degree_level=BACHELOR, limit=1)

    assert by_credits.viewer is not None
    assert by_trades.viewer is not None
    assert fewest_trades_first.viewer is not None
    assert among_bachelors.viewer is not None
    assert by_credits.viewer.rank == 4
    assert by_trades.viewer.rank == 1
    assert fewest_trades_first.viewer.rank == 4
    assert among_bachelors.viewer.rank == 2
    assert names(among_bachelors) == ["ada"]
    await assert_books_balance(container.uow_factory)


async def test_the_viewer_is_null_when_the_filters_leave_them_out(
    league: League, container: Container, factory: Factory
) -> None:
    answer = await ask(container, factory, league.ada, degree_level=MASTER)

    assert names(answer) == ["ben"]
    assert not answer.items[0].is_viewer
    assert answer.viewer is None
    await assert_books_balance(container.uow_factory)


# --- Paging --------------------------------------------------------------------------


async def test_following_the_cursor_lists_every_user_once_in_rank_order(
    container: Container, factory: Factory
) -> None:
    """Seven users in pages of three: three pages, the last one short."""
    people = [
        await factory.user(username=f"player{place}", balance_micro=cr(80 - 10 * place))
        for place in range(1, 8)
    ]

    first = await ask(container, factory, people[0], limit=3)
    second = await ask(container, factory, people[0], limit=3, cursor=first.next_cursor)
    third = await ask(container, factory, people[0], limit=3, cursor=second.next_cursor)

    assert (first.next_cursor, second.next_cursor, third.next_cursor) == ("3", "6", None)
    assert [len(page.items) for page in (first, second, third)] == [3, 3, 1]
    listed = [*first.items, *second.items, *third.items]
    assert [item.username for item in listed] == [person.username for person in people]
    assert [item.rank for item in listed] == [1, 2, 3, 4, 5, 6, 7]
    await assert_books_balance(container.uow_factory)


async def test_the_last_page_has_no_next_cursor_also_when_it_is_full(
    league: League, container: Container, factory: Factory
) -> None:
    """The four of the league in pages of one to five: a page that ends exactly at the
    last rank is the last page."""
    page_sizes_by_limit = {1: [1, 1, 1, 1], 2: [2, 2], 3: [3, 1], 4: [4], 5: [4]}

    for limit, expected_sizes in page_sizes_by_limit.items():
        sizes: list[int] = []
        listed: list[str] = []
        cursor: str | None = None
        while True:
            page = await ask(container, factory, league.ada, limit=limit, cursor=cursor)
            sizes.append(len(page.items))
            listed += names(page)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert sizes == expected_sizes, limit
        assert listed == ["dan", "ada", "ben", "cleo"], limit
    await assert_books_balance(container.uow_factory)


async def test_a_cursor_of_zero_starts_at_the_first_rank(
    league: League, container: Container, factory: Factory
) -> None:
    assert await ask(container, factory, league.ada, cursor="0") == await ask(
        container, factory, league.ada
    )
    await assert_books_balance(container.uow_factory)


async def test_a_cursor_at_or_beyond_the_end_gives_an_empty_page_and_still_the_viewer(
    league: League, container: Container, factory: Factory
) -> None:
    for cursor in ("4", "5", str(CURSOR_MAX)):
        answer = await ask(container, factory, league.ada, cursor=cursor, limit=MAX_PAGE_SIZE)

        assert answer.items == [], cursor
        assert answer.next_cursor is None, cursor
        assert answer.viewer is not None, cursor
        assert (answer.viewer.rank, answer.viewer.username) == (2, "ada"), cursor
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "cursor",
    [
        "-1",
        "+1",
        "1.0",
        "1e3",
        "1_0",
        "0x10",
        "abc",
        "٣",
        "²",
        "1 2",
        str(CURSOR_MAX + 1),
    ],
    ids=[
        "negative",
        "signed",
        "a-decimal",
        "an-exponent",
        "an-underscore",
        "hexadecimal",
        "letters",
        "an-arabic-indic-digit",
        "a-superscript-digit",
        "two-numbers",
        "above-the-largest-rank",
    ],
)
async def test_a_cursor_that_is_no_rank_is_refused_and_names_the_field(
    container: Container, factory: Factory, cursor: str
) -> None:
    """Also what Python itself would read as a number: a sign, an underscore, a digit
    of another script."""
    viewer = await factory.user()

    with pytest.raises(ValidationFailedError) as refused:
        await ask(container, factory, viewer, cursor=cursor)

    assert refused.value.details == {"field": "cursor"}


async def test_a_cursor_that_is_no_rank_is_a_422_of_the_api(
    factory: Factory, client: httpx.AsyncClient, as_user: ActAs
) -> None:
    as_user(await factory.user())

    response = await client.get(LEADERBOARD, params={"cursor": "second-page"})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"] == {"field": "cursor"}


# --- Who is listed, and what counts --------------------------------------------------


async def test_a_user_who_never_traded_is_listed_with_no_trades_and_no_rates(
    container: Container, factory: Factory
) -> None:
    newcomer = await factory.user(username="newcomer", balance_micro=cr(500))

    answer = await ask(container, factory, newcomer)

    assert await figures_by_name(container) == {
        "newcomer": Figures(
            credits_micro=cr(500),
            trades=0,
            settled_markets=0,
            win_rate_micro=None,
            avg_payout_ratio_micro=None,
        )
    }
    assert answer.items == [
        LeaderboardEntry(
            rank=1,
            username="newcomer",
            degree_level=newcomer.degree_level,
            degree_programme=newcomer.degree_programme,
            credits_cr=to_cr(cr(500)),
            trades=0,
            settled_markets=0,
            win_rate=None,
            avg_payout_ratio=None,
            is_viewer=True,
        )
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_suspended_user_is_not_listed_and_takes_no_rank(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Also one who traded and won, and would be second by credits."""
    await factory.user(username="top", balance_micro=cr(300))
    banned = await factory.user(
        username="banned", balance_micro=cr(200), status=UserStatus.SUSPENDED
    )
    low = await factory.user(username="low", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(banned, market, Outcome.YES, cr(10), cr(6))
    await desk.resolve(market, Outcome.YES)

    answer = await ask(container, factory, low)

    assert [(item.rank, item.username) for item in answer.items] == [(1, "top"), (2, "low")]
    assert set(await figures_by_name(container)) == {"top", "low"}
    await assert_books_balance(container.uow_factory)


async def test_credits_are_the_balance_as_it_is_now(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """After a buy and after the payout of a resolution, each time the account's balance."""
    trader = await factory.user(username="trader", balance_micro=cr(100))
    market = await desk.market()

    await desk.buy(trader, market, Outcome.YES, cr(10), cr(6))
    after_the_buy = (await ask(container, factory, trader)).items[0].credits_cr
    balance_after_the_buy = await factory.balance_micro(trader)
    await desk.resolve(market, Outcome.YES)
    after_the_payout = (await ask(container, factory, trader)).items[0].credits_cr

    assert after_the_buy == to_cr(balance_after_the_buy) == to_cr(cr(94))
    assert after_the_payout == to_cr(await factory.balance_micro(trader)) == to_cr(cr(104))
    await assert_books_balance(container.uow_factory)


async def test_trades_are_counted_over_every_market_whatever_became_of_it(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Buys and sales alike, in a market that is open, one that was voided and one
    that resolved."""
    busy = await factory.user(username="busy", balance_micro=cr(200))
    still_open, voided, resolved = [await desk.market() for _ in range(3)]
    await desk.buy(busy, still_open, Outcome.YES, cr(10))
    await desk.buy(busy, still_open, Outcome.NO, cr(5))
    await desk.sell(busy, still_open, Outcome.YES, cr(4))
    await desk.buy(busy, voided, Outcome.YES, cr(10))
    await desk.buy(busy, resolved, Outcome.NO, cr(10))
    await desk.void(voided)
    await desk.resolve(resolved, Outcome.YES)

    figures = (await figures_by_name(container))["busy"]

    assert figures.trades == 5
    assert figures.settled_markets == 1
    assert await figures_by_name(container) == await figures_from_the_rows(container)
    await assert_books_balance(container.uow_factory)


@pytest.mark.parametrize(
    "status", [MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED, MarketStatus.VOID]
)
async def test_a_market_that_did_not_resolve_gives_no_rate(
    desk: Desk, container: Container, factory: Factory, status: MarketStatus
) -> None:
    """A user who only ever traded such a market has trades, and null rates."""
    trader = await factory.user(username="trader", balance_micro=cr(100))
    if status is MarketStatus.VOID:
        market = await desk.market()
        await desk.buy(trader, market, Outcome.YES, cr(10), cr(8))
        await desk.void(market)
    else:
        market = await desk.market(status=status)
        await desk.buy(trader, market, Outcome.YES, cr(10), cr(8))

    figures = (await figures_by_name(container))["trader"]
    entry = (await ask(container, factory, trader)).items[0]

    assert figures == Figures(
        # A void gave the stake back; in the other markets it is still at stake.
        credits_micro=cr(100) if status is MarketStatus.VOID else cr(92),
        trades=1,
        settled_markets=0,
        win_rate_micro=None,
        avg_payout_ratio_micro=None,
    )
    assert (entry.settled_markets, entry.win_rate, entry.avg_payout_ratio) == (0, None, None)
    await assert_books_balance(container.uow_factory)


async def test_a_void_market_is_left_out_of_the_rates_of_a_user_who_has_others(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Ten credits back for six in the resolved market; the eight of the voided one
    are in neither sum."""
    trader = await factory.user(username="trader", balance_micro=cr(100))
    resolved, voided = await desk.market(), await desk.market()
    await desk.buy(trader, resolved, Outcome.YES, cr(10), cr(6))
    await desk.buy(trader, voided, Outcome.YES, cr(10), cr(8))
    await desk.resolve(resolved, Outcome.YES)
    await desk.void(voided)

    assert await figures_by_name(container) == {
        "trader": Figures(
            credits_micro=cr(104),
            trades=2,
            settled_markets=1,
            win_rate_micro=1_000_000,
            avg_payout_ratio_micro=1_666_666,
        )
    }
    await assert_books_balance(container.uow_factory)


async def test_a_market_nobody_won_is_a_loss_for_everyone_in_it(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Their rates are zero, which is a rate: not null."""
    xu = await factory.user(username="xu", balance_micro=cr(100))
    yan = await factory.user(username="yan", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(xu, market, Outcome.YES, cr(5), cr(3))
    await desk.buy(yan, market, Outcome.YES, cr(8), cr(5))
    await desk.resolve(market, Outcome.NO)

    figures = await figures_by_name(container)
    answer = await ask(container, factory, xu, min_win_rate=0, max_avg_payout_ratio=0)

    assert {name: (row.settled_markets, row.win_rate_micro) for name, row in figures.items()} == {
        "xu": (1, 0),
        "yan": (1, 0),
    }
    assert {name: row.avg_payout_ratio_micro for name, row in figures.items()} == {
        "xu": 0,
        "yan": 0,
    }
    assert [(item.username, item.win_rate, item.avg_payout_ratio) for item in answer.items] == [
        ("xu", 0.0, 0.0),
        ("yan", 0.0, 0.0),
    ]
    await assert_books_balance(container.uow_factory)


async def test_a_position_nothing_was_paid_for_is_no_market_the_user_put_credits_into(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """No trade leaves such a row behind. Were one ever there, it has to count for
    nothing, and nobody's ratio may be divided by its cost of zero."""
    bystander = await factory.user(username="bystander", balance_micro=cr(100))
    trader = await factory.user(username="trader", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(trader, market, Outcome.YES, cr(10), cr(6))
    async with container.uow_factory() as uow:
        await uow.positions.add(
            Position(user_id=bystander.id, market_id=market.id, outcome=Outcome.YES)
        )
    await desk.resolve(market, Outcome.YES)

    figures = await figures_by_name(container)

    assert figures["bystander"] == Figures(
        credits_micro=cr(100),
        trades=0,
        settled_markets=0,
        win_rate_micro=None,
        avg_payout_ratio_micro=None,
    )
    assert figures["trader"].settled_markets == 1
    await assert_books_balance(container.uow_factory)


async def test_what_was_sold_before_the_resolution_counts_as_returned(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """`early` buys YES low and sells all of it once the crowd has driven the price
    up. The market resolves NO and she holds nothing, yet the sale alone returned
    more than she put in: a win. The crowd held on and lost."""
    early = await factory.user(username="early", balance_micro=cr(100))
    crowd = await factory.user(username="crowd", balance_micro=cr(1000))
    market = await desk.market(b_micro=cr(100))
    await desk.buy(early, market, Outcome.YES, cr(20))
    await desk.buy(crowd, market, Outcome.YES, cr(300))
    await desk.sell(early, market, Outcome.YES, cr(20))
    await desk.resolve(market, Outcome.NO)

    (sold,) = await stored_positions(container, early, market)
    figures = await figures_by_name(container)

    assert sold.shares_micro == 0
    assert sold.proceeds_micro > sold.cost_micro
    assert figures["early"] == Figures(
        credits_micro=cr(100) - sold.cost_micro + sold.proceeds_micro,
        trades=2,
        settled_markets=1,
        win_rate_micro=1_000_000,
        avg_payout_ratio_micro=sold.proceeds_micro * MICRO // sold.cost_micro,
    )
    assert (figures["crowd"].win_rate_micro, figures["crowd"].avg_payout_ratio_micro) == (0, 0)
    assert figures == await figures_from_the_rows(container)
    await assert_books_balance(container.uow_factory)


async def test_shares_kept_until_the_resolution_pay_on_top_of_what_was_sold(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Twenty shares bought, eight sold, twelve held when their outcome wins: what
    came back is the proceeds of the sale plus one credit for each share held."""
    holder = await factory.user(username="holder", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(holder, market, Outcome.NO, cr(20))
    await desk.sell(holder, market, Outcome.NO, cr(8))
    await desk.resolve(market, Outcome.NO)

    (kept,) = await stored_positions(container, holder, market)
    figures = (await figures_by_name(container))["holder"]

    assert kept.shares_micro == cr(12)
    assert kept.proceeds_micro > 0
    assert figures.avg_payout_ratio_micro == (
        (kept.proceeds_micro + cr(12)) * MICRO // kept.cost_micro
    )
    assert figures.win_rate_micro == 1_000_000
    assert figures.trades == 2
    assert figures == (await figures_from_the_rows(container))["holder"]
    await assert_books_balance(container.uow_factory)


async def test_both_positions_of_a_user_in_one_market_are_one_market(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Ten YES for 6 and ten NO for 7: 13 put in, and the ten winning shares back."""
    hedger = await factory.user(username="hedger", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(hedger, market, Outcome.YES, cr(10), cr(6))
    await desk.buy(hedger, market, Outcome.NO, cr(10), cr(7))
    await desk.resolve(market, Outcome.YES)

    assert await figures_by_name(container) == {
        "hedger": Figures(
            credits_micro=cr(97),
            trades=2,
            settled_markets=1,
            win_rate_micro=0,
            avg_payout_ratio_micro=769_230,
        )
    }
    await assert_books_balance(container.uow_factory)


async def test_getting_back_exactly_what_was_put_in_is_no_win(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Ten winning shares for 10 credits return what they cost. For a millionth of a
    credit less they are a win."""
    even = await factory.user(username="even", balance_micro=cr(100))
    ahead = await factory.user(username="ahead", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(even, market, Outcome.YES, cr(10), cr(10))
    await desk.buy(ahead, market, Outcome.YES, cr(10), cr("9.999999"))
    await desk.resolve(market, Outcome.YES)

    figures = await figures_by_name(container)

    assert (figures["even"].win_rate_micro, figures["even"].avg_payout_ratio_micro) == (
        0,
        1_000_000,
    )
    assert (figures["ahead"].win_rate_micro, figures["ahead"].avg_payout_ratio_micro) == (
        1_000_000,
        1_000_000,
    )
    await assert_books_balance(container.uow_factory)


async def test_the_payout_ratio_weighs_a_market_by_what_was_put_into_it(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """1.5 credits in a market that paid 2, and 98.5 in one that paid nothing: 2 back
    for 100 is 0.02. The plain mean of the two markets' ratios would be 0.666666."""
    trader = await factory.user(username="trader", balance_micro=cr(200))
    small_win, large_loss = await desk.market(), await desk.market()
    await desk.buy(trader, small_win, Outcome.YES, cr(2), cr("1.5"))
    await desk.buy(trader, large_loss, Outcome.YES, cr(100), cr("98.5"))
    await desk.resolve(small_win, Outcome.YES)
    await desk.resolve(large_loss, Outcome.NO)

    assert await figures_by_name(container) == {
        "trader": Figures(
            credits_micro=cr(102),
            trades=2,
            settled_markets=2,
            win_rate_micro=500_000,
            avg_payout_ratio_micro=20_000,
        )
    }
    await assert_books_balance(container.uow_factory)


async def test_a_payout_ratio_just_short_of_one_is_not_sent_as_one(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Ten credits back for 10.00001 are 0.999999, and nobody is shown a payout they
    did not reach: the row says 0.99."""
    trader = await factory.user(username="trader", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(trader, market, Outcome.YES, cr(10), cr("10.00001"))
    await desk.resolve(market, Outcome.YES)

    figures = (await figures_by_name(container))["trader"]
    entry = (await ask(container, factory, trader)).items[0]

    assert figures.avg_payout_ratio_micro == 999_999
    assert entry.avg_payout_ratio == 0.99
    assert entry.win_rate == 0.0
    await assert_books_balance(container.uow_factory)


async def test_a_position_too_large_for_a_bigint_times_a_million_still_has_its_ratio(
    desk: Desk, container: Container, factory: Factory
) -> None:
    """Ten million winning shares: what they returned, in millionths of a micro-credit,
    is beyond a BIGINT, so the ratio has to be worked out in NUMERIC."""
    whale = await factory.user(username="whale", balance_micro=cr(20_000_000))
    market = await desk.market()
    await desk.buy(whale, market, Outcome.YES, cr(10_000_000))
    await desk.resolve(market, Outcome.YES)

    (held,) = await stored_positions(container, whale, market)
    figures = (await figures_by_name(container))["whale"]
    entry = (await ask(container, factory, whale)).items[0]

    assert held.shares_micro * MICRO > BIGINT_MAX
    assert figures.avg_payout_ratio_micro == held.shares_micro * MICRO // held.cost_micro
    assert figures.win_rate_micro == 1_000_000
    assert entry.avg_payout_ratio == 1.0
    await assert_books_balance(container.uow_factory)


# --- Users without a value, and users who tie ----------------------------------------


@pytest.mark.parametrize("sort", [LeaderboardSort.WIN_RATE, LeaderboardSort.AVG_PAYOUT_RATIO])
async def test_users_without_a_rate_come_last_in_both_directions(
    league: League, desk: Desk, container: Container, factory: Factory, sort: LeaderboardSort
) -> None:
    """Dan's rates are zero and sort as the lowest there are. Eve never traded and
    Finn only holds a position in an open market: they have no rate, and follow the
    others whichever way the list runs, although they are the two richest."""
    await factory.user(username="eve", balance_micro=cr(900))
    finn = await factory.user(username="finn", balance_micro=cr(700))
    await desk.buy(finn, await desk.market(), Outcome.YES, cr(10))
    rated = list(LEAGUE_ORDERS[sort])
    assert rated[-1] == "dan"

    highest_first = await ask(container, factory, finn, sort=sort, order=SortOrder.DESC)
    lowest_first = await ask(container, factory, finn, sort=sort, order=SortOrder.ASC)

    assert names(highest_first) == [*rated, "eve", "finn"]
    assert names(lowest_first) == [*rated[::-1], "eve", "finn"]
    for answer in (highest_first, lowest_first):
        assert [getattr(item, sort.value) for item in answer.items[-2:]] == [None, None]
    await assert_books_balance(container.uow_factory)


async def test_a_user_who_never_traded_has_zero_trades_and_sorts_by_that_number(
    league: League, container: Container, factory: Factory
) -> None:
    """Zero is a number of trades: it comes last when the most come first, and first
    when the fewest do."""
    eve = await factory.user(username="eve", balance_micro=cr(900))
    by_trades = list(LEAGUE_ORDERS[LeaderboardSort.TRADES])

    most_first = await ask(container, factory, eve, sort="trades", order="desc")
    fewest_first = await ask(container, factory, eve, sort="trades", order="asc")

    assert names(most_first) == [*by_trades, "eve"]
    assert names(fewest_first) == ["eve", *by_trades[::-1]]
    await assert_books_balance(container.uow_factory)


async def test_users_who_tie_are_ordered_by_who_registered_first_in_both_directions(
    desk: Desk, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """Two users with the same credits, trades and rates, so they tie under every
    measure. `late` is written first and so has the lower id, but registered an hour
    after `early`."""
    clock.set(TEST_NOW + HOUR)
    late = await factory.user(username="late", balance_micro=cr(100))
    clock.set(TEST_NOW)
    early = await factory.user(username="early", balance_micro=cr(100))
    market = await desk.market()
    await desk.buy(late, market, Outcome.YES, cr(10), cr(6))
    await desk.buy(early, market, Outcome.YES, cr(10), cr(6))
    await desk.resolve(market, Outcome.YES)

    figures = await figures_by_name(container)

    assert late.id < early.id
    assert figures["late"] == figures["early"]
    for sort in LeaderboardSort:
        for order in SortOrder:
            answer = await ask(container, factory, late, sort=sort, order=order)
            assert [(item.rank, item.username) for item in answer.items] == [
                (1, "early"),
                (2, "late"),
            ], (sort, order)
    await assert_books_balance(container.uow_factory)


async def test_users_who_tie_and_registered_at_the_same_instant_are_ordered_by_id(
    container: Container, factory: Factory
) -> None:
    first = await factory.user(username="zoe", balance_micro=cr(100))
    second = await factory.user(username="abe", balance_micro=cr(100))

    assert first.created_at == second.created_at
    assert first.id < second.id
    for sort in LeaderboardSort:
        for order in SortOrder:
            answer = await ask(container, factory, first, sort=sort, order=order)
            assert [(item.rank, item.username) for item in answer.items] == [
                (1, "zoe"),
                (2, "abe"),
            ], (sort, order)
    await assert_books_balance(container.uow_factory)


# --- The statement -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("viewer", "after_rank", "limit", "ranks_returned"),
    [
        ("ada", 0, 2, [1, 2]),
        ("cleo", 0, 2, [1, 2, 4]),
        ("dan", 1, 2, [1, 2, 3]),
        ("cleo", 1, 2, [2, 3, 4]),
        ("ben", 2, 5, [3, 4]),
        ("dan", 4, 5, [1]),
        (None, 1, 2, [2, 3]),
    ],
    ids=[
        "the-viewer-inside-the-stretch",
        "the-viewer-beyond-it",
        "the-viewer-before-it",
        "the-viewer-right-after-it",
        "a-stretch-longer-than-the-list",
        "a-stretch-beyond-the-list",
        "a-viewer-who-is-not-listed",
    ],
)
async def test_the_statement_returns_the_stretch_asked_for_and_the_viewers_row_once(
    league: League,
    container: Container,
    viewer: str | None,
    after_rank: int,
    limit: int,
    ranks_returned: list[int],
) -> None:
    """`LeaderboardRepository.rank` itself: the ranks after `after_rank`, `limit` of
    them at most, and the viewer's row wherever it ranks. By credits the league is dan,
    ada, ben, cleo."""
    viewer_id = uuid7() if viewer is None else getattr(league, viewer).id

    async with container.uow_factory() as uow:
        rows = await uow.leaderboard.rank(
            sort=LeaderboardSort.CREDITS,
            order=SortOrder.DESC,
            filters=LeaderboardFilter(),
            after_rank=after_rank,
            limit=limit,
            viewer_id=viewer_id,
        )

    by_credits = ["dan", "ada", "ben", "cleo"]
    assert [row.rank for row in rows] == ranks_returned
    assert [row.username for row in rows] == [by_credits[rank - 1] for rank in ranks_returned]
    await assert_books_balance(container.uow_factory)


async def test_the_statement_leaves_the_viewer_out_when_the_filters_do(
    league: League, container: Container
) -> None:
    async with container.uow_factory() as uow:
        rows = await uow.leaderboard.rank(
            sort=LeaderboardSort.CREDITS,
            order=SortOrder.DESC,
            filters=LeaderboardFilter(degree_level=BACHELOR),
            after_rank=0,
            limit=1,
            viewer_id=league.dan.id,
        )

    assert [(row.rank, row.username) for row in rows] == [(1, "ada")]
    await assert_books_balance(container.uow_factory)


@contextmanager
def statements_sent(container: Container) -> Iterator[list[str]]:
    """The SQL the application sends to Postgres while the block runs."""
    statements: list[str] = []

    def record(connection: object, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    engine = container.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_a_ranking_is_read_with_one_statement(
    league: League, container: Container, factory: Factory
) -> None:
    """The page, the row read ahead and the viewer's row come from one snapshot."""
    with statements_sent(container) as statements:
        answer = await ask(container, factory, league.cleo, sort="win_rate", min_trades=1, limit=2)

    assert names(answer) == ["ben", "cleo"]
    assert answer.next_cursor == "2"
    assert answer.viewer is not None
    assert len(statements) == 1, statements
    assert statements[0].startswith("WITH ")
    await assert_books_balance(container.uow_factory)


async def test_no_value_of_a_request_is_planned_into_the_joins_of_the_statement(
    league: League, container: Container, factory: Factory
) -> None:
    """Postgres plans a statement it has run a few times without the values of its
    parameters. With a filter among the joins it then expected a single user and joined
    the aggregates row by row: a second on 5,000 users instead of a tenth. So `board`,
    which holds every join, is materialised and takes no parameter at all; whatever a
    request sends is compared with its result."""
    with statements_sent(container) as statements:
        await ask(
            container,
            factory,
            league.ada,
            sort="avg_payout_ratio",
            order="asc",
            degree_level=BACHELOR,
            degree_programme=COMPUTER_SCIENCE_BSC,
            min_credits_cr="1",
            max_credits_cr="1000",
            min_trades=1,
            max_trades=10,
            min_win_rate=0.1,
            max_win_rate=0.9,
            min_avg_payout_ratio=0.5,
            max_avg_payout_ratio=5,
            cursor="0",
        )

    (statement,) = statements
    the_joins, the_rest = statement.split("ranked AS", maxsplit=1)
    filters, the_stretch_and_the_viewer = 10, 3
    assert "board AS MATERIALIZED" in the_joins
    assert "$" not in the_joins
    assert the_rest.count("$") == filters + the_stretch_and_the_viewer
    # What the statement itself fixes is written out, so the planner knows it.
    for constant in ("'RESOLVED'", "'YES'", "'NO'", "'USER'", "'ACTIVE'"):
        assert constant in the_joins
    await assert_books_balance(container.uow_factory)


# --- The census: any query, answered as the definitions say --------------------------


type Bounds[T] = tuple[T | None, T | None]
"""The lowest and the highest value a filter lets through. None leaves a side open."""

OPEN: Final[tuple[None, None]] = (None, None)


@dataclass(frozen=True, slots=True)
class Wanted:
    """A query as its author means it, in exact numbers, before any of it is text."""

    sort: LeaderboardSort = LeaderboardSort.CREDITS
    order: SortOrder = SortOrder.DESC
    limit: int = DEFAULT_PAGE_SIZE
    after: int | None = None
    degree_level: DegreeLevel | None = None
    degree_programme: str | None = None
    credits_micro: Bounds[int] = OPEN
    trades: Bounds[int] = OPEN
    win_rate: Bounds[Decimal] = OPEN
    avg_payout_ratio: Bounds[Decimal] = OPEN

    def params(self) -> dict[str, str]:
        """The query string a client sends for it: text, as a request carries it."""
        lowest_credits, highest_credits = self.credits_micro
        named: dict[str, object | None] = {
            "sort": self.sort.value,
            "order": self.order.value,
            "limit": self.limit,
            "cursor": self.after,
            "degree_level": None if self.degree_level is None else self.degree_level.value,
            "degree_programme": self.degree_programme,
            "min_credits_cr": None if lowest_credits is None else format_cr(lowest_credits),
            "max_credits_cr": None if highest_credits is None else format_cr(highest_credits),
            "min_trades": self.trades[0],
            "max_trades": self.trades[1],
            "min_win_rate": _plain(self.win_rate[0]),
            "max_win_rate": _plain(self.win_rate[1]),
            "min_avg_payout_ratio": _plain(self.avg_payout_ratio[0]),
            "max_avg_payout_ratio": _plain(self.avg_payout_ratio[1]),
        }
        return {name: str(value) for name, value in named.items() if value is not None}

    def keeps(self, standing: Standing) -> bool:
        """Whether the filters let this user through, compared in exact numbers."""
        user, figures = standing.user, standing.figures
        return (
            self.degree_level in (None, user.degree_level)
            and self.degree_programme in (None, user.degree_programme)
            and _within(figures.credits_micro, self.credits_micro)
            and _within(figures.trades, self.trades)
            and _within(_fraction(figures.win_rate_micro), self.win_rate)
            and _within(_fraction(figures.avg_payout_ratio_micro), self.avg_payout_ratio)
        )


def _plain(bound: Decimal | None) -> str | None:
    """A decimal as a client writes it: digits and a point, never an exponent."""
    return None if bound is None else format(bound, "f")


def _fraction(rate_micro: int | None) -> Decimal | None:
    """A rate of whole millionths as the exact number it stands for."""
    return None if rate_micro is None else Decimal(rate_micro) / MICRO


def _within[T: (int, Decimal)](value: T | None, bounds: Bounds[T]) -> bool:
    """Inclusive on both sides. A user without a value passes only a filter that sets
    no bound."""
    lowest, highest = bounds
    if lowest is None and highest is None:
        return True
    if value is None:
        return False
    return (lowest is None or value >= lowest) and (highest is None or value <= highest)


def measure_of(figures: Figures, sort: LeaderboardSort) -> int | None:
    """The figure a sort orders by."""
    return {
        LeaderboardSort.CREDITS: figures.credits_micro,
        LeaderboardSort.WIN_RATE: figures.win_rate_micro,
        LeaderboardSort.TRADES: figures.trades,
        LeaderboardSort.AVG_PAYOUT_RATIO: figures.avg_payout_ratio_micro,
    }[sort]


def in_order(listed: Sequence[Standing], sort: LeaderboardSort, order: SortOrder) -> list[Standing]:
    """The ranking: by the measure in the direction asked for, users without a value
    last either way, ties by who registered first and then by id."""
    by_registration = sorted(listed, key=lambda s: (s.user.created_at, s.user.id.int))
    valued = [
        (value, standing)
        for standing in by_registration
        if (value := measure_of(standing.figures, sort)) is not None
    ]
    unvalued = [s for s in by_registration if measure_of(s.figures, sort) is None]
    # The sort is stable, in reverse too: users who tie stay in the order of registration.
    valued.sort(key=lambda pair: pair[0], reverse=order is SortOrder.DESC)
    return [*(standing for _, standing in valued), *unvalued]


def entry_of(standing: Standing, rank: int, viewer: User, decimals: int) -> LeaderboardEntry:
    """A user of the ranking as the API sends them."""
    user, figures = standing.user, standing.figures
    cut = Decimal(1).scaleb(-PAYOUT_RATIO_PLACES)
    win_rate, payout_ratio = figures.win_rate_micro, _fraction(figures.avg_payout_ratio_micro)
    return LeaderboardEntry(
        rank=rank,
        username=user.username,
        degree_level=user.degree_level,
        degree_programme=user.degree_programme,
        credits_cr=to_cr(figures.credits_micro),
        trades=figures.trades,
        settled_markets=figures.settled_markets,
        win_rate=None if win_rate is None else display_probability(win_rate, decimals),
        avg_payout_ratio=(
            None if payout_ratio is None else float(payout_ratio.quantize(cut, ROUND_DOWN))
        ),
        is_viewer=user.id == viewer.id,
    )


def answer_of(
    wanted: Wanted, everyone: Sequence[Standing], viewer: User, decimals: int
) -> LeaderboardResponse:
    """What `GET /leaderboard` has to answer `viewer` for `wanted`, worked out from the
    figures the stored rows give."""
    listed = [standing for standing in everyone if wanted.keeps(standing)]
    ranked = in_order(listed, wanted.sort, wanted.order)
    entries = [
        entry_of(standing, rank, viewer, decimals) for rank, standing in enumerate(ranked, start=1)
    ]
    after = wanted.after or 0
    last = after + wanted.limit
    return LeaderboardResponse(
        items=entries[after:last],
        next_cursor=str(last) if len(entries) > last else None,
        viewer=next((entry for entry in entries if entry.is_viewer), None),
    )


@dataclass(frozen=True, slots=True)
class Census:
    standings: Sequence[Standing]
    """Everybody who is listed, with the figures the stored rows give them."""

    decimals: int
    """PRICE_DISPLAY_DECIMALS of the settings."""

    def person(self, username: str) -> User:
        (user,) = [s.user for s in self.standings if s.user.username == username]
        return user

    @property
    def usernames(self) -> list[str]:
        return [standing.user.username for standing in self.standings]


@pytest.fixture
async def census(
    league: League, desk: Desk, container: Container, factory: Factory, clock: FrozenClock
) -> Census:
    """The league and eight more, so that every rule has somebody it applies to:

        eve    never traded
        finn   never traded, registered at the same instant as eve and exactly as rich
        gus    holds a position in a market that is still open
        hana   only ever traded a market that was voided
        ivo    sold eight of twenty shares before the resolution and was paid for the rest
        jo     holds both outcomes of one resolved market
        kim    got back exactly what she put in
        lou    traded and won, and is suspended: not listed

    Their degrees repeat some of the league's and add a programme written with a dash.
    """
    await factory.user(
        username="eve",
        balance_micro=cr(50),
        degree_level=MASTER,
        degree_programme=COMPUTER_SCIENCE_MSC,
    )
    await factory.user(
        username="finn",
        balance_micro=cr(50),
        degree_level=BACHELOR,
        degree_programme=COMPUTER_SCIENCE_BSC,
    )
    clock.advance(MINUTE)
    gus = await factory.user(
        username="gus",
        balance_micro=cr(100),
        degree_level=BACHELOR,
        degree_programme=PROGRAMME_WITH_A_DASH,
    )
    clock.advance(MINUTE)
    hana = await factory.user(username="hana", balance_micro=cr(100))
    clock.advance(MINUTE)
    ivo = await factory.user(
        username="ivo",
        balance_micro=cr(100),
        degree_level=DOCTORATE,
        degree_programme=PHYSICS_DOCTORATE,
    )
    clock.advance(MINUTE)
    jo = await factory.user(username="jo", balance_micro=cr(100))
    clock.advance(MINUTE)
    kim = await factory.user(
        username="kim",
        balance_micro=cr(100),
        degree_level=BACHELOR,
        degree_programme=MATHEMATICS_BSC,
    )
    clock.advance(MINUTE)
    lou = await factory.user(username="lou", balance_micro=cr(100), status=UserStatus.SUSPENDED)

    still_open, voided, lively, quiet = (
        await desk.market(),
        await desk.market(),
        await desk.market(b_micro=cr(100)),
        await desk.market(),
    )
    await desk.buy(gus, still_open, Outcome.YES, cr(10))
    await desk.buy(hana, voided, Outcome.NO, cr(10), cr(8))
    await desk.buy(ivo, lively, Outcome.YES, cr(20))
    await desk.sell(ivo, lively, Outcome.YES, cr(8))
    await desk.buy(jo, quiet, Outcome.YES, cr(10), cr(6))
    await desk.buy(jo, quiet, Outcome.NO, cr(10), cr(7))
    await desk.buy(kim, quiet, Outcome.YES, cr(10), cr(10))
    await desk.buy(lou, quiet, Outcome.YES, cr(10), cr(6))
    await desk.void(voided)
    await desk.resolve(lively, Outcome.YES)
    await desk.resolve(quiet, Outcome.YES)

    return Census(
        standings=await standings(container),
        decimals=container.settings.pricing.price_display_decimals,
    )


BESIDE: Final[tuple[int, ...]] = (-1000, -1, 0, 1, 1000)
"""How far beside somebody's rate a bound is put, in thousandths of a millionth: a whole
millionth below, a hair below, exactly on it, a hair above, a whole millionth above."""

RATE_PLACES: Final[int] = 9
"""Decimal places of a generated bound on a rate: three more than a rate has."""


def rate_bounds(rates_micro: Sequence[int], ceiling: int) -> st.SearchStrategy[Decimal]:
    """A bound on a rate as a client may write it, to nine places: on a rate somebody
    has, right beside one, or anywhere from 0 to `ceiling`."""
    top = ceiling * MICRO * 1000
    beside = st.builds(
        lambda rate_micro, offset: min(max(rate_micro * 1000 + offset, 0), top),
        st.sampled_from([0, *rates_micro]),
        st.sampled_from(BESIDE),
    )
    return st.one_of(beside, st.integers(min_value=0, max_value=top)).map(
        lambda thousandths: Decimal(thousandths).scaleb(-RATE_PLACES)
    )


def whole_bounds(values: Sequence[int]) -> st.SearchStrategy[int]:
    """A bound on credits (in µCR) or on trades: on a value somebody has or one beside it."""
    return st.builds(
        lambda value, offset: max(value + offset, 0),
        st.sampled_from(list(values)),
        st.sampled_from([-1, 0, 1]),
    )


def pairs[T: (int, Decimal)](values: st.SearchStrategy[T]) -> st.SearchStrategy[Bounds[T]]:
    """A lowest and a highest bound, either of which may be left open, and never the
    lowest above the highest: the query refuses that."""

    def uncrossed(drawn: Bounds[T]) -> Bounds[T]:
        lowest, highest = drawn
        if lowest is not None and highest is not None and lowest > highest:
            return highest, lowest
        return drawn

    return st.tuples(st.none() | values, st.none() | values).map(uncrossed)


def degrees(users: Sequence[User]) -> st.SearchStrategy[tuple[DegreeLevel | None, str | None]]:
    """A level and a programme to filter by, either of which may be left out: mostly no
    filter, often the level or the programme of somebody, sometimes a degree nobody has."""
    somebody = st.sampled_from(users)
    no_filter: tuple[DegreeLevel | None, str | None] = (None, None)
    return st.one_of(
        st.just(no_filter),
        st.just(no_filter),
        st.just(no_filter),
        st.just(no_filter),
        st.just(no_filter),
        somebody.map(lambda user: (user.degree_level, None)),
        somebody.map(lambda user: (None, user.degree_programme)),
        somebody.map(lambda user: (user.degree_level, user.degree_programme)),
        st.sampled_from([(DegreeLevel.OTHER, None), (None, NOBODYS_PROGRAMME)]),
    )


def viewers(census: Census, wanted: Wanted) -> st.SearchStrategy[str]:
    """Whom to ask as: anybody, and half the time somebody the filters keep, so that
    the viewer's own row is often there to be compared."""
    kept = [s.user.username for s in census.standings if wanted.keeps(s)]
    anybody = st.sampled_from(census.usernames)
    return st.one_of(st.sampled_from(kept), anybody) if kept else anybody


@st.composite
def queries(draw: st.DrawFn, everyone: Sequence[Standing]) -> Wanted:
    """Any query about these users: every order, any page, and filters aimed at the
    values they have. At most two measures are bounded at once, so that most draws
    leave somebody on the list."""
    figures = [standing.figures for standing in everyone]
    win_rates = [f.win_rate_micro for f in figures if f.win_rate_micro is not None]
    payout_ratios = [
        f.avg_payout_ratio_micro for f in figures if f.avg_payout_ratio_micro is not None
    ]
    bounded = draw(st.sets(st.sampled_from(MEASURES_WITH_BOUNDS), max_size=2))
    beyond_the_end = len(everyone) + 2
    degree_level, degree_programme = draw(degrees([standing.user for standing in everyone]))
    return Wanted(
        sort=draw(st.sampled_from(LeaderboardSort)),
        order=draw(st.sampled_from(SortOrder)),
        limit=draw(st.integers(min_value=1, max_value=beyond_the_end)),
        after=draw(st.none() | st.none() | st.integers(min_value=0, max_value=beyond_the_end)),
        degree_level=degree_level,
        degree_programme=degree_programme,
        credits_micro=(
            draw(pairs(whole_bounds([f.credits_micro for f in figures])))
            if "credits_cr" in bounded
            else OPEN
        ),
        trades=(
            draw(pairs(whole_bounds([f.trades for f in figures]))) if "trades" in bounded else OPEN
        ),
        win_rate=draw(pairs(rate_bounds(win_rates, 1))) if "win_rate" in bounded else OPEN,
        avg_payout_ratio=(
            draw(pairs(rate_bounds(payout_ratios, 3))) if "avg_payout_ratio" in bounded else OPEN
        ),
    )


@checks
@given(data=st.data())
async def test_any_query_is_answered_as_the_definitions_say(
    census: Census, container: Container, factory: Factory, data: st.DataObject
) -> None:
    """Order, filters, ranks, page, cursor and the viewer's row, against the figures of
    the stored rows. The bounds are compared as the decimals a client wrote."""
    wanted = data.draw(queries(census.standings), label="query")
    viewer = census.person(data.draw(viewers(census, wanted), label="viewer"))

    answer = await container.leaderboard.ranking(
        factory.identity(viewer), LeaderboardQuery.model_validate(wanted.params())
    )

    assert answer == answer_of(wanted, census.standings, viewer, census.decimals)
    await assert_books_balance(container.uow_factory)


@checks
@given(data=st.data())
async def test_following_the_cursor_through_any_ranking_lists_everybody_in_it_once(
    census: Census, container: Container, factory: Factory, data: st.DataObject
) -> None:
    """Whatever the order, the filters and the size of a page."""
    wanted = replace(data.draw(queries(census.standings), label="query"), after=None)
    viewer = census.person(data.draw(viewers(census, wanted), label="viewer"))
    whole = answer_of(
        replace(wanted, limit=MAX_PAGE_SIZE), census.standings, viewer, census.decimals
    )

    walked: list[LeaderboardEntry] = []
    pages = 0
    cursor: str | None = None
    while True:
        params = wanted.params() | ({} if cursor is None else {"cursor": cursor})
        page = await container.leaderboard.ranking(
            factory.identity(viewer), LeaderboardQuery.model_validate(params)
        )
        assert len(page.items) <= wanted.limit
        assert page.viewer == whole.viewer
        walked += page.items
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert walked == whole.items
    assert pages == max(1, -(-len(whole.items) // wanted.limit))
    await assert_books_balance(container.uow_factory)


@checks
@given(
    cursor=st.one_of(
        st.text(min_size=1, max_size=64).map(str.strip).filter(bool),
        st.from_regex(r"[0-9]{1,64}", fullmatch=True),
        st.integers(min_value=-3, max_value=CURSOR_MAX + 3).map(str),
    ),
    limit=st.integers(min_value=1, max_value=6),
)
async def test_a_cursor_is_a_whole_number_up_to_the_largest_rank_and_nothing_else(
    league: League, container: Container, factory: Factory, cursor: str, limit: int
) -> None:
    """Any text: ASCII digits that spell a number from 0 to `CURSOR_MAX` continue after
    that rank, everything else is refused with the name of the field."""
    is_a_rank = cursor.isascii() and cursor.isdigit() and int(cursor) <= CURSOR_MAX

    if is_a_rank:
        answer = await ask(container, factory, league.ada, cursor=cursor, limit=limit)
        last = min(int(cursor) + limit, len(LEAGUE_FIGURES))
        assert ranks(answer) == list(range(int(cursor) + 1, last + 1))
    else:
        with pytest.raises(ValidationFailedError) as refused:
            await ask(container, factory, league.ada, cursor=cursor, limit=limit)
        assert refused.value.details == {"field": "cursor"}
    await assert_books_balance(container.uow_factory)
