"""The leaderboard: users ranked by credits, win rate, trades or payout ratio (SPEC §10, A.2).

Nothing is stored for it and nothing is cached. Every request computes the ranking from
the accounts, trades, positions and resolutions as they are, in one statement
(`LeaderboardRepository.rank`), so the ranks of an answer agree with each other.

The frontend ranks, sorts, filters and rounds nothing (golden rule 6): what it sends is
the query, and what it shows is the answer in the order it arrives.
"""

import re
from decimal import Decimal
from typing import Final
from uuid import UUID

from app.core.config import Settings
from app.core.errors import ValidationFailedError
from app.core.identity import AuthenticatedUser
from app.core.units import MICRO, ceil_micro, display_probability, floor_micro, to_cr, to_micro
from app.repositories.leaderboard import LeaderboardFilter, LeaderboardRow
from app.schemas.leaderboard import LeaderboardEntry, LeaderboardQuery, LeaderboardResponse
from app.services import UowFactory

PAYOUT_RATIO_PLACES: Final[int] = 2
"""Decimal places of `avg_payout_ratio` on the wire. A ratio is cut to them, never
rounded up: nobody is shown a payout they did not reach."""

CURSOR_MAX: Final[int] = 1_000_000_000
"""The largest rank a cursor may name. It only keeps the number inside what the
database can count with; a list never gets that long."""

_CURSOR: Final[re.Pattern[str]] = re.compile(r"[0-9]+")
"""A cursor as this service writes one: a whole number in ASCII digits, without a sign,
a separator or a space."""

_RATIO_STEPS_PER_UNIT: Final[int] = 10**PAYOUT_RATIO_PLACES
"""How many steps of its last place a payout ratio of 1 has on the wire: 100."""

_MILLIONTHS_PER_RATIO_STEP: Final[int] = MICRO // _RATIO_STEPS_PER_UNIT
"""How many millionths one such step stands for: 10,000."""


class LeaderboardService:
    def __init__(self, uow_factory: UowFactory, settings: Settings) -> None:
        self._uow_factory = uow_factory
        self._display_decimals = settings.pricing.price_display_decimals

    async def ranking(
        self, viewer: AuthenticatedUser, query: LeaderboardQuery
    ) -> LeaderboardResponse:
        """One page of the ranking, and the viewer's own row (`GET /leaderboard`).

        Who is ranked: every user whose status is ACTIVE, whether or not they ever
        traded. A suspended user is not listed.

        The measures of a user:

        * Credits: the balance of their account, `accounts.balance_micro`.
        * Trades: the number of their rows in `trades`, over every market and
          whatever became of it; 0 for someone who never traded.
        * `settled_markets`: the number n of markets that are RESOLVED and into which
          the user put credits (the sum of `positions.cost_micro` over both outcomes
          is above 0). A VOID market is not one of them, and neither is one that is
          not settled yet.
        * Win rate and average payout ratio, over those n markets. For each market:
          `cost` is the sum of `cost_micro` over the user's positions in it;
          `returned` is the sum of `proceeds_micro` over them, plus `shares_micro` of
          the position on the winning outcome (a winning share pays 1 CR, and a
          resolved position keeps its shares, A.5). The market is a win when
          `returned > cost`, strictly: selling out at exactly the cost is no win.
          Then, in whole millionths:

              win_rate_micro         = floor(wins · 10⁶ / n)
              avg_payout_ratio_micro = floor(Σ returned · 10⁶ / Σ cost)

          The ratio is the cost-weighted mean: a market weighs as much as was put into
          it, so a trade of the minimum size cannot move it. The database computes
          the sums and the ratio in NUMERIC, because a sum of BIGINTs times 10⁶ does
          not fit a BIGINT; the win rate is a quotient of two counts.
        * A user with n = 0 has no rate: both are null.

        What the API sends: `credits_cr = to_cr(credits_micro)`; `win_rate =
        display_probability(win_rate_micro, PRICE_DISPLAY_DECIMALS)`, the rounding of
        every probability; `avg_payout_ratio` cut to `PAYOUT_RATIO_PLACES` places,
        which for two places is `float(Decimal(avg_payout_ratio_micro // 10_000) /
        100)`: 1,256,789 millionths are sent as 1.25. Both rates are JSON numbers, or
        null.

        Filters, from `query`. All are inclusive, all are optional, and every one that
        is given must hold:

        * `degree_level` and `degree_programme`: equal to the user's columns, as they
          are stored. Neither is checked against the catalogue; a pair nobody has
          gives an empty list.
        * `min_credits_cr` and `max_credits_cr`: against `credits_micro`, through
          `to_micro`.
        * `min_trades` and `max_trades`: against the number of trades.
        * `min_win_rate`, `max_win_rate`, `min_avg_payout_ratio` and
          `max_avg_payout_ratio`: against the unrounded millionths, not against what a
          row shows. The bound is the decimal the client wrote, `Decimal(str(value))`,
          times 10⁶; a lower bound is rounded up to a whole millionth (`ceil_micro`)
          and an upper bound down (`floor_micro`), so the comparison of integers says
          what the comparison of the exact numbers would.
        * A bound on either rate leaves out every user who has none.

        Order: by the measure of `query.sort`, `desc` or `asc` as `query.order` says.
        Users without a value for it (a rate, with n = 0) come last in both
        directions. Ties are broken by `users.created_at`, the earlier first, then by
        `users.id`, in both directions.

        Rank: the position in the filtered, ordered list, counted from 1
        (`ROW_NUMBER()`). A filter therefore changes ranks: the first of the Bachelor
        students is rank 1 of that list.

        Paging. The cursor is the rank the previous page ended at, as decimal text;
        without one the page starts at rank 1. A cursor that is not a whole number
        from 0 to `CURSOR_MAX` raises `ValidationFailedError` (422) with
        `details={"field": "cursor"}`. A whole number is ASCII digits and nothing
        else: a sign, an underscore or a digit of another script is refused, although
        `int()` would read it. With `after` the cursor's number:

        1. One read transaction, one statement: `leaderboard.rank(sort=query.sort,
           order=query.order, filters=LeaderboardFilter(...), after_rank=after,
           limit=query.limit + 1, viewer_id=viewer.id)`.
        2. `items` are the rows ranked `after + 1` to `after + query.limit`.
           `next_cursor` is `str(after + query.limit)` when a row ranked `after +
           query.limit + 1` came back, else null.
        3. `viewer` is the row whose `user_id` is `viewer.id`, wherever it ranks, also
           outside the page; null when the filters leave the viewer out. It is the
           same list as `items`: same filters, same order, same ranks.
        4. `is_viewer` is true on that row, in `items` and as `viewer`.

        Each page is ranked when it is asked for. A balance that moves between two
        requests can shift a user across a page boundary; the client shows what it
        gets.
        """
        after = _after_rank(query.cursor)
        filters = _filters(query)

        async with self._uow_factory() as uow:
            rows = await uow.leaderboard.rank(
                sort=query.sort,
                order=query.order,
                filters=filters,
                after_rank=after,
                limit=query.limit + 1,
                viewer_id=viewer.id,
            )

        last = after + query.limit
        return LeaderboardResponse(
            items=[self._entry(row, viewer.id) for row in rows if after < row.rank <= last],
            next_cursor=str(last) if any(row.rank == last + 1 for row in rows) else None,
            viewer=next(
                (self._entry(row, viewer.id) for row in rows if row.user_id == viewer.id), None
            ),
        )

    def _entry(self, row: LeaderboardRow, viewer_id: UUID) -> LeaderboardEntry:
        """A row of the ranking as the API sends it: credits as an amount, the win rate
        rounded like a probability, the payout ratio cut to its places."""
        return LeaderboardEntry(
            rank=row.rank,
            username=row.username,
            degree_level=row.degree_level,
            degree_programme=row.degree_programme,
            credits_cr=to_cr(row.credits_micro),
            trades=row.trades,
            settled_markets=row.settled_markets,
            win_rate=(
                None
                if row.win_rate_micro is None
                else display_probability(row.win_rate_micro, self._display_decimals)
            ),
            avg_payout_ratio=(
                None
                if row.avg_payout_ratio_micro is None
                else _cut_payout_ratio(row.avg_payout_ratio_micro)
            ),
            is_viewer=row.user_id == viewer_id,
        )


