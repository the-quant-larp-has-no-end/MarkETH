"""The leaderboard query (SPEC §10, Appendix A.2).

One statement over `users`, `accounts`, `trades`, `positions`, `markets` and
`resolutions`. Nothing is stored for it and no table or index exists for it alone: the
ranking is computed from the rows the rest of the application writes.

The measures and their rounding are defined once, in the docstring of
`LeaderboardService.ranking`. This module turns that definition into SQL and nothing
else: it takes bounds in micro-units and hands back integers.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    CTE,
    BigInteger,
    BindParameter,
    ColumnElement,
    Numeric,
    Select,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import TypeEngine

from app.core.enums import (
    AccountKind,
    DegreeLevel,
    LeaderboardSort,
    MarketStatus,
    ResolutionOutcome,
    SortOrder,
    UserStatus,
)
from app.core.units import MICRO
from app.models.ledger import Account
from app.models.market import Market, Resolution
from app.models.trading import Position, Trade
from app.models.user import User

MEASURE_COLUMNS: Final[Mapping[LeaderboardSort, str]] = {
    LeaderboardSort.CREDITS: "credits_micro",
    LeaderboardSort.WIN_RATE: "win_rate_micro",
    LeaderboardSort.TRADES: "trades",
    LeaderboardSort.AVG_PAYOUT_RATIO: "avg_payout_ratio_micro",
}
"""The column of `board` that each sort orders by."""


@dataclass(frozen=True, slots=True)
class LeaderboardFilter:
    """Whom a ranking leaves out. A value of None leaves that bound open; every bound
    that is set is inclusive."""

    degree_level: DegreeLevel | None = None
    degree_programme: str | None = None
    min_credits_micro: int | None = None
    max_credits_micro: int | None = None
    min_trades: int | None = None
    max_trades: int | None = None
    min_win_rate_micro: int | None = None
    max_win_rate_micro: int | None = None
    min_avg_payout_ratio_micro: int | None = None
    max_avg_payout_ratio_micro: int | None = None


@dataclass(frozen=True, slots=True)
class LeaderboardRow:
    """One user of a ranking, in integers. A rate is in millionths, and None for a user
    without a settled market."""

    rank: int
    user_id: UUID
    username: str
    degree_level: DegreeLevel
    degree_programme: str
    credits_micro: int
    trades: int
    settled_markets: int
    win_rate_micro: int | None
    avg_payout_ratio_micro: int | None


class LeaderboardRepository:
    """Not a `BaseRepository`: it reads across aggregates and owns no table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rank(
        self,
        *,
        sort: LeaderboardSort,
        order: SortOrder,
        filters: LeaderboardFilter,
        after_rank: int,
        limit: int,
        viewer_id: UUID,
    ) -> Sequence[LeaderboardRow]:
        """A stretch of the ranking and the viewer's own row, in one statement.

        Returns, in rank order, the rows ranked `after_rank + 1` to `after_rank + limit`
        of the filtered, sorted list, and the row of `viewer_id` wherever it ranks. The
        viewer's row is there once, also when it lies inside the stretch, and is missing
        when the filters leave the viewer out. One statement reads one snapshot, so the
        ranks of a result agree with each other.

        The statement, as common table expressions (the definitions are those of
        `LeaderboardService.ranking`):

        1. `settled`: one row per user and RESOLVED market the user put credits into.
           `positions` joined to `markets` (`status = 'RESOLVED'`) and to `resolutions`
           (`outcome` YES or NO), grouped by user and market, `HAVING SUM(cost_micro) >
           0`. `cost` is `SUM(cost_micro)`; `returned` is `SUM(proceeds_micro)` plus
           `SUM(shares_micro) FILTER (WHERE positions.outcome = resolutions.outcome)`,
           0 when the user holds nothing of the winning outcome. Both stay NUMERIC.
        2. `rated`: one row per user who has such a market. `settled_markets =
           COUNT(*)`; `win_rate_micro = floor(wins * 1000000 / settled_markets)` with
           `wins = COUNT(*) FILTER (WHERE returned > cost)`; `avg_payout_ratio_micro =
           div(SUM(returned) * 1000000, SUM(cost))`. `div` on NUMERIC is the exact
           truncated quotient.
        3. `traded`: `COUNT(*)` of `trades` per user.
        4. `board`: every user whose status is ACTIVE, joined to their account (`kind =
           'USER'`), left-joined to `traded` and `rated`. Credits are the account's
           `balance_micro`; `trades` and `settled_markets` are 0 for a user who is in
           neither; the two rates stay NULL for a user who is not in `rated`.
        5. `ranked`: `board` narrowed by `filters`, each bound only when it is set,
           with `ROW_NUMBER() OVER (ORDER BY <measure> <order> NULLS LAST,
           users.created_at ASC, users.id ASC)` as `rank`. The measure is the column
           of `sort`. NULLS LAST holds in both directions. A bound on a rate drops
           the users whose rate is NULL, because no comparison with NULL is true.
        6. The result: the rows of `ranked` with `after_rank < rank <= after_rank +
           limit`, or with `user_id = viewer_id`, ordered by `rank`.

        Two things keep it as fast with filters as without. Postgres plans a statement
        it has run a few times for its parameters as such, without their values:

        * The values that belong to the statement itself (the statuses, the account
          kind, the million) are written into its text, not bound. The planner then
          knows how many markets are RESOLVED, and that `kind = 'USER'` lets it use
          the partial index on `accounts.owner_id`.
        * `board` is MATERIALIZED: it is computed for every ACTIVE user, and the
          filters are applied to the result. Left to itself the planner moves a bound
          it does not know the value of into the joins, expects it to leave a single
          user, and then joins `traded` and `rated` row by row. On 5,000 users that
          took a second where this takes a tenth.

        Three things to get right in SQLAlchemy:

        * `/` between integers renders a division by a NUMERIC cast. Use `//`, or
          `func.div`, where the quotient is to be truncated.
        * The sums of step 1 must not go through `sum_micro`, which casts to BIGINT:
          `returned * 1000000` does not fit one for a large position.
        * Bind every bound as BIGINT (`literal(value, BigInteger)`). An untyped Python
          int is bound as INTEGER, which a ratio bound above 2147.48 overflows.

        `win_rate_micro` is a quotient of BIGINTs and arrives as an `int`.
        `avg_payout_ratio_micro` is a quotient of NUMERICs and arrives as an integral
        `Decimal`: convert it with `int()`. Either is None for a user without a
        settled market.
        """
        statement = ranking_statement(
            sort=sort,
            order=order,
            filters=filters,
            after_rank=after_rank,
            limit=limit,
            viewer_id=viewer_id,
        )
        return [
            LeaderboardRow(
                rank=rank,
                user_id=user_id,
                username=username,
                degree_level=degree_level,
                degree_programme=degree_programme,
                credits_micro=credits_micro,
                trades=trades,
                settled_markets=settled_markets,
                win_rate_micro=win_rate_micro,
                avg_payout_ratio_micro=(
                    None if avg_payout_ratio_micro is None else int(avg_payout_ratio_micro)
                ),
            )
            for (
                rank,
                user_id,
                username,
                degree_level,
                degree_programme,
                credits_micro,
                trades,
                settled_markets,
                win_rate_micro,
                avg_payout_ratio_micro,
            ) in await self._session.execute(statement)
        ]


