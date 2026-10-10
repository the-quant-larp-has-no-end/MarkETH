"""The leaderboard (SPEC §10, §11, Appendix A.2).

`GET /leaderboard` ranks the users by one of four measures: credits, win rate, number of
trades and average payout ratio. The server sorts, filters and ranks; a client sends its
choice as the query and shows the rows in the order they arrive (golden rule 6).

The two rates are JSON numbers, already rounded for display like a probability. They are
not amounts: a rate is a quotient, and no balance is ever computed from one. Credits are
decimal strings like every `*_cr` field.
"""

from typing import Annotated, Final

from pydantic import Field

from app.core.degrees import PROGRAMME_MAX_LENGTH
from app.core.enums import DegreeLevel, LeaderboardSort, SortOrder
from app.schemas.common import (
    CreditAmount,
    CursorPage,
    PageQuery,
    Probability,
    ResponseModel,
    in_its_own_words,
)

TRADES_FILTER_MAX: Final[int] = 1_000_000_000
"""The largest number of trades a filter may name. It only keeps a bound inside what the
database can compare; nobody makes that many trades."""

PAYOUT_RATIO_FILTER_MAX: Final[int] = 1_000_000
"""The largest payout ratio a filter may name, for the same reason."""

MEASURES_WITH_BOUNDS: Final[tuple[str, ...]] = (
    "credits_cr",
    "trades",
    "win_rate",
    "avg_payout_ratio",
)
"""What a query can bound from below (`min_…`) and from above (`max_…`)."""

PayoutRatio = Annotated[float, Field(ge=0)]
"""Credits returned per credit put in, cut to two places: 1.25 is a quarter more than
was put in, 0 is everything lost."""

NOT_A_PERCENTAGE_MESSAGE: Final[str] = "Enter a percentage between 0 and 100."
"""What a bound on the win rate says for anything that is no fraction from 0 to 1. The
filter is typed in percent, and the sentence is in the unit the person typed."""

NOT_A_RATIO_MESSAGE: Final[str] = "Enter a number, for example 1.25."
"""What a bound on the payout ratio says for something that is no number."""

WinRateBound = Annotated[
    float,
    Field(ge=0, le=1, allow_inf_nan=False),
    in_its_own_words(
        float_type=NOT_A_PERCENTAGE_MESSAGE,
        float_parsing=NOT_A_PERCENTAGE_MESSAGE,
        finite_number=NOT_A_PERCENTAGE_MESSAGE,
        greater_than_equal=NOT_A_PERCENTAGE_MESSAGE,
        less_than_equal=NOT_A_PERCENTAGE_MESSAGE,
    ),
]
"""A bound on the win rate, as the fraction it is sent as: 0.5 is 50 %."""

PayoutRatioBound = Annotated[
    float,
    Field(ge=0, le=PAYOUT_RATIO_FILTER_MAX, allow_inf_nan=False),
    in_its_own_words(
        float_type=NOT_A_RATIO_MESSAGE,
        float_parsing=NOT_A_RATIO_MESSAGE,
        finite_number=NOT_A_RATIO_MESSAGE,
    ),
]
"""A bound on the payout ratio: credits returned per credit put in."""


class LeaderboardQuery(PageQuery):
    """Query string of `GET /leaderboard`: the order, and whom to leave out.

    Every bound is inclusive and optional, and all that are given must hold. A bound on
    `win_rate` or on `avg_payout_ratio` also leaves out everybody who has no rate yet
    (`settled_markets` of 0). A `min_…` above its `max_…` is rejected by the service:
    422 with the `min_…` in `details.field`.
    """

    sort: LeaderboardSort = LeaderboardSort.CREDITS
    """The measure to order by. Users who have no value for it (a rate without a
    resolved market) come last, whatever `order` is."""

    order: SortOrder = SortOrder.DESC
    """`desc` puts the highest first."""

    min_credits_cr: CreditAmount | None = None
    max_credits_cr: CreditAmount | None = None
    min_trades: int | None = Field(default=None, ge=0, le=TRADES_FILTER_MAX)
    max_trades: int | None = Field(default=None, ge=0, le=TRADES_FILTER_MAX)
    min_win_rate: WinRateBound | None = None
    """A fraction: 0.5 is half of the markets. It is compared with the exact rate, not
    with the rounded one a row shows."""

    max_win_rate: WinRateBound | None = None
    min_avg_payout_ratio: PayoutRatioBound | None = None
    """Compared with the exact ratio, not with the one a row shows cut to two places."""

    max_avg_payout_ratio: PayoutRatioBound | None = None
    degree_level: DegreeLevel | None = None
    degree_programme: str | None = Field(
        default=None, min_length=1, max_length=PROGRAMME_MAX_LENGTH
    )
    """A label as `GET /config` lists it. Users are matched on what their profile says;
    a label nobody has gives an empty list, not an error."""


class LeaderboardEntry(ResponseModel):
    """One user on the leaderboard: a username and a degree, never an address or a real
    name."""

    rank: int
    """The user's place in the list as it was asked for, 1 being first. It counts within
    the filters and in the chosen order."""

    username: str
    degree_level: DegreeLevel
    degree_programme: str
    """A label of `degree_level`, as `GET /config` lists it."""

    credits_cr: CreditAmount
    """What the user has right now: `balance_cr` plus `positions_value_cr`. It is what
    the `credits` sort orders by and what `min_credits_cr` and `max_credits_cr` bound."""

    balance_cr: CreditAmount
    """The part in the wallet: the balance of the user's account."""

    positions_value_cr: CreditAmount
    """The part in open positions: the shares the user holds in markets that are not
    yet RESOLVED or VOID, each holding at the current price of its outcome and rounded
    down. It is the `total_value_cr` of that user's `GET /me/positions`. 0 for someone
    who holds nothing."""

    trades: int
    """How many trades the user made, over every market."""

    settled_markets: int
    """How many RESOLVED markets the user put credits into: what the two rates are
    taken over. A VOID market does not count."""

    win_rate: Probability | None
    """The share of those markets that returned more than the user put in, rounded like
    a probability. Null while `settled_markets` is 0."""

    avg_payout_ratio: PayoutRatio | None
    """What those markets returned per credit put in, each market weighing as much as
    was put into it. Null while `settled_markets` is 0."""

    is_viewer: bool
    """True for the caller's own row."""


class LeaderboardResponse(CursorPage[LeaderboardEntry]):
    """`GET /leaderboard`: one page of the ranking, first rank first.

    `next_cursor` is opaque to the client and null on the last page. A page is ranked at
    the moment of its request: balances move, so two pages fetched apart may not join
    without a gap or a repeat.
    """

    viewer: LeaderboardEntry | None
    """The caller's own row under the same filters and order, wherever it ranks, also
    beyond this page. Null when the filters leave the caller out."""
