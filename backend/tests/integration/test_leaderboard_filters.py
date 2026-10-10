"""Every filter of the leaderboard, alone and in pairs, through the route (SPEC §10, A.2).

A league of fifteen people who differ in everything the leaderboard looks at: degree,
balance, open positions, number of trades, settled markets and wins. The people buy
through the factory, sell through the trading service, and their markets are halted,
closed, resolved and voided by the lifecycle service: the rows are the application's.

`GET /leaderboard` is then asked every question a filter sheet can ask: each of the ten
filters alone and each two of them together, under every sort and in both orders. For
each question the answer is held, field by field, against a ranking worked out here in
plain Python from the stored rows, read with SQL of their own: who is listed, in which
order, each rank, each figure of a row, the viewer's own row, and the same list again in
pages that follow `next_cursor`.

Nothing of the application takes part in what is expected but `app.pricing.lmsr`, the
one place that computes a price.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from itertools import combinations
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.degrees import CATALOGUE
from app.core.enums import (
    DegreeLevel,
    LeaderboardSort,
    Outcome,
    SortOrder,
    TradeSide,
    UserStatus,
)
from app.core.units import MICRO, to_cr
from app.models import Market, User
from app.pricing import lmsr
from app.schemas.admin.markets import (
    CloseMarketRequest,
    HaltMarketRequest,
    ResolveMarketRequest,
    VoidMarketRequest,
)
from app.schemas.trading import TradeRequest
from tests.support.books import assert_books_balance
from tests.support.clock import FrozenClock
from tests.support.economy import PRECISION
from tests.support.factories import ActAs, Factory
from tests.support.settings import load_base_settings

pytestmark = pytest.mark.integration

LEADERBOARD: Final[str] = "/api/leaderboard"

DECIMALS: Final[int] = load_base_settings().pricing.price_display_decimals
"""PRICE_DISPLAY_DECIMALS: a win rate is sent rounded to that many decimals of a percent."""

MINUTE: Final[timedelta] = timedelta(minutes=1)

PAGE: Final[int] = 4
"""The small page the list is walked in: four pages for the whole league."""

WHOLE: Final[int] = 200
"""A page that holds everybody."""

SORTS: Final[tuple[LeaderboardSort, ...]] = tuple(LeaderboardSort)
ORDERS: Final[tuple[SortOrder, ...]] = tuple(SortOrder)

FILTERS: Final[tuple[str, ...]] = (
    "degree_level",
    "degree_programme",
    "min_credits_cr",
    "max_credits_cr",
    "min_trades",
    "max_trades",
    "min_win_rate",
    "max_win_rate",
    "min_avg_payout_ratio",
    "max_avg_payout_ratio",
)
"""Everything a query can filter by."""

FILTER_SETS: Final[int] = 1 + len(FILTERS) + len(FILTERS) * (len(FILTERS) - 1) // 2
"""No filter, each filter alone, and each two together: 56."""

CASES: Final[int] = FILTER_SETS * len(SORTS) * len(ORDERS)
"""Every set of filters under every sort in both orders: 448 questions."""

BSC_CS: Final[str] = "BSc Computer Science"
BSC_MATH: Final[str] = "BSc Mathematics"
BSC_BIOCHEMISTRY: Final[str] = next(
    label for label in CATALOGUE[0].programmes if not label.isascii()
)
"""A Bachelor label ETH writes with an en dash: it makes the trip as it is stored."""
MSC_CS: Final[str] = "MSc Computer Science"
MSC_DATA: Final[str] = "MSc Data Science"
PHYSICS: Final[str] = "Doctorate Physics"
STAFF: Final[str] = "ETH staff"


def cr(amount: int | str) -> int:
    return int(Fraction(str(amount)) * MICRO)


# --- The league ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class League:
    """The people a test asks as. Everything else about the league is read from the
    database."""

    ada: User
    dan: User
    jo: User
    admin: User


async def league(container: Container, factory: Factory, clock: FrozenClock) -> League:
    """Fifteen listed people, one suspended trader and the suspended host who proposes
    every market, so that no market is anybody's own.

        ada    Bachelor   won one market, holds an open position
        ben    Master     lost one market, won one, holds shares of a closed market
        cleo   Bachelor   five trades in an open market and one in a voided one
        dan    Doctorate  the richest, never traded
        eve    Master     only ever traded a market that was voided
        finn   Bachelor   won one of three
        gus    Bachelor   sold at a profit before a market resolved against him
        hana   Other      positions in an open, a halted and a closed market, none settled
        ivo    Doctorate  lost everything he put in, and holds shares past a deadline
        jo     Master     held both outcomes of a resolved market, and an open position
        kim    Bachelor   nothing and no trade
        noa    Master     registered at the same instant as kim, with exactly as little:
                          the two tie under every measure, and only their ids order them
        lou    Master     got back exactly what she put in, holds shares past a deadline
        mia    Bachelor   won all three of her markets
        admin  Bachelor   settles the markets and trades nothing
        zed    suspended  traded and won: not listed
    """
    people: dict[str, User] = {}

    async def register(
        username: str, level: DegreeLevel, programme: str, balance: int, *, wait: bool = True
    ) -> None:
        people[username] = await factory.user(
            username=username,
            balance_micro=cr(balance),
            degree_level=level,
            degree_programme=programme,
        )
        if wait:
            clock.advance(MINUTE)

    await register("ada", DegreeLevel.BACHELOR, BSC_CS, 500)
    await register("ben", DegreeLevel.MASTER, MSC_CS, 480)
    await register("cleo", DegreeLevel.BACHELOR, BSC_MATH, 300)
    await register("dan", DegreeLevel.DOCTORATE, PHYSICS, 800)
    await register("eve", DegreeLevel.MASTER, MSC_CS, 120)
    await register("finn", DegreeLevel.BACHELOR, BSC_CS, 500)
    await register("gus", DegreeLevel.BACHELOR, BSC_MATH, 250)
    await register("hana", DegreeLevel.OTHER, STAFF, 500)
    await register("ivo", DegreeLevel.DOCTORATE, PHYSICS, 60)
    await register("jo", DegreeLevel.MASTER, MSC_DATA, 500)
    await register("kim", DegreeLevel.BACHELOR, BSC_CS, 0, wait=False)
    await register("noa", DegreeLevel.MASTER, MSC_DATA, 0)
    await register("lou", DegreeLevel.MASTER, MSC_CS, 500)
    await register("mia", DegreeLevel.BACHELOR, BSC_BIOCHEMISTRY, 500)
    admin = await factory.admin()
    clock.advance(MINUTE)
    zed = await factory.user(username="zed", balance_micro=cr(900), status=UserStatus.SUSPENDED)
    host = await factory.user(username="host", status=UserStatus.SUSPENDED)
    acting_admin = factory.identity(admin)

    async def market(**more: Any) -> Market:
        return await factory.market(created_by=host, **more)

    async def buy(who: str, where: Market, outcome: Outcome, shares: int, cost: int | None) -> None:
        await factory.position(
            people[who],
            where,
            outcome,
            shares_micro=cr(shares),
            cost_micro=None if cost is None else cr(cost),
        )

    async def sell(who: str, where: Market, outcome: Outcome, shares: int) -> None:
        await container.trading.trade(
            factory.identity(people[who]),
            where.id,
            uuid7(),
            TradeRequest(side=TradeSide.SELL, outcome=outcome, shares=to_cr(cr(shares))),
        )

    yes, no = Outcome.YES, Outcome.NO
    won_yes, won_no, won_yes_too, voided = [await market() for _ in range(4)]
    open_one, open_two, halted, closed = [await market() for _ in range(4)]
    past_deadline = await market(close_in=timedelta(hours=2))

    await buy("ada", won_yes, yes, 40, None)
    await buy("ben", won_yes, no, 30, None)
    await buy("finn", won_yes, yes, 20, None)
    await buy("jo", won_yes, yes, 25, None)
    await buy("jo", won_yes, no, 25, None)
    await buy("mia", won_yes, yes, 30, None)
    await buy("lou", won_yes, yes, 10, 10)

    await buy("gus", won_no, yes, 60, None)
    await buy("finn", won_no, yes, 90, None)
    await sell("gus", won_no, yes, 60)
    await buy("ben", won_no, no, 50, None)
    await buy("mia", won_no, no, 40, None)

    await buy("finn", won_yes_too, no, 20, None)
    await buy("ivo", won_yes_too, no, 40, None)
    await buy("mia", won_yes_too, yes, 20, None)
    await factory.position(zed, won_yes_too, yes, shares_micro=cr(20))

    await buy("eve", voided, yes, 30, None)
    await buy("cleo", voided, no, 10, None)

    await buy("ada", open_one, yes, 35, None)
    await buy("cleo", open_one, yes, 20, None)
    await sell("cleo", open_one, yes, 5)
    await buy("cleo", open_one, no, 10, None)
    await sell("cleo", open_one, yes, 5)
    await buy("cleo", open_one, yes, 8, None)
    await buy("hana", open_one, no, 15, None)
    await buy("jo", open_two, no, 45, None)
    await buy("gus", open_two, yes, 12, None)
    await buy("hana", halted, yes, 25, None)
    await buy("hana", closed, no, 10, None)
    await buy("ben", closed, yes, 15, None)
    await buy("ivo", past_deadline, yes, 5, None)
    await buy("lou", past_deadline, yes, 22, None)

    lifecycle = container.market_lifecycle
    for settled, outcome in ((won_yes, yes), (won_no, no), (won_yes_too, yes)):
        await lifecycle.close(acting_admin, settled.id, CloseMarketRequest(note=None))
        await lifecycle.resolve(
            acting_admin, settled.id, ResolveMarketRequest(outcome=outcome, evidence="Seen.")
        )
    await lifecycle.close(acting_admin, voided.id, CloseMarketRequest(note=None))
    await lifecycle.void(acting_admin, voided.id, VoidMarketRequest(reason="Asked wrongly."))
    await lifecycle.halt(acting_admin, halted.id, HaltMarketRequest(reason="In doubt."))
    await lifecycle.close(acting_admin, closed.id, CloseMarketRequest(note=None))
    clock.advance(timedelta(hours=3))
    return League(ada=people["ada"], dan=people["dan"], jo=people["jo"], admin=admin)


# --- What the rows say, worked out here ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Player:
    """One listed person and their figures, in integers. A rate is in millionths, and
    None for somebody without a resolved market they put credits into."""

    id: UUID
    username: str
    degree_level: str
    degree_programme: str
    created_at: datetime
    balance: int
    in_positions: int
    trades: int
    settled_markets: int
    win_rate: int | None
    payout_ratio: int | None

    @property
    def credits(self) -> int:
        return self.balance + self.in_positions


async def players(owner_engine: AsyncEngine) -> list[Player]:
    """Every ACTIVE user with their figures, from the tables as they are stored.

    Credits are the balance plus what the open positions are worth: over the markets
    that are neither RESOLVED nor VOID, the shares of each holding at the price of its
    outcome, rounded down. A market counts for the rates when it is RESOLVED and the
    user put credits into it; what came back is what was sold plus one credit per share
    held of the winning outcome, and a win is more back than in. The win rate and the
    payout ratio are cut to whole millionths.
    """

    async def read(statement: str) -> Sequence[Any]:
        async with owner_engine.connect() as connection:
            return (await connection.execute(text(statement))).all()

    users = await read(
        "SELECT id, username, degree_level, degree_programme, created_at"
        " FROM users WHERE status = 'ACTIVE'"
    )
    balances: dict[UUID, int] = dict(
        await read("SELECT owner_id, balance_micro FROM accounts WHERE kind = 'USER'")
    )
    traded: dict[UUID, int] = dict(await read("SELECT user_id, count(*) FROM trades GROUP BY 1"))
    markets = {
        market.id: market
        for market in await read("SELECT id, status, b_micro, q_yes_micro, q_no_micro FROM markets")
    }
    outcomes: dict[UUID, str] = dict(await read("SELECT market_id, outcome FROM resolutions"))
    positions = await read(
        "SELECT user_id, market_id, outcome, shares_micro, cost_micro, proceeds_micro"
        " FROM positions"
    )

    p_yes = {
        market.id: lmsr.price_yes_micro(
            lmsr.MarketState(q_yes=market.q_yes_micro, q_no=market.q_no_micro, b=market.b_micro),
            precision=PRECISION,
        )
        for market in markets.values()
        if market.status not in ("RESOLVED", "VOID", "PROPOSED", "REJECTED")
    }
    found: list[Player] = []
    for user in users:
        held = [position for position in positions if position.user_id == user.id]
        in_positions = 0
        for position in held:
            if position.market_id in p_yes:
                price = p_yes[position.market_id]
                if position.outcome == "NO":
                    price = MICRO - price
                in_positions += position.shares_micro * price // MICRO
        settled = wins = put_in_all = back_all = 0
        for market_id in sorted({position.market_id for position in held}):
            if markets[market_id].status != "RESOLVED":
                continue
            there = [position for position in held if position.market_id == market_id]
            put_in = sum(position.cost_micro for position in there)
            if put_in == 0:
                continue
            back = sum(position.proceeds_micro for position in there) + sum(
                position.shares_micro
                for position in there
                if position.outcome == outcomes[market_id]
            )
            settled += 1
            wins += back > put_in
            put_in_all += put_in
            back_all += back
        found.append(
            Player(
                id=user.id,
                username=user.username,
                degree_level=user.degree_level,
                degree_programme=user.degree_programme,
                created_at=user.created_at,
                balance=balances[user.id],
                in_positions=in_positions,
                trades=traded.get(user.id, 0),
                settled_markets=settled,
                win_rate=wins * MICRO // settled if settled else None,
                payout_ratio=back_all * MICRO // put_in_all if settled else None,
            )
        )
    return found


def amount(micro: int) -> str:
    """An amount as the API writes it: a decimal string without trailing zeros."""
    whole, millionths = divmod(micro, MICRO)
    return f"{whole}.{millionths:06d}".rstrip("0").rstrip(".")


def row(player: Player, rank: int, viewer_id: UUID) -> dict[str, Any]:
    """A person of the ranking as `GET /leaderboard` sends them."""
    win_rate = (
        None
        if player.win_rate is None
        else float(round(Fraction(player.win_rate, MICRO), DECIMALS + 2))
    )
    payout_ratio = None if player.payout_ratio is None else (player.payout_ratio // 10_000) / 100
    return {
        "rank": rank,
        "username": player.username,
        "degree_level": player.degree_level,
        "degree_programme": player.degree_programme,
        "credits_cr": amount(player.credits),
        "balance_cr": amount(player.balance),
        "positions_value_cr": amount(player.in_positions),
        "trades": player.trades,
        "settled_markets": player.settled_markets,
        "win_rate": win_rate,
        "avg_payout_ratio": payout_ratio,
        "is_viewer": player.id == viewer_id,
    }


MEASURES: Final[dict[LeaderboardSort, Callable[[Player], int | None]]] = {
    LeaderboardSort.CREDITS: lambda player: player.credits,
    LeaderboardSort.WIN_RATE: lambda player: player.win_rate,
    LeaderboardSort.TRADES: lambda player: player.trades,
    LeaderboardSort.AVG_PAYOUT_RATIO: lambda player: player.payout_ratio,
}
"""The figure each sort orders by."""


@dataclass(frozen=True, slots=True)
class Filter:
    """One filter as a query names it, and whom it keeps."""

    name: str
    value: str
    keeps: Callable[[Player], bool]


def at_least(figure: Callable[[Player], int | None], bound: Fraction) -> Callable[[Player], bool]:
    """Inclusive, and somebody without the figure is not kept."""

    def keeps(player: Player) -> bool:
        value = figure(player)
        return value is not None and value >= bound

    return keeps


def at_most(figure: Callable[[Player], int | None], bound: Fraction) -> Callable[[Player], bool]:
    def keeps(player: Player) -> bool:
        value = figure(player)
        return value is not None and value <= bound

    return keeps


def filters_for(everyone: Sequence[Player]) -> list[Filter]:
    """One filter of each kind, with a bound somebody of the league sits exactly on, so
    that every bound has to be inclusive to give the right list. A lower bound is never
    above the upper bound of the same measure."""
    by_credits = sorted(player.credits for player in everyone)
    lowest_credits, highest_credits = by_credits[4], by_credits[-4]
    rates = sorted(player.win_rate for player in everyone if player.win_rate is not None)
    ratios = sorted(player.payout_ratio for player in everyone if player.payout_ratio is not None)
    # The two in the middle of those who have a rate: each bound keeps about half of them.
    lowest_rate, highest_rate = rates[len(rates) // 2 - 1], rates[len(rates) // 2]
    lowest_ratio, highest_ratio = ratios[len(ratios) // 2 - 1], ratios[len(ratios) // 2]
    assert lowest_credits < highest_credits
    assert lowest_rate < highest_rate
    assert lowest_ratio < highest_ratio

    def rate(player: Player) -> int | None:
        return player.win_rate

    def ratio(player: Player) -> int | None:
        return player.payout_ratio

    def credits_of(player: Player) -> int | None:
        return player.credits

    def trades_of(player: Player) -> int | None:
        return player.trades

    return [
        Filter("degree_level", "MASTER", lambda player: player.degree_level == "MASTER"),
        Filter("degree_programme", BSC_CS, lambda player: player.degree_programme == BSC_CS),
        Filter(
            "min_credits_cr", amount(lowest_credits), at_least(credits_of, Fraction(lowest_credits))
        ),
        Filter(
            "max_credits_cr",
            amount(highest_credits),
            at_most(credits_of, Fraction(highest_credits)),
        ),
        Filter("min_trades", "2", at_least(trades_of, Fraction(2))),
        Filter("max_trades", "3", at_most(trades_of, Fraction(3))),
        Filter("min_win_rate", amount(lowest_rate), at_least(rate, Fraction(lowest_rate))),
        Filter("max_win_rate", amount(highest_rate), at_most(rate, Fraction(highest_rate))),
        Filter(
            "min_avg_payout_ratio", amount(lowest_ratio), at_least(ratio, Fraction(lowest_ratio))
        ),
        Filter(
            "max_avg_payout_ratio", amount(highest_ratio), at_most(ratio, Fraction(highest_ratio))
        ),
    ]


def filter_sets(filters: Sequence[Filter]) -> list[tuple[Filter, ...]]:
    """No filter, every filter alone, and every two together."""
    return [(), *((one,) for one in filters), *combinations(filters, 2)]


def ranking(
    everyone: Sequence[Player],
    chosen: Sequence[Filter],
    sort: LeaderboardSort,
    order: SortOrder,
    viewer_id: UUID,
) -> list[dict[str, Any]]:
    """The whole list a query owes, first rank first: whoever every filter keeps, by
    the measure in the direction asked for, people without a value for it last either
    way, ties by who registered first and then by id. The rank is the place in this
    list, counted from 1."""
    measure = MEASURES[sort]
    kept = sorted(
        (player for player in everyone if all(one.keeps(player) for one in chosen)),
        key=lambda player: (player.created_at, player.id.int),
    )
    valued = [player for player in kept if measure(player) is not None]
    unvalued = [player for player in kept if measure(player) is None]
    # The sort is stable, in reverse too: people who tie stay in the order of registration.
    valued.sort(key=lambda player: measure(player) or 0, reverse=order is SortOrder.DESC)
    return [
        row(player, rank, viewer_id) for rank, player in enumerate([*valued, *unvalued], start=1)
    ]


# --- The league is what its docstring says ---------------------------------------------


async def test_the_league_differs_in_everything_the_leaderboard_looks_at(
    container: Container, factory: Factory, clock: FrozenClock, owner_engine: AsyncEngine
) -> None:
    """Fifteen listed people. Were they alike in something, a filter on it would prove
    nothing: so this holds the league itself, and that every filter of the test keeps
    some of them and leaves others out."""
    await league(container, factory, clock)

    everyone = await players(owner_engine)
    by_name = {player.username: player for player in everyone}
    filters = filters_for(everyone)

    assert len(everyone) == 15
    assert "zed" not in by_name
    assert "host" not in by_name
    assert len({player.degree_level for player in everyone}) == 4
    assert len({player.degree_programme for player in everyone}) == 8
    assert len({player.balance for player in everyone}) >= 12
    assert len({player.credits for player in everyone}) >= 12
    assert {name for name, player in by_name.items() if player.in_positions > 0} == {
        "ada",
        "ben",
        "cleo",
        "gus",
        "hana",
        "ivo",
        "jo",
        "lou",
    }
    assert sorted({player.trades for player in everyone}) == [0, 1, 2, 3, 6]
    assert sorted({player.settled_markets for player in everyone}) == [0, 1, 2, 3]
    assert {name: player.win_rate for name, player in by_name.items()} == {
        "ada": MICRO,
        "ben": 500_000,
        "cleo": None,
        "dan": None,
        "eve": None,
        "finn": 333_333,
        "gus": MICRO,
        "hana": None,
        "ivo": 0,
        "jo": 0,
        "kim": None,
        "lou": 0,
        "mia": MICRO,
        "noa": None,
        "admin0": None,
    }
    assert by_name["lou"].payout_ratio == MICRO, "she got back exactly what she put in"
    assert by_name["ivo"].payout_ratio == 0
    assert by_name["gus"].settled_markets == 1, "he sold at a profit before it resolved against him"
    kim, noa = by_name["kim"], by_name["noa"]
    assert kim.created_at == noa.created_at
    assert kim.id.int < noa.id.int
    assert all(measure(kim) == measure(noa) for measure in MEASURES.values()), "they tie"
    assert (kim.credits, noa.credits, by_name["admin0"].credits) == (0, 0, 0)
    assert by_name["dan"].credits == max(player.credits for player in everyone)

    assert [one.name for one in filters] == list(FILTERS)
    shown: dict[str, Callable[[Player], str | None]] = {
        "credits_cr": lambda player: amount(player.credits),
        "trades": lambda player: str(player.trades),
        "win_rate": lambda player: None if player.win_rate is None else amount(player.win_rate),
        "avg_payout_ratio": lambda player: (
            None if player.payout_ratio is None else amount(player.payout_ratio)
        ),
    }
    for one in filters:
        kept = sum(one.keeps(player) for player in everyone)
        assert 0 < kept < len(everyone), one.name
        if one.name.startswith(("min_", "max_")):
            on_the_bound = [
                player for player in everyone if shown[one.name[4:]](player) == one.value
            ]
            assert on_the_bound, f"nobody sits exactly on {one.name}={one.value}"
            assert all(one.keeps(player) for player in on_the_bound), one.name
    sets = filter_sets(filters)
    assert len(sets) == FILTER_SETS == 56
    assert CASES == 448
    left = [sum(all(one.keeps(player) for one in chosen) for player in everyone) for chosen in sets]
    assert left[0] == len(everyone)
    assert left.count(0) >= 1, "some pair leaves nobody: an empty list is one of the answers"
    assert sum(0 < kept < len(everyone) for kept in left) >= 50
    await assert_books_balance(container.uow_factory)


# --- Every question ----------------------------------------------------------------------


@pytest.mark.parametrize("order", ORDERS, ids=str)
@pytest.mark.parametrize("sort", SORTS, ids=str)
async def test_every_filter_alone_and_in_pairs_lists_ranks_and_pages_as_the_rows_say(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    client: httpx.AsyncClient,
    as_user: ActAs,
    sort: LeaderboardSort,
    order: SortOrder,
) -> None:
    """Fifty-six sets of filters under one sort and one order. For each: the whole list
    in one page, and the same list again in pages of four that follow `next_cursor`.
    Every row is compared whole (rank, name, degree, credits and their two parts,
    trades, settled markets, both rates, `is_viewer`), and so is the viewer's own row,
    on every page. Who asks changes from one set to the next: somebody in the middle of
    the list, the richest who never traded, and the admin."""
    asked_as = await league(container, factory, clock)
    everyone = await players(owner_engine)
    viewers = [asked_as.ada, asked_as.dan, asked_as.admin]
    asked = 0

    for number, chosen in enumerate(filter_sets(filters_for(everyone))):
        viewer = viewers[number % len(viewers)]
        as_user(viewer)
        query = {"sort": sort.value, "order": order.value} | {one.name: one.value for one in chosen}
        named = sorted(query.items())
        expected = ranking(everyone, chosen, sort, order, viewer.id)
        own = next((entry for entry in expected if entry["is_viewer"]), None)

        whole = await client.get(LEADERBOARD, params=query | {"limit": WHOLE})

        assert whole.status_code == 200, (named, whole.text)
        assert whole.json() == {"items": expected, "next_cursor": None, "viewer": own}, named
        assert [entry["rank"] for entry in expected] == list(range(1, len(expected) + 1))

        walked: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page_query = query | {"limit": PAGE} | ({} if cursor is None else {"cursor": cursor})
            page = (await client.get(LEADERBOARD, params=page_query)).json()
            assert page["items"] == expected[len(walked) : len(walked) + PAGE], (named, cursor)
            assert page["viewer"] == own, (named, cursor)
            walked += page["items"]
            more = len(walked) < len(expected)
            assert page["next_cursor"] == (str(len(walked)) if more else None), (named, cursor)
            if not more:
                break
            cursor = page["next_cursor"]
        assert walked == expected, named
        asked += 1

    assert asked == FILTER_SETS
    await assert_books_balance(container.uow_factory)


async def test_the_rank_of_a_filtered_list_is_the_place_in_that_list(
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
    client: httpx.AsyncClient,
    as_user: ActAs,
) -> None:
    """Not the place among everybody. The Master students are ranked 1 to 5 among
    themselves, whatever their places in the whole list are, and the viewer's own row
    carries the rank of the list that was asked for."""
    asked_as = await league(container, factory, clock)
    everyone = await players(owner_engine)
    jo = next(player for player in everyone if player.username == "jo")
    as_user(asked_as.ada)
    all_of_them = (await client.get(LEADERBOARD)).json()
    place_among_all = {entry["username"]: entry["rank"] for entry in all_of_them["items"]}

    masters = (await client.get(LEADERBOARD, params={"degree_level": "MASTER"})).json()

    listed = [(entry["rank"], entry["username"]) for entry in masters["items"]]
    assert [rank for rank, _ in listed] == [1, 2, 3, 4, 5]
    assert {name for _, name in listed} == {"ben", "eve", "jo", "lou", "noa"}
    among_all = [place_among_all[name] for _, name in listed]
    assert among_all == sorted(among_all), "the same order as in the whole list"
    assert among_all != [1, 2, 3, 4, 5], "and other places than there"
    assert masters["viewer"] is None, "ada is a Bachelor student: she is not in this list"
    assert all_of_them["viewer"]["rank"] == place_among_all["ada"]

    as_user(asked_as.jo)
    for_jo = (await client.get(LEADERBOARD, params={"degree_level": "MASTER", "limit": "1"})).json()
    in_the_filtered_list = next(rank for rank, name in listed if name == "jo")
    assert [entry["rank"] for entry in for_jo["items"]] == [1]
    assert (for_jo["viewer"]["username"], for_jo["viewer"]["rank"]) == ("jo", in_the_filtered_list)
    assert for_jo["viewer"]["rank"] < place_among_all["jo"]
    assert for_jo["viewer"]["credits_cr"] == amount(jo.credits)
    await assert_books_balance(container.uow_factory)
