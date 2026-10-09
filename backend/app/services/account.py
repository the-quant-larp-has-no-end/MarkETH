"""What a signed-in user reads about themselves: `/me` and its lists (SPEC §10, A.2).

Every method is a read in one transaction; nothing here writes. Amounts leave as
`*_cr` decimals built with `to_cr`, prices through `MarketView`.
"""

from collections.abc import Mapping
from typing import Final

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import JournalKind
from app.core.identity import AuthenticatedUser
from app.schemas.common import PageQuery
from app.schemas.me import (
    LedgerListResponse,
    MeResponse,
    PortfolioResponse,
    ProposalListResponse,
    TradeListResponse,
)
from app.services import UowFactory
from app.services.grants import GrantService
from app.services.market_view import MarketView

LEDGER_DESCRIPTIONS: Final[Mapping[JournalKind, str]] = {
    JournalKind.SIGNUP_GRANT: "Welcome vouchers",
    JournalKind.MONTHLY_GRANT: "Monthly voucher",
    JournalKind.CODE_REDEMPTION: "Voucher code",
    JournalKind.TRADE_BUY: "Bought shares",
    JournalKind.TRADE_SELL: "Sold shares",
    JournalKind.MARKET_RESOLUTION: "Market payout",
    JournalKind.MARKET_VOID: "Market voided, stake refunded",
    JournalKind.COFFEE_TICKET_ISSUE: "Coffee ticket",
    JournalKind.COFFEE_TICKET_REDEEM: "Coffee ticket redeemed",
    JournalKind.COFFEE_TICKET_REFUND: "Coffee ticket expired, refunded",
}
"""`LedgerItem.description` of a line, by the kind of its journal entry."""


class AccountService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        grants: GrantService,
        view: MarketView,
    ) -> None:
        self._uow_factory = uow_factory
        self._monthly_grant_micro = settings.vouchers.monthly_grant_micro
        self._coffee_ticket_value_micro = settings.vouchers.coffee_ticket_value_micro
        self._clock = clock
        self._grants = grants
        self._view = view

    async def me(self, user: AuthenticatedUser) -> MeResponse:
        """Profile, balance and the next monthly grant (`GET /me`).

        `id`, `email`, `role` and `created_at` come from the `users` row, `balance_cr`
        from the user's account, `can_get_coffee` is `balance >=
        COFFEE_TICKET_VALUE_CR`, `monthly_grant_cr` comes from `MONTHLY_GRANT_CR` and
        `next_monthly_grant_on` from `GrantService.next_grant_date()`. A user whose row
        or account is gone raises `UnauthenticatedError` (401).
        """
        raise NotImplementedError

    async def portfolio(self, user: AuthenticatedUser) -> PortfolioResponse:
        """The user's positions, one entry per market (`GET /me/positions`).

        1. `positions.list_for_user(user.id)`, grouped by market. Every market with a
           position row is listed, also one whose shares were all sold.
        2. Load the markets and the resolutions of the settled ones in two queries,
           whatever the number of markets: `markets.get_many(<market ids>)` and
           `resolutions.get_many_by_market(<market ids>)`. Build each entry with
           `MarketView`:
           `market = summary(...)`, `holdings = holdings(...)`, `value_cr` the sum of
           the two holding values, `cost_cr` and `proceeds_cr` the sums over both
           positions, `net_stake_cr = net_stake_micro(...)`.
        3. `payout_cr` is set only for a RESOLVED market: the shares held of the
           winning outcome. `refund_cr` is set only for a VOID market: the net stake.
        4. Order: markets that are not settled first, soonest `close_at` first; then
           settled ones, latest `close_at` first; ties by market id.

        `total_value_cr` sums `value_cr` over the markets that are not settled.
        """
        raise NotImplementedError

    async def trades(self, user: AuthenticatedUser, page: PageQuery) -> TradeListResponse:
        """The user's trades, newest first (`GET /me/trades`).

        The cursor is the id of the last trade of the previous page, as text. One that
        is not a UUID raises `ValidationFailedError` (422).

        1. `trades.list_for_user(user.id, limit=page.limit + 1, before_id=<cursor>)`.
        2. Return the first `page.limit` rows. `next_cursor` is the id of the last
           returned row when one more row came back, else null.
        3. Each item carries its market's question (`markets.get_many` for the page's
           market ids, one query),
           `average_price = MarketView.average_price(amount, shares)` and the display
           values of `p_yes_before_micro` and `p_yes_after_micro`
           (`MarketView.probabilities(...)[0]`).
        """
        raise NotImplementedError

    async def ledger(self, user: AuthenticatedUser, page: PageQuery) -> LedgerListResponse:
        """Every movement on the user's balance, newest first (`GET /me/ledger`).

        The cursor is the id of the last posting of the previous page, as decimal
        text. One that is not a positive integer raises `ValidationFailedError` (422).

        1. Load the user's account; `journal.list_postings_for_account(account.id,
           limit=page.limit + 1, before_id=<cursor>)`.
        2. Page as in `trades`. An item is `id` = the posting's id, `kind` = the
           entry's kind, `amount_cr` = the posting's signed amount, `description` =
           `LEDGER_DESCRIPTIONS[kind]`, `created_at` = the entry's.
        """
        raise NotImplementedError

    async def proposals(self, user: AuthenticatedUser) -> ProposalListResponse:
        """The markets the user proposed, in every status, newest first
        (`GET /me/proposals`): `markets.list_created_by(user.id)`, each with its
        effective status (`MarketView.effective_status`).
        """
        raise NotImplementedError