def _after_rank(cursor: str | None) -> int:
    """The rank a page starts after: 0 without a cursor, else the cursor's number."""
    if cursor is None:
        return 0
    if _CURSOR.fullmatch(cursor) is None or int(cursor) > CURSOR_MAX:
        raise ValidationFailedError(details={"field": "cursor"})
    return int(cursor)


def _filters(query: LeaderboardQuery) -> LeaderboardFilter:
    """The filters of a query in the integers the repository compares with."""
    return LeaderboardFilter(
        degree_level=query.degree_level,
        degree_programme=query.degree_programme,
        min_credits_micro=None if query.min_credits_cr is None else to_micro(query.min_credits_cr),
        max_credits_micro=None if query.max_credits_cr is None else to_micro(query.max_credits_cr),
        min_trades=query.min_trades,
        max_trades=query.max_trades,
        min_win_rate_micro=_lowest_micro(query.min_win_rate),
        max_win_rate_micro=_highest_micro(query.max_win_rate),
        min_avg_payout_ratio_micro=_lowest_micro(query.min_avg_payout_ratio),
        max_avg_payout_ratio_micro=_highest_micro(query.max_avg_payout_ratio),
    )


def _millionths(bound: float) -> Decimal:
    """A bound on a rate in millionths, exactly as the client wrote it: the shortest
    decimal that reads back as this float, so 0.1 is a tenth and nothing beside it."""
    return Decimal(str(bound)) * MICRO


def _lowest_micro(bound: float | None) -> int | None:
    """The smallest whole number of millionths that is at least `bound`."""
    return None if bound is None else ceil_micro(_millionths(bound))


def _highest_micro(bound: float | None) -> int | None:
    """The largest whole number of millionths that is at most `bound`."""
    return None if bound is None else floor_micro(_millionths(bound))


def _cut_payout_ratio(avg_payout_ratio_micro: int) -> float:
    """A payout ratio as the API sends it: cut to `PAYOUT_RATIO_PLACES` places, never
    rounded up. The cut is taken on the integer, so no float decides a digit."""
    steps = avg_payout_ratio_micro // _MILLIONTHS_PER_RATIO_STEP
    return float(Decimal(steps) / _RATIO_STEPS_PER_UNIT)
