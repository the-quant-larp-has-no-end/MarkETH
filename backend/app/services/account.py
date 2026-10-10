"""What a user reads about themselves: `/me` and its lists (SPEC §10, A.2).

Every method is a read in one transaction; nothing here writes. Amounts leave as
`*_cr` decimals built with `to_cr`, prices through `MarketView`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import JournalKind, ResolutionOutcome
from app.core.errors import ValidationFailedError
from app.core.identity import AuthenticatedUser
from app.core.units import to_cr, to_micro
from app.models.market import Market, Resolution
from app.models.trading import Position
from app.schemas.common import PageQuery
from app.schemas.me import (
    LedgerItem,
    LedgerListResponse,
    MeResponse,
    PortfolioEntry,
    PortfolioResponse,
    ProposalItem,
    ProposalListResponse,
    TradeItem,
    TradeListResponse,
)
from app.services import UowFactory
from app.services.grants import GrantService
from app.services.ledger import LedgerRef
from app.services.market_view import SETTLED_STATUSES, MarketView
from app.services.notices import NoticeService

LEDGER_DESCRIPTIONS: Final[Mapping[JournalKind, str]] = {
    JournalKind.SIGNUP_GRANT: "Welcome tokens",
    JournalKind.MONTHLY_GRANT: "Monthly tokens",
    JournalKind.CODE_REDEMPTION: "Code redeemed",
    JournalKind.TRADE_BUY: "Bought shares",
    JournalKind.TRADE_SELL: "Sold shares",
    JournalKind.MARKET_RESOLUTION: "Market payout",
    JournalKind.MARKET_VOID: "Market voided, stake refunded",
    JournalKind.VOUCHER_ISSUE: "Voucher",
    JournalKind.VOUCHER_REDEEM: "Voucher redeemed",
    JournalKind.VOUCHER_REFUND: "Voucher expired, refunded",
    JournalKind.ADMIN_ADJUSTMENT: "Adjustment by VIS",
    JournalKind.STREAK_REWARD: "Streak tokens",
    JournalKind.STREAK_BONUS: "Streak bonus",
}
"""What a ledger line is called, by the kind of its journal entry. A line about a
voucher also says what the voucher is for: `ledger_description` puts the two together."""

CURSOR_FIELD: Final[str] = "cursor"
"""`details.field` of the `ValidationFailedError` that refuses a cursor."""

MAX_POSTING_ID: Final[int] = 2**63 - 1
"""The largest `postings.id` there can be: the column is a BIGINT."""


def ledger_description(kind: JournalKind, product_name: str | None = None) -> str:
    """`LedgerItem.description` of a line.

    `product_name` is the product of the voucher an entry is about and None for every
    other entry: "Voucher: Mate" and "Voucher expired, refunded: Mate", but "Monthly
    tokens".
    """
    label = LEDGER_DESCRIPTIONS[kind]
    return label if product_name is None else f"{label}: {product_name}"


@dataclass(frozen=True, slots=True)
class _PortfolioRow:
    """One market of a portfolio: what it is ordered by, shown as and counted with."""

    market: Market
    entry: PortfolioEntry
    value_micro: int
    settled: bool
    """Whether the market's row says RESOLVED or VOID."""