def ranking_statement(
    *,
    sort: LeaderboardSort,
    order: SortOrder,
    filters: LeaderboardFilter,
    after_rank: int,
    limit: int,
    viewer_id: UUID,
) -> Select[Any]:
    """The statement `LeaderboardRepository.rank` executes; its docstring numbers the
    steps. The columns come back in the order of the fields of `LeaderboardRow`."""
    board = _board(_traded(), _rated(_settled()))

    # Step 5: the filtered list, numbered in the order asked for.
    measure = board.c[MEASURE_COLUMNS[sort]]
    by_measure = measure.asc() if order is SortOrder.ASC else measure.desc()
    position = func.row_number().over(
        order_by=[by_measure.nulls_last(), board.c.created_at.asc(), board.c.user_id.asc()]
    )
    ranked = select(board, position.label("rank")).where(*_conditions(board, filters)).cte("ranked")

    # Step 6: the stretch asked for, and the viewer's row.
    in_stretch = (ranked.c.rank > _bigint(after_rank)) & (
        ranked.c.rank <= _bigint(after_rank + limit)
    )
    return (
        select(
            ranked.c.rank,
            ranked.c.user_id,
            ranked.c.username,
            ranked.c.degree_level,
            ranked.c.degree_programme,
            ranked.c.credits_micro,
            ranked.c.trades,
            ranked.c.settled_markets,
            ranked.c.win_rate_micro,
            ranked.c.avg_payout_ratio_micro,
        )
        .where(or_(in_stretch, ranked.c.user_id == viewer_id))
        .order_by(ranked.c.rank)
    )


def _bigint(value: int) -> BindParameter[int]:
    """A bound as BIGINT. Left untyped, an int is bound as INTEGER and one above
    2,147,483,647 does not fit."""
    return literal(value, BigInteger)


def _inline[T](value: T, type_: TypeEngine[T]) -> BindParameter[T]:
    """A value that belongs to the statement itself, written into its text instead of
    being bound, so that the planner sees it (see `LeaderboardRepository.rank`).

    Only values this module names are written this way, never one a request carries.
    """
    return literal(value, type_, literal_execute=True)


_ZERO: Final[BindParameter[int]] = _inline(0, BigInteger())
_MILLION: Final[BindParameter[int]] = _inline(MICRO, BigInteger())
"""Millionths per unit: what turns a quotient into a rate in whole millionths."""


def _settled() -> CTE:
    """Step 1: per user and RESOLVED market the user put credits into, what went in and
    what came back, in NUMERIC."""
    cost = func.sum(Position.cost_micro)
    winning_shares = func.sum(Position.shares_micro).filter(Position.outcome == Resolution.outcome)
    returned = func.sum(Position.proceeds_micro) + func.coalesce(winning_shares, _ZERO)
    return (
        select(
            Position.user_id,
            Position.market_id,
            cast(cost, Numeric).label("cost_micro"),
            cast(returned, Numeric).label("returned_micro"),
        )
        .join(Market, Market.id == Position.market_id)
        .join(Resolution, Resolution.market_id == Market.id)
        .where(
            Market.status == _inline(MarketStatus.RESOLVED, Market.status.type),
            Resolution.outcome.in_(
                [
                    _inline(ResolutionOutcome.YES, Resolution.outcome.type),
                    _inline(ResolutionOutcome.NO, Resolution.outcome.type),
                ]
            ),
        )
        .group_by(Position.user_id, Position.market_id)
        .having(cost > _ZERO)
        .cte("settled")
    )


def _rated(settled: CTE) -> CTE:
    """Step 2: per user with a settled market, how many there are and the two rates in
    whole millionths."""
    markets = func.count()
    wins = func.count().filter(settled.c.returned_micro > settled.c.cost_micro)
    # A quotient of two counts: `//` keeps it an integer division of BIGINTs.
    win_rate = wins * _MILLION // markets
    # NUMERIC throughout: a sum of BIGINTs times a million does not fit a BIGINT.
    payout_ratio = func.div(
        func.sum(settled.c.returned_micro) * _MILLION,
        func.sum(settled.c.cost_micro),
        type_=Numeric,
    )
    return (
        select(
            settled.c.user_id,
            markets.label("settled_markets"),
            win_rate.label("win_rate_micro"),
            payout_ratio.label("avg_payout_ratio_micro"),
        )
        .group_by(settled.c.user_id)
        .cte("rated")
    )


def _traded() -> CTE:
    """Step 3: per user who ever traded, the number of their trades."""
    return select(Trade.user_id, func.count().label("trades")).group_by(Trade.user_id).cte("traded")


def _board(traded: CTE, rated: CTE) -> CTE:
    """Step 4: every ACTIVE user with their measures. MATERIALIZED, so that no filter
    is planned into its joins."""
    return (
        select(
            User.id.label("user_id"),
            User.username,
            User.degree_level,
            User.degree_programme,
            User.created_at,
            Account.balance_micro.label("credits_micro"),
            func.coalesce(traded.c.trades, _ZERO).label("trades"),
            func.coalesce(rated.c.settled_markets, _ZERO).label("settled_markets"),
            rated.c.win_rate_micro,
            rated.c.avg_payout_ratio_micro,
        )
        .join(
            Account,
            (Account.owner_id == User.id)
            & (Account.kind == _inline(AccountKind.USER, Account.kind.type)),
        )
        .outerjoin(traded, traded.c.user_id == User.id)
        .outerjoin(rated, rated.c.user_id == User.id)
        .where(User.status == _inline(UserStatus.ACTIVE, User.status.type))
        .cte("board")
        .prefix_with("MATERIALIZED")
    )


def _conditions(board: CTE, filters: LeaderboardFilter) -> list[ColumnElement[bool]]:
    """The filters that are set, as conditions on `board`. A comparison with a NULL rate
    is not true, so a bound on a rate also drops everybody who has none."""
    conditions: list[ColumnElement[bool]] = []
    if filters.degree_level is not None:
        conditions.append(board.c.degree_level == filters.degree_level)
    if filters.degree_programme is not None:
        conditions.append(board.c.degree_programme == filters.degree_programme)
    bounds = (
        (board.c.credits_micro, filters.min_credits_micro, filters.max_credits_micro),
        (board.c.trades, filters.min_trades, filters.max_trades),
        (board.c.win_rate_micro, filters.min_win_rate_micro, filters.max_win_rate_micro),
        (
            board.c.avg_payout_ratio_micro,
            filters.min_avg_payout_ratio_micro,
            filters.max_avg_payout_ratio_micro,
        ),
    )
    for column, lowest, highest in bounds:
        if lowest is not None:
            conditions.append(column >= _bigint(lowest))
        if highest is not None:
            conditions.append(column <= _bigint(highest))
    return conditions