class AccountService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        grants: GrantService,
        view: MarketView,
        notices: NoticeService,
    ) -> None:
        self._uow_factory = uow_factory
        self._monthly_grant_micro = settings.grants.monthly_grant_micro
        self._lowest_voucher_price_micro = settings.vouchers.lowest_price_micro
        self._clock = clock
        self._grants = grants
        self._view = view
        self._notices = notices

    async def me(self, user: AuthenticatedUser) -> MeResponse:
        """Profile, balance, the next monthly grant and what the user has not been told
        yet (`GET /me`).

        One read transaction: the `users` row, the user's account and their unseen
        notices. `id`, `email`, `name`, `username`, `degree_level`, `degree_programme`,
        `created_at` and `policy_accepted_at` come from the row. `role` is `user.role`,
        the role this request was let in with: the row's own, or ADMIN for a bootstrap
        admin (`IdentityService.authenticate`). `balance_cr` comes from the account.
        `can_get_voucher` is `balance >= settings.vouchers.lowest_price_micro`: the
        balance covers the cheapest product of `VOUCHER_PRODUCTS`. Which products it
        covers is `VoucherService.products`. `monthly_grant_cr` comes from
        `MONTHLY_GRANT_CR` and `next_monthly_grant_at` from
        `GrantService.next_grant_at(<the row>)`. `notices` are the user's notices
        that are not marked seen, oldest first (`NoticeService.unseen`): an empty list
        for most requests, and the approval of a proposal of theirs until the app has
        shown it. Every user has a row and an account: the profile transaction creates
        the two together and nothing deletes either.

        `ProfileService.save` answers with this too, right after its commit.
        """
        async with self._uow_factory() as uow:
            row = await uow.users.get(user.id)
            account = await uow.accounts.get_user_account(user.id)
            notices = await self._notices.unseen(uow, user.id)
        if row is None:
            raise LookupError(f"user {user.id} has no row")
        if account is None:
            raise LookupError(f"user {user.id} has no ledger account")
        return MeResponse(
            id=row.id,
            email=row.email,
            name=row.name,
            username=row.username,
            degree_level=row.degree_level,
            degree_programme=row.degree_programme,
            role=user.role,
            created_at=row.created_at,
            policy_accepted_at=row.policy_accepted_at,
            balance_cr=to_cr(account.balance_micro),
            can_get_voucher=account.balance_micro >= self._lowest_voucher_price_micro,
            monthly_grant_cr=to_cr(self._monthly_grant_micro),
            next_monthly_grant_at=self._grants.next_grant_at(row),
            notices=notices,
        )

    async def portfolio(self, user: AuthenticatedUser) -> PortfolioResponse:
        """The user's positions, one entry per market (`GET /me/positions`).

        1. `positions.list_for_user(user.id)`, grouped by market. Every market with a
           position row is listed, also one whose shares were all sold.
        2. Load the markets and the resolutions of the settled ones in two queries,
           whatever the number of markets: `markets.get_many(<market ids>)` and
           `resolutions.get_many_by_market(<market ids>)`, in that order. A market is
           settled when its row says RESOLVED or VOID, and only then is its
           resolution used: a settlement that commits between the two queries shows
           with the next request, whole. Build each entry with `MarketView`:
           `market = summary(...)`, `holdings = holdings(...)`, `value_cr` the sum of
           the two holding values, `cost_cr` and `proceeds_cr` the sums over both
           positions, `net_stake_cr = net_stake_micro(...)`.
        3. `payout_cr = MarketView.payout_micro(<the positions>, <the resolution>)`,
           set only for a RESOLVED market: the shares held of the winning outcome.
           `refund_cr = MarketView.refund_micro(...)`, set only for a VOID market: the
           net stake. Both are null in every other status. `MarketService.detail`
           reports the same two figures for the same market.
        4. Order: markets that are not settled first, soonest `close_at` first; then
           settled ones, latest `close_at` first; ties by market id.

        `total_value_cr` sums `value_cr` over the markets that are not settled.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            by_market: dict[UUID, list[Position]] = {}
            for position in await uow.positions.list_for_user(user.id):
                by_market.setdefault(position.market_id, []).append(position)
            if not by_market:
                return PortfolioResponse(items=[], total_value_cr=to_cr(0))
            market_ids = list(by_market)
            markets = await uow.markets.get_many(market_ids)
            resolutions = await uow.resolutions.get_many_by_market(market_ids)

        rows: list[_PortfolioRow] = []
        for market_id, positions in by_market.items():
            market = markets[market_id]
            settled = market.status in SETTLED_STATUSES
            resolution = resolutions.get(market_id) if settled else None
            holdings = self._view.holdings(market, positions, resolution)
            value_micro = sum(to_micro(holding.value_cr) for holding in holdings)
            entry = PortfolioEntry(
                market=self._view.summary(market, now, resolution),
                holdings=holdings,
                value_cr=to_cr(value_micro),
                cost_cr=to_cr(sum(position.cost_micro for position in positions)),
                proceeds_cr=to_cr(sum(position.proceeds_micro for position in positions)),
                net_stake_cr=to_cr(self._view.net_stake_micro(positions)),
                payout_cr=_optional_cr(self._view.payout_micro(positions, resolution)),
                refund_cr=_optional_cr(self._view.refund_micro(positions, resolution)),
            )
            rows.append(_PortfolioRow(market, entry, value_micro, settled))

        unsettled = [row for row in rows if not row.settled]
        unsettled.sort(key=lambda row: (row.market.close_at, row.market.id))
        # Two stable passes: by id, then latest `close_at` first, which keeps the order
        # of the first pass among markets that close at the same instant.
        settled_rows = [row for row in rows if row.settled]
        settled_rows.sort(key=lambda row: row.market.id)
        settled_rows.sort(key=lambda row: row.market.close_at, reverse=True)
        return PortfolioResponse(
            items=[row.entry for row in (*unsettled, *settled_rows)],
            total_value_cr=to_cr(sum(row.value_micro for row in unsettled)),
        )

    async def trades(self, user: AuthenticatedUser, page: PageQuery) -> TradeListResponse:
        """The user's trades, newest first (`GET /me/trades`).

        The cursor is the id of the last trade of the previous page, as text. One that
        is not a UUID raises `ValidationFailedError` (422) with `details.field =
        "cursor"`.

        1. `trades.list_for_user(user.id, limit=page.limit + 1, before_id=<cursor>)`.
        2. Return the first `page.limit` rows. `next_cursor` is the id of the last
           returned row when one more row came back, else null.
        3. Each item carries its market's question (`markets.get_many` for the page's
           market ids, one query),
           `average_price = MarketView.average_price(amount, shares)` and the display
           values of `p_yes_before_micro` and `p_yes_after_micro`
           (`MarketView.probabilities(...)[0]`).
        """
        before_id = _trade_cursor(page.cursor)
        async with self._uow_factory() as uow:
            rows = await uow.trades.list_for_user(
                user.id, limit=page.limit + 1, before_id=before_id
            )
            shown = rows[: page.limit]
            market_ids = {trade.market_id for trade in shown}
            markets = await uow.markets.get_many(market_ids) if market_ids else {}

        items = [
            TradeItem(
                id=trade.id,
                market_id=trade.market_id,
                market_question=markets[trade.market_id].question,
                side=trade.side,
                outcome=trade.outcome,
                shares=to_cr(trade.shares_micro),
                amount_cr=to_cr(trade.amount_micro),
                average_price=self._view.average_price(trade.amount_micro, trade.shares_micro),
                p_yes_before=self._view.probabilities(trade.p_yes_before_micro)[0],
                p_yes_after=self._view.probabilities(trade.p_yes_after_micro)[0],
                market_version=trade.market_version,
                created_at=trade.created_at,
            )
            for trade in shown
        ]
        more = len(rows) > page.limit
        return TradeListResponse(items=items, next_cursor=str(shown[-1].id) if more else None)

    async def ledger(self, user: AuthenticatedUser, page: PageQuery) -> LedgerListResponse:
        """Every movement on the user's balance, newest first (`GET /me/ledger`).

        The cursor is the id of the last posting of the previous page, as decimal
        text. One that is not a positive integer a `postings.id` can be raises
        `ValidationFailedError` (422) with `details.field = "cursor"`.

        1. Load the user's account; `journal.list_postings_for_account(account.id,
           limit=page.limit + 1, before_id=<cursor>)`.
        2. Page as in `trades`. For the lines of the page whose entry is about a
           voucher (`entry.ref_type == LedgerRef.VOUCHER`, so `entry.ref_id` is the
           voucher's id), read what those vouchers are for in one query:
           `vouchers.product_names(<their ref_ids>)`.
        3. An item is `id` = the posting's id, `kind` = the entry's kind, `amount_cr`
           = the posting's signed amount, `created_at` = the entry's, and
           `description` = `ledger_description(kind, <the product name of the entry's
           voucher, None for any other entry>)`.

        A user's ledger shows two kinds of voucher lines, the issue ("Voucher: Mate")
        and the refund ("Voucher expired, refunded: Mate"). A redemption moves credit
        between two accounts of VIS and touches no user's balance. What an admin
        added to or took from the balance is a line of its own, "Adjustment by
        VIS", with the sign of its amount saying which.
        """
        before_id = _posting_cursor(page.cursor)
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_user_account(user.id)
            if account is None:
                raise LookupError(f"user {user.id} has no ledger account")
            rows = await uow.journal.list_postings_for_account(
                account.id, limit=page.limit + 1, before_id=before_id
            )
            shown = rows[: page.limit]
            voucher_ids = {
                entry.ref_id for _, entry in shown if entry.ref_type == LedgerRef.VOUCHER
            }
            product_names = await uow.vouchers.product_names(voucher_ids) if voucher_ids else {}

        items = [
            LedgerItem(
                id=posting.id,
                kind=entry.kind,
                amount_cr=to_cr(posting.amount_micro),
                description=ledger_description(
                    entry.kind,
                    product_names.get(entry.ref_id)
                    if entry.ref_type == LedgerRef.VOUCHER
                    else None,
                ),
                created_at=entry.created_at,
            )
            for posting, entry in shown
        ]
        more = len(rows) > page.limit
        return LedgerListResponse(items=items, next_cursor=str(shown[-1][0].id) if more else None)

    async def proposals(self, user: AuthenticatedUser) -> ProposalListResponse:
        """The markets the user proposed, in every status, newest first
        (`GET /me/proposals`): `markets.list_created_by(user.id)`, each with its
        effective status (`MarketView.effective_status`). A proposal nobody decided
        on before its `close_at` is listed EXPIRED: it is out of review.

        `outcome` is how a settled market ended: YES, NO or VOID from its
        `resolutions` row, and null for every market whose row does not say RESOLVED
        or VOID. The rows of the settled ones are read in one query
        (`resolutions.get_many_by_market`), after the markets: a settlement that
        commits between the two shows with the next request, whole.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            markets = await uow.markets.list_created_by(user.id)
            settled = [market.id for market in markets if market.status in SETTLED_STATUSES]
            resolutions = await uow.resolutions.get_many_by_market(settled) if settled else {}
        return ProposalListResponse(
            items=[
                ProposalItem(
                    id=market.id,
                    question=market.question,
                    yes_definition=market.yes_def,
                    no_definition=market.no_def,
                    resolution_criteria=market.resolution_criteria,
                    resolution_source=market.resolution_source,
                    status=self._view.effective_status(market, now),
                    outcome=_outcome(market, resolutions),
                    close_at=market.close_at,
                    resolve_by=market.resolve_by,
                    created_at=market.created_at,
                    rejection_reason=market.rejection_reason,
                )
                for market in markets
            ]
        )


def _optional_cr(value_micro: int | None) -> Decimal | None:
    return None if value_micro is None else to_cr(value_micro)


def _outcome(market: Market, resolutions: Mapping[UUID, Resolution]) -> ResolutionOutcome | None:
    """How `market` ended, None while its row does not say RESOLVED or VOID.

    A settlement writes the status and the `resolutions` row in one transaction, so a
    settled market without that row is a broken book and raises `LookupError`.
    """
    if market.status not in SETTLED_STATUSES:
        return None
    resolution = resolutions.get(market.id)
    if resolution is None:
        raise LookupError(f"market {market.id} is {market.status} but has no resolution")
    return resolution.outcome


def _refused_cursor() -> ValidationFailedError:
    return ValidationFailedError(details={"field": CURSOR_FIELD})


def _trade_cursor(cursor: str | None) -> UUID | None:
    """The trade id a cursor of `/me/trades` stands for; None for the first page."""
    if cursor is None:
        return None
    try:
        return UUID(cursor)
    except ValueError:
        raise _refused_cursor() from None


def _posting_cursor(cursor: str | None) -> int | None:
    """The posting id a cursor of `/me/ledger` stands for; None for the first page.

    Only ASCII digits count: `int` alone would also read a sign, an underscore and the
    digits of other scripts. An id the column cannot hold is refused here, because the
    driver would refuse to send it.
    """
    if cursor is None:
        return None
    if not (cursor.isascii() and cursor.isdigit()):
        raise _refused_cursor()
    posting_id = int(cursor)
    if not 0 < posting_id <= MAX_POSTING_ID:
        raise _refused_cursor()
    return posting_id
