"""The books of whole markets, from a proposal to a settlement (SPEC §1, §5, §6, §8, §12,
Appendix A.4, A.5, A.8).

Every service has tests of its own, on rows those tests put in place. Here the services
meet. A market is proposed, approved, traded by several people and settled, one by a
resolution and one by a void, and every step is a request to a route: nothing but the
people and their starting balances is written by the factory. After every step the
whole database is held against the rules of `tests/support/books.py`: every journal
entry and all accounts sum to zero, and a market's q is what its positions hold.

SPEC §12 names what the two settlements have to show: "settlement pays exactly the
winning shares" and "VOID refunds equal net stakes". With them: that what one screen
says about a market is what every other screen says, and that a credit can be followed
from the grant that made it to the counter where it is spent.

What a trader is owed is what `MarketView` makes of the stored positions. What a trader
paid and received is what the API answered for each trade; no price is worked out here.
"""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from uuid import UUID, uuid7

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.container import Container
from app.core.enums import (
    AccountKind,
    AlertSeverity,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
)
from app.core.identity import PortalIdentity
from app.core.units import MICRO, display_probability, format_cr, to_cr, to_micro
from app.db.base import Base
from app.integrations.redis.market_cache import MarketCache
from app.models import (
    Alert,
    AuditLogEntry,
    JournalEntry,
    Market,
    Position,
    Posting,
    Resolution,
    Trade,
    User,
)
from app.pricing import lmsr
from app.relay.outbox_relay import OutboxRelay
from app.schemas.admin.code_batches import CodeBatchCreatedResponse
from app.schemas.admin.risk import AlertItem, RiskMarketRow, RiskResponse
from app.schemas.barista import VoucherRedeemResponse
from app.schemas.codes import RedeemCodeResponse
from app.schemas.common import Limit
from app.schemas.leaderboard import LeaderboardEntry, LeaderboardResponse
from app.schemas.live import (
    HistoryPoint,
    HistoryResponse,
    HistorySource,
    QuoteEvent,
    StatusEvent,
)
from app.schemas.markets import (
    MarketDetailResponse,
    MarketListResponse,
    MarketSummary,
    SellPortion,
    StakePreset,
    TradeBlockReason,
)
from app.schemas.me import LedgerListResponse, MeResponse, PortfolioEntry, PortfolioResponse
from app.schemas.profile import ProfileSavedResponse
from app.schemas.trading import TradeResponse
from app.schemas.vouchers import VoucherIssuedResponse, VoucherProductListResponse
from app.services.account import ledger_description
from app.services.audit import AuditAction
from app.services.codes import ONE_PER_USER_MESSAGE, OWN_BATCH_MESSAGE
from app.services.history import HistoryService
from app.services.leaderboard import PAYOUT_RATIO_PLACES
from app.services.ledger import resolution_key, void_key
from app.services.live_quotes import LiveEvent
from app.services.market_lifecycle import CLOSE_FIRST_MESSAGE
from app.services.markets import PUBLIC_STATUSES
from app.services.trading import OWN_MARKET_MESSAGE
from tests.support.books import (
    assert_books_balance,
    count_rows,
    read_books,
    replay,
    system_balance,
    violations,
)
from tests.support.clock import FrozenClock
from tests.support.factories import ActAs, Factory, PortalHeaders
from tests.support.history import MemoryHistory, unpublished_ids
from tests.support.settings import TEST_ADMIN_EMAILS, TEST_BARISTA_EMAILS, load_base_settings

pytestmark = pytest.mark.integration

_SETTINGS = load_base_settings()

GRANT: Final[int] = _SETTINGS.grants.signup_grant_micro
"""SIGNUP_GRANT_CR in µCR: what a trader of these tests starts with."""

PRECISION: Final[int] = _SETTINGS.pricing.decimal_precision
DISPLAY_DECIMALS: Final[int] = _SETTINGS.pricing.price_display_decimals

MINUTE: Final[timedelta] = timedelta(minutes=1)
HOUR: Final[timedelta] = timedelta(hours=1)
WEEK: Final[timedelta] = timedelta(days=7)

NOTHING: Final[Decimal] = Decimal(0)
"""No credits and no shares, as an amount of the API."""

CODE_VALUE: Final[Decimal] = Decimal(50)
"""What a code of the batch in the last test is worth, in CR: within CODE_MIN_VALUE_CR
and CODE_MAX_VALUE_CR."""

STREAM_SECONDS: Final[float] = 5.0
"""How long a test waits for an event that should arrive at once, before it fails
instead of hanging. Below SSE_HEARTBEAT_SECONDS, so no heartbeat is taken for one."""

EVIDENCE: Final[str] = "The menu of that Friday shows it."
VOID_REASON: Final[str] = "The mensa was closed that day, so there is nothing to decide."

PUBLIC_LIST: Final[str] = MarketCache.list_key(PUBLIC_STATUSES)
"""The cache key of the list a request without `status` gets."""

SETTLEMENT_TABLES: Final[tuple[type[Base], ...]] = (
    Resolution,
    JournalEntry,
    Posting,
    AuditLogEntry,
    Trade,
)
"""What a settlement or a trade would add a row to."""

type Choose = Callable[[Sequence[StakePreset]], StakePreset]
"""Which of the buy buttons a market page offers somebody taps."""


def smallest(offered: Sequence[StakePreset]) -> StakePreset:
    return min(offered, key=lambda preset: preset.amount_cr)


def largest(offered: Sequence[StakePreset]) -> StakePreset:
    return max(offered, key=lambda preset: preset.amount_cr)


# --- The API, as the people of a test use it -----------------------------------------------


class Api:
    """The routes, called as somebody: `as_user` says who, the route does the rest, and
    its answer is read with the model the route declares.

    `trades` keeps every trade the API executed, per trader, as it answered it: what
    somebody paid and received is added up from those answers.
    """

    def __init__(self, client: httpx.AsyncClient, as_user: ActAs) -> None:
        self._client = client
        self._as_user = as_user
        self.trades: dict[UUID, list[TradeResponse]] = defaultdict(list)

    async def send(
        self,
        who: User,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
        key: UUID | None = None,
    ) -> httpx.Response:
        self._as_user(who)
        headers = {} if key is None else {"Idempotency-Key": str(key)}
        return await self._client.request(
            method, f"/api{path}", json=body, params=query, headers=headers
        )

    async def read[ModelT: BaseModel](
        self, who: User, path: str, model: type[ModelT], **query: str
    ) -> ModelT:
        response = await self.send(who, "GET", path, query=query)
        assert response.status_code == 200, response.text
        return model.model_validate(response.json())

    async def propose(self, who: User, now: datetime, tier: MarketTier) -> MarketDetailResponse:
        close_at = now + WEEK
        response = await self.send(
            who,
            "POST",
            "/markets",
            body={
                "question": "Will the Polymensa serve Älplermagronen on Friday?",
                "yes_definition": "It is on the lunch menu that day.",
                "no_definition": "It is not on the lunch menu that day.",
                "resolution_criteria": "The menu published for that Friday decides.",
                "resolution_source": "The menu plan on ethz.ch",
                "close_at": close_at.isoformat(),
                "resolve_by": (close_at + WEEK).isoformat(),
                "requested_tier": tier.value,
            },
        )
        assert response.status_code == 201, response.text
        return MarketDetailResponse.model_validate(response.json())

    async def act(self, admin: User, market_id: UUID, action: str, **body: str) -> httpx.Response:
        """One of the seven admin actions on a market, answered as it is."""
        return await self.send(admin, "POST", f"/admin/markets/{market_id}/{action}", body=body)

    async def done(
        self, admin: User, market_id: UUID, action: str, **body: str
    ) -> MarketDetailResponse:
        """An admin action that has to succeed."""
        response = await self.act(admin, market_id, action, **body)
        assert response.status_code == 200, response.text
        return MarketDetailResponse.model_validate(response.json())

    async def market(self, who: User, market_id: UUID) -> MarketDetailResponse:
        return await self.read(who, f"/markets/{market_id}", MarketDetailResponse)

    async def trade(self, who: User, market_id: UUID, **body: str) -> httpx.Response:
        """A trade under a key of its own, answered as it is."""
        return await self.send(who, "POST", f"/markets/{market_id}/trades", body=body, key=uuid7())

    def executed(self, who: User, response: httpx.Response) -> TradeResponse:
        assert response.status_code == 201, response.text
        answer = TradeResponse.model_validate(response.json())
        self.trades[who.id].append(answer)
        return answer

    async def tap_buy(
        self, who: User, market_id: UUID, outcome: Outcome, choose: Choose
    ) -> TradeResponse:
        """Buy with one of the buttons the market page offers `who` right now. A button
        the page marks allowed is a trade the trading service takes, at that amount."""
        offered = [
            preset
            for preset in (await self.market(who, market_id)).viewer.stake_presets
            if preset.allowed
        ]
        button = choose(offered)
        answer = self.executed(
            who,
            await self.trade(
                who,
                market_id,
                side="BUY",
                outcome=outcome.value,
                amount_cr=format_cr(to_micro(button.amount_cr)),
            ),
        )
        assert (answer.side, answer.outcome) == (TradeSide.BUY, outcome)
        assert answer.amount_cr == button.amount_cr
        return answer

    async def tap_sell(
        self, who: User, market_id: UUID, outcome: Outcome, portion: SellPortion
    ) -> TradeResponse:
        """Sell with one of the sell buttons of the market page: half or all of what
        `who` holds of `outcome`, in the shares the server worked out."""
        (button,) = [
            preset
            for preset in (await self.market(who, market_id)).viewer.sell_presets
            if (preset.outcome, preset.portion) == (outcome, portion)
        ]
        assert button.allowed
        answer = self.executed(
            who,
            await self.trade(
                who,
                market_id,
                side="SELL",
                outcome=outcome.value,
                shares=format_cr(to_micro(button.shares)),
            ),
        )
        assert (answer.side, answer.shares) == (TradeSide.SELL, button.shares)
        return answer

    def paid(self, who: User) -> int:
        """µCR `who` paid for buys, as the API answered them."""
        return self._total(who, TradeSide.BUY)

    def received(self, who: User) -> int:
        """µCR `who` received for sells, as the API answered them."""
        return self._total(who, TradeSide.SELL)

    def _total(self, who: User, side: TradeSide) -> int:
        return sum(to_micro(trade.amount_cr) for trade in self.trades[who.id] if trade.side is side)


@pytest.fixture
def api(client: httpx.AsyncClient, as_user: ActAs) -> Api:
    return Api(client, as_user)


async def registered(
    factory: Factory, clock: FrozenClock, *usernames: str, balance_micro: int = 0
) -> list[User]:
    """Users of these names, registered a minute apart: where a ranking breaks a tie by
    who registered first, that is the order they are named in."""
    users: list[User] = []
    for username in usernames:
        users.append(await factory.user(username=username, balance_micro=balance_micro))
        clock.advance(MINUTE)
    return users


def refusal(response: httpx.Response, status_code: int, code: str) -> dict[str, Any]:
    """The response is that error; returns its body."""
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    return body


# --- Reading the books ---------------------------------------------------------------------


async def held(container: Container, user: User, market_id: UUID) -> Sequence[Position]:
    """The user's positions in the market, as they are stored."""
    async with container.uow_factory() as uow:
        return await uow.positions.list_for_user_market(user.id, market_id)


async def all_rows[ModelT: Base](container: Container, model: type[ModelT]) -> Sequence[ModelT]:
    """Every row of a table."""
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(model))).all()


async def stored(container: Container, market_id: UUID) -> Market:
    async with container.uow_factory() as uow:
        market = await uow.markets.get(market_id)
    assert market is not None
    return market


async def resolution_of(container: Container, market_id: UUID) -> Resolution:
    async with container.uow_factory() as uow:
        resolution = await uow.resolutions.get_by_market(market_id)
    assert resolution is not None
    return resolution


async def market_account(container: Container, market_id: UUID) -> int:
    """The balance of the market's own account, in µCR."""
    async with container.uow_factory() as uow:
        account = await uow.accounts.get_market_account(market_id)
    assert account is not None
    return account.balance_micro


async def market_maker(container: Container) -> int:
    """The balance of `vis:mm_pnl`, which takes what a settlement leaves over or lacks."""
    return await system_balance(container.uow_factory, AccountKind.VIS_MM_PNL)


async def entry_postings(container: Container, key: str) -> dict[UUID | None, int]:
    """The postings of the journal entry booked under `key`: per user for a user's
    account, and under None what went to the accounts that are nobody's."""
    async with container.uow_factory() as uow:
        entry = await uow.journal.get_entry_by_idempotency_key(key)
        assert entry is not None, f"no entry was booked under {key}"
        postings = (
            await uow.session.scalars(select(Posting).where(Posting.entry_id == entry.id))
        ).all()
        owners = {
            posting.account_id: (await uow.accounts.get(posting.account_id)) for posting in postings
        }
    found: dict[UUID | None, int] = defaultdict(int)
    for posting in postings:
        account = owners[posting.account_id]
        assert account is not None
        found[account.owner_id] += posting.amount_micro
    return dict(found)


async def written(container: Container) -> dict[str, int]:
    """How many rows each table holds that a settlement or a trade would add to."""
    return {
        model.__tablename__: await count_rows(container.uow_factory, model)
        for model in SETTLEMENT_TABLES
    }


async def risk_row(api: Api, admin: User, market_id: UUID) -> RiskMarketRow:
    """The market's row on the admin risk page."""
    risk = await api.read(admin, "/admin/risk", RiskResponse)
    (row,) = [row for row in risk.markets if row.id == market_id]
    return row


def entry_of(portfolio: PortfolioResponse, market_id: UUID) -> PortfolioEntry:
    (entry,) = [entry for entry in portfolio.items if entry.market.id == market_id]
    return entry


def by_username(board: LeaderboardResponse) -> dict[str, LeaderboardEntry]:
    return {entry.username: entry for entry in board.items}


def payout_ratio(returned_micro: int, paid_micro: int) -> float:
    """The payout ratio the leaderboard sends for one resolved market (A.2): what came
    back per credit put in, in whole millionths, cut to the places it is shown with."""
    step_micro = MICRO // 10**PAYOUT_RATIO_PLACES
    steps = returned_micro * MICRO // paid_micro // step_micro
    return float(Decimal(steps) / 10**PAYOUT_RATIO_PLACES)


# --- A market that is resolved ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settled:
    """A market that went from its proposal to its settlement through the routes, with
    what was read right before the settlement."""

    market_id: UUID
    proposer: User
    admin: User
    traders: tuple[User, ...]
    balances_before: dict[UUID, int]
    held_before: dict[UUID, Sequence[Position]]
    account_before: int
    """The balance of the market's account, B."""

    market_maker_before: int
    shown_before: RiskMarketRow
    """The market's row on the risk page."""

    answer: MarketDetailResponse
    """What the settling request was answered with."""

    def trader(self, username: str) -> User:
        (user,) = [user for user in self.traders if user.username == username]
        return user


async def before_settlement(
    api: Api,
    container: Container,
    factory: Factory,
    market_id: UUID,
    admin: User,
    traders: Sequence[User],
) -> tuple[dict[UUID, int], dict[UUID, Sequence[Position]], int, int, RiskMarketRow]:
    return (
        {user.id: await factory.balance_micro(user) for user in traders},
        {user.id: await held(container, user, market_id) for user in traders},
        await market_account(container, market_id),
        await market_maker(container),
        await risk_row(api, admin, market_id),
    )


@pytest.fixture
async def resolved(api: Api, container: Container, factory: Factory, clock: FrozenClock) -> Settled:
    """Paula proposes, an admin approves, four people trade, the admin closes and
    resolves to YES.

    Alice and Carol buy YES with the largest button, which takes each to the stake
    limit, and Alice sells half again. Bob buys NO and keeps it. Dave buys NO and sells
    it all. So at the resolution Alice and Carol hold YES, Bob holds NO and Dave holds
    nothing.
    """
    (paula,) = await registered(factory, clock, "paula")
    alice, bob, carol, dave = await registered(
        factory, clock, "alice", "bob", "carol", "dave", balance_micro=GRANT
    )
    admin = await factory.admin()
    books = container.uow_factory

    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    opened = await api.done(admin, market_id, "approve", tier="DEFAULT")
    assert (opened.status, opened.version) == (MarketStatus.OPEN, 0)
    await assert_books_balance(books)

    for who, outcome, choose in (
        (alice, Outcome.YES, largest),
        (bob, Outcome.NO, smallest),
        (carol, Outcome.YES, largest),
    ):
        clock.advance(MINUTE)
        await api.tap_buy(who, market_id, outcome, choose)
        await assert_books_balance(books)
    clock.advance(MINUTE)
    await api.tap_sell(alice, market_id, Outcome.YES, SellPortion.HALF)
    await assert_books_balance(books)
    clock.advance(MINUTE)
    await api.tap_buy(dave, market_id, Outcome.NO, smallest)
    clock.advance(MINUTE)
    await api.tap_sell(dave, market_id, Outcome.NO, SellPortion.ALL)
    await assert_books_balance(books)

    clock.advance(HOUR)
    closed = await api.done(admin, market_id, "close")
    assert closed.status is MarketStatus.CLOSED
    await assert_books_balance(books)
    traders = (alice, bob, carol, dave)
    before = await before_settlement(api, container, factory, market_id, admin, traders)

    clock.advance(HOUR)
    answer = await api.done(admin, market_id, "resolve", outcome="YES", evidence=EVIDENCE)
    await assert_books_balance(books)
    return Settled(market_id, paula, admin, traders, *before, answer)


async def test_the_resolution_pays_every_holder_a_credit_per_winning_share_and_nobody_else(
    resolved: Settled, container: Container, factory: Factory
) -> None:
    """SPEC §12: "settlement pays exactly the winning shares"."""
    view = container.market_view
    resolution = await resolution_of(container, resolved.market_id)
    market = await stored(container, resolved.market_id)
    paid_out: dict[UUID, int] = {}
    for user in resolved.traders:
        owed = view.payout_micro(resolved.held_before[user.id], resolution)
        assert owed is not None
        paid_out[user.id] = await factory.balance_micro(user) - resolved.balances_before[user.id]
        assert paid_out[user.id] == owed, user.username

    alice, bob, carol, dave = resolved.traders
    assert resolution.outcome is ResolutionOutcome.YES
    assert paid_out[alice.id] > 0
    assert paid_out[carol.id] > 0
    assert (paid_out[bob.id], paid_out[dave.id]) == (0, 0), "NO lost, and Dave held nothing"
    # Every YES share the market sold is paid, and nothing but those: q is the positions.
    assert sum(paid_out.values()) == market.q_yes_micro
    assert market.status is MarketStatus.RESOLVED
    # Resolution leaves the positions as they were: the payout is read from them.
    for user in resolved.traders:
        after = await held(container, user, resolved.market_id)
        assert [
            (row.outcome, row.shares_micro, row.cost_micro, row.proceeds_micro) for row in after
        ] == [
            (row.outcome, row.shares_micro, row.cost_micro, row.proceeds_micro)
            for row in resolved.held_before[user.id]
        ]
    await assert_books_balance(container.uow_factory)


async def test_the_resolution_is_one_entry_that_empties_the_market_account_into_the_winners(
    resolved: Settled, container: Container
) -> None:
    """A.8: each winner their shares, the market's account its whole balance, and
    `vis:mm_pnl` the difference. What the risk page showed as the market's exposure
    before is what the market maker lost: YES was the more heavily held side."""
    alice, _, carol, _ = resolved.traders
    market = await stored(container, resolved.market_id)
    postings = await entry_postings(container, resolution_key(resolved.market_id))
    to_winners = {user: amount for user, amount in postings.items() if user is not None}
    result = await market_maker(container) - resolved.market_maker_before

    assert set(to_winners) == {alice.id, carol.id}
    assert sum(to_winners.values()) == market.q_yes_micro > market.q_no_micro
    assert postings[None] == -sum(to_winners.values()), "the entry sums to zero"
    assert await market_account(container, resolved.market_id) == 0
    assert result == resolved.account_before - sum(to_winners.values())
    assert result == -to_micro(resolved.shown_before.exposure_cr)
    assert -result <= market.budget_micro, "the loss is within the market's budget"
    kinds = [entry.kind for entry in await all_rows(container, JournalEntry)]
    assert kinds.count(JournalKind.MARKET_RESOLUTION) == 1
    await assert_books_balance(container.uow_factory)


async def test_every_balance_is_the_grant_minus_the_buys_plus_the_sales_plus_the_payout(
    resolved: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """Nothing else moved a credit of anybody: what each trade cost or returned is what
    the API answered for it, and the payout is what `MarketView` reads off the rows."""
    view = container.market_view
    resolution = await resolution_of(container, resolved.market_id)
    for user in resolved.traders:
        payout = view.payout_micro(await held(container, user, resolved.market_id), resolution)
        assert payout is not None
        expected = GRANT - api.paid(user) + api.received(user) + payout
        assert await factory.balance_micro(user) == expected, user.username
    assert await factory.balance_micro(resolved.proposer) == 0
    assert await factory.balance_micro(resolved.admin) == 0
    await assert_books_balance(container.uow_factory)


async def test_the_books_of_the_resolved_market_hold_every_rule(
    resolved: Settled, container: Container
) -> None:
    """Spelled out once what `assert_books_balance` checked after every step: all
    accounts sum to zero, every journal entry does, and q is what the positions hold."""
    books = await read_books(container.uow_factory)
    market = await stored(container, resolved.market_id)

    assert violations(books) == []
    assert sum(account.balance_micro for account in books.accounts) == 0
    assert books.entry_totals_micro
    assert set(books.entry_totals_micro.values()) == {0}
    assert books.shares_held_micro[(market.id, Outcome.YES)] == market.q_yes_micro
    assert books.shares_held_micro[(market.id, Outcome.NO)] == market.q_no_micro
    await assert_books_balance(container.uow_factory)


async def test_the_trades_of_the_market_were_priced_one_after_another(
    resolved: Settled, api: Api, container: Container
) -> None:
    """Priced again by lmsr from an empty market, each trade costs or returns what was
    recorded, and they end in the quantities the market has."""
    market = await stored(container, resolved.market_id)
    async with container.uow_factory() as uow:
        trades = await uow.trades.list_for_market(market.id, limit=1000)

    end = replay(lmsr.MarketState(q_yes=0, q_no=0, b=market.b_micro), trades, precision=PRECISION)

    assert (end.q_yes, end.q_no) == (market.q_yes_micro, market.q_no_micro)
    assert len(trades) == sum(len(answers) for answers in api.trades.values()) == market.version
    answered = {trade.trade_id: trade for answers in api.trades.values() for trade in answers}
    for trade in trades:
        assert to_micro(answered[trade.id].amount_cr) == trade.amount_micro
        assert to_micro(answered[trade.id].shares) == trade.shares_micro
    await assert_books_balance(container.uow_factory)


async def test_the_portfolio_the_market_page_and_the_ledger_tell_one_story_of_the_payout(
    resolved: Settled, api: Api, container: Container
) -> None:
    """A winner sees the same payout on all three screens, and somebody who won nothing
    sees a payout of nothing and no line for it."""
    view = container.market_view
    resolution = await resolution_of(container, resolved.market_id)
    for user in resolved.traders:
        payout = view.payout_micro(await held(container, user, resolved.market_id), resolution)
        assert payout is not None
        page = await api.market(user, resolved.market_id)
        portfolio = await api.read(user, "/me/positions", PortfolioResponse)
        ledger = await api.read(user, "/me/ledger", LedgerListResponse)
        me = await api.read(user, "/me", MeResponse)
        entry = entry_of(portfolio, resolved.market_id)

        assert page.viewer.payout_cr == entry.payout_cr == to_cr(payout), user.username
        assert (page.viewer.refund_cr, entry.refund_cr) == (None, None)
        assert (page.status, page.outcome) == (MarketStatus.RESOLVED, ResolutionOutcome.YES)
        assert entry.market.model_dump() == page.model_dump(include=set(MarketSummary.model_fields))
        assert entry.holdings == page.viewer.holdings
        yes, no = entry.holdings
        assert (yes.value_cr, no.value_cr) == (yes.shares, NOTHING), "1 CR a winning share"
        assert portfolio.total_value_cr == 0, "a settled market is no open value"
        payouts = [item for item in ledger.items if item.kind is JournalKind.MARKET_RESOLUTION]
        if payout:
            (line,) = payouts
            assert line == ledger.items[0]
            assert line.amount_cr == to_cr(payout)
            assert line.description == ledger_description(JournalKind.MARKET_RESOLUTION)
        else:
            assert payouts == []
        assert sum(item.amount_cr for item in ledger.items) == me.balance_cr
    await assert_books_balance(container.uow_factory)


async def test_a_resolved_market_takes_no_trade_and_no_second_settlement(
    resolved: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """SPEC §12: "a second resolution fails". So does a void after it, and a trade."""
    second_admin = await factory.admin(1)
    alice = resolved.trader("alice")
    before = await written(container)
    balances = {user.id: await factory.balance_micro(user) for user in resolved.traders}

    for admin in (resolved.admin, second_admin):
        again = await api.act(admin, resolved.market_id, "resolve", outcome="NO", evidence=EVIDENCE)
        voided = await api.act(admin, resolved.market_id, "void", reason=VOID_REASON)
        refusal(again, 409, "CONFLICT")
        refusal(voided, 409, "CONFLICT")
    late = await api.trade(alice, resolved.market_id, side="SELL", outcome="YES", shares="1")

    refusal(late, 409, "MARKET_NOT_OPEN")
    assert await written(container) == before
    assert {user.id: await factory.balance_micro(user) for user in resolved.traders} == balances
    assert (await resolution_of(container, resolved.market_id)).outcome is ResolutionOutcome.YES
    await assert_books_balance(container.uow_factory)


async def test_the_leaderboard_ranks_the_traders_by_what_the_resolved_market_returned(
    resolved: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """The rows real trades and a real resolution leave behind are the rows the ranking
    reads: credits are the balances, trades are the trades, and the two rates follow
    from what each trader put in and got back (A.2)."""
    alice = resolved.trader("alice")
    view = container.market_view
    resolution = await resolution_of(container, resolved.market_id)

    board = await api.read(alice, "/leaderboard", LeaderboardResponse, sort="win_rate")
    listed = by_username(board)

    everybody = (*resolved.traders, resolved.proposer, resolved.admin)
    assert set(listed) == {user.username for user in everybody}
    for user in everybody:
        row = listed[user.username]
        assert row.credits_cr == to_cr(await factory.balance_micro(user))
        assert row.trades == len(api.trades[user.id])
    for user in resolved.traders:
        payout = view.payout_micro(await held(container, user, resolved.market_id), resolution)
        assert payout is not None
        returned, put_in = api.received(user) + payout, api.paid(user)
        row = listed[user.username]
        won = MICRO if returned > put_in else 0
        assert row.settled_markets == 1
        assert row.win_rate == display_probability(won, DISPLAY_DECIMALS), user.username
        assert row.avg_payout_ratio == payout_ratio(returned, put_in), user.username
    for bystander in (resolved.proposer, resolved.admin):
        row = listed[bystander.username]
        assert (row.settled_markets, row.win_rate, row.avg_payout_ratio) == (0, None, None)
    assert (listed["alice"].win_rate, listed["carol"].win_rate) == (1.0, 1.0)
    assert (listed["bob"].win_rate, listed["dave"].win_rate) == (0.0, 0.0)
    assert listed["bob"].avg_payout_ratio == 0.0, "Bob kept shares that paid nothing"
    # The winners first, in the order they registered; who has no rate comes last.
    assert [row.username for row in board.items] == [
        "alice",
        "carol",
        "bob",
        "dave",
        "paula",
        resolved.admin.username,
    ]
    assert [row.rank for row in board.items] == [1, 2, 3, 4, 5, 6]
    assert board.viewer == listed["alice"]
    assert board.viewer is not None
    assert board.viewer.is_viewer
    await assert_books_balance(container.uow_factory)


# --- A market that is voided -------------------------------------------------------------------


@pytest.fixture
async def voided(api: Api, container: Container, factory: Factory, clock: FrozenClock) -> Settled:
    """Paula proposes, an admin approves, three people trade, the admin closes and
    voids.

    Erin buys YES first and sells it all after Frank's larger buy moved the price up,
    so she took out more than she put in. Frank sells half of his. Gina buys NO and
    keeps it.
    """
    (paula,) = await registered(factory, clock, "paula")
    erin, frank, gina = await registered(
        factory, clock, "erin", "frank", "gina", balance_micro=GRANT
    )
    admin = await factory.admin()
    books = container.uow_factory

    market_id = (await api.propose(paula, clock.now(), MarketTier.TIGHT)).id
    await api.done(admin, market_id, "approve", tier="TIGHT")
    for who, outcome, choose in (
        (erin, Outcome.YES, smallest),
        (frank, Outcome.YES, largest),
    ):
        clock.advance(MINUTE)
        await api.tap_buy(who, market_id, outcome, choose)
        await assert_books_balance(books)
    clock.advance(MINUTE)
    await api.tap_sell(erin, market_id, Outcome.YES, SellPortion.ALL)
    clock.advance(MINUTE)
    await api.tap_buy(gina, market_id, Outcome.NO, smallest)
    clock.advance(MINUTE)
    await api.tap_sell(frank, market_id, Outcome.YES, SellPortion.HALF)
    await assert_books_balance(books)

    # A market that still trades is not voided: it is closed first (A.4).
    too_early = await api.act(admin, market_id, "void", reason=VOID_REASON)
    assert refusal(too_early, 409, "CONFLICT")["message"] == CLOSE_FIRST_MESSAGE
    clock.advance(HOUR)
    await api.done(admin, market_id, "close")
    await assert_books_balance(books)
    traders = (erin, frank, gina)
    before = await before_settlement(api, container, factory, market_id, admin, traders)

    clock.advance(HOUR)
    answer = await api.done(admin, market_id, "void", reason=VOID_REASON)
    await assert_books_balance(books)
    return Settled(market_id, paula, admin, traders, *before, answer)


async def test_the_void_refunds_every_net_stake_and_nothing_else(
    voided: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """SPEC §12: "VOID refunds equal net stakes". The net stake is what `MarketView`
    makes of the positions, and it is what the API's own answers add up to."""
    view = container.market_view
    refunded: dict[UUID, int] = {}
    for user in voided.traders:
        net_stake = view.net_stake_micro(voided.held_before[user.id])
        refunded[user.id] = await factory.balance_micro(user) - voided.balances_before[user.id]
        assert refunded[user.id] == net_stake, user.username
        assert net_stake == max(0, api.paid(user) - api.received(user))

    erin, frank, gina = voided.traders
    assert refunded[erin.id] == 0
    assert refunded[frank.id] > 0
    assert refunded[gina.id] == api.paid(gina), "Gina sold nothing: all of it comes back"
    # Whoever took no gain out is where they started.
    assert await factory.balance_micro(frank) == await factory.balance_micro(gina) == GRANT
    await assert_books_balance(container.uow_factory)


async def test_a_trader_who_sold_at_a_gain_keeps_it_through_a_void(
    voided: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """A void refunds stakes; it does not take back what a sale paid. Erin's stake was
    back in her wallet before the void, with a gain on top, and stays there."""
    erin = voided.trader("erin")
    gain = api.received(erin) - api.paid(erin)

    assert gain > 0
    assert await factory.balance_micro(erin) == GRANT + gain
    assert await factory.balance_micro(erin) == voided.balances_before[erin.id]
    await assert_books_balance(container.uow_factory)


async def test_what_the_risk_page_showed_as_the_cost_of_a_void_is_what_the_void_cost(
    voided: Settled, container: Container
) -> None:
    """One entry: each trader their net stake, the market's account its whole balance,
    `vis:mm_pnl` the difference. That difference is the `void_cost_cr` an admin saw on
    the risk page before deciding, and it is Erin's gain: nobody else took anything
    out."""
    erin, frank, gina = voided.traders
    postings = await entry_postings(container, void_key(voided.market_id))
    refunds = {user: amount for user, amount in postings.items() if user is not None}
    cost = voided.market_maker_before - await market_maker(container)

    assert set(refunds) == {frank.id, gina.id}, "nobody without a net stake gets a line"
    assert postings[None] == -sum(refunds.values())
    assert cost == sum(refunds.values()) - voided.account_before
    assert cost == to_micro(voided.shown_before.void_cost_cr) > 0
    assert await market_account(container, voided.market_id) == 0
    kinds = [entry.kind for entry in await all_rows(container, JournalEntry)]
    assert kinds.count(JournalKind.MARKET_VOID) == 1
    assert erin.id not in refunds
    await assert_books_balance(container.uow_factory)


async def test_a_voided_market_holds_no_share_and_keeps_what_was_paid_and_received(
    voided: Settled, container: Container
) -> None:
    """A.5: every position is cancelled and the market's q with it; cost and proceeds
    stay, which is what the refund is read from ever after."""
    market = await stored(container, voided.market_id)

    assert (market.status, market.q_yes_micro, market.q_no_micro) == (MarketStatus.VOID, 0, 0)
    assert (await resolution_of(container, voided.market_id)).outcome is ResolutionOutcome.VOID
    for user in voided.traders:
        after = await held(container, user, voided.market_id)
        assert [row.shares_micro for row in after] == [0] * len(after)
        assert [(row.outcome, row.cost_micro, row.proceeds_micro) for row in after] == [
            (row.outcome, row.cost_micro, row.proceeds_micro) for row in voided.held_before[user.id]
        ]
    assert violations(await read_books(container.uow_factory)) == []
    await assert_books_balance(container.uow_factory)


async def test_the_portfolio_the_market_page_and_the_ledger_tell_one_story_of_the_refund(
    voided: Settled, api: Api, container: Container
) -> None:
    view = container.market_view
    resolution = await resolution_of(container, voided.market_id)
    for user in voided.traders:
        refund = view.refund_micro(await held(container, user, voided.market_id), resolution)
        assert refund is not None
        page = await api.market(user, voided.market_id)
        portfolio = await api.read(user, "/me/positions", PortfolioResponse)
        ledger = await api.read(user, "/me/ledger", LedgerListResponse)
        me = await api.read(user, "/me", MeResponse)
        entry = entry_of(portfolio, voided.market_id)

        assert page.viewer.refund_cr == entry.refund_cr == to_cr(refund), user.username
        assert (page.viewer.payout_cr, entry.payout_cr) == (None, None)
        assert (page.status, page.outcome) == (MarketStatus.VOID, ResolutionOutcome.VOID)
        assert page.resolution is not None
        assert page.resolution.evidence == VOID_REASON
        assert [(holding.shares, holding.value_cr) for holding in entry.holdings] == [
            (NOTHING, NOTHING),
            (NOTHING, NOTHING),
        ]
        assert page.viewer.net_stake_cr == entry.net_stake_cr == to_cr(refund)
        refunds = [item for item in ledger.items if item.kind is JournalKind.MARKET_VOID]
        if refund:
            (line,) = refunds
            assert line == ledger.items[0]
            assert line.amount_cr == to_cr(refund)
            assert line.description == ledger_description(JournalKind.MARKET_VOID)
        else:
            assert refunds == []
        assert sum(item.amount_cr for item in ledger.items) == me.balance_cr
    await assert_books_balance(container.uow_factory)


async def test_a_voided_market_takes_no_trade_no_resolution_and_no_second_void(
    voided: Settled, api: Api, container: Container, factory: Factory
) -> None:
    gina = voided.trader("gina")
    before = await written(container)
    balances = {user.id: await factory.balance_micro(user) for user in voided.traders}

    resolve = await api.act(
        voided.admin, voided.market_id, "resolve", outcome="YES", evidence=EVIDENCE
    )
    again = await api.act(voided.admin, voided.market_id, "void", reason=VOID_REASON)
    late = await api.trade(gina, voided.market_id, side="BUY", outcome="NO", amount_cr="25")

    refusal(resolve, 409, "CONFLICT")
    refusal(again, 409, "CONFLICT")
    refusal(late, 409, "MARKET_NOT_OPEN")
    assert await written(container) == before
    assert {user.id: await factory.balance_micro(user) for user in voided.traders} == balances
    await assert_books_balance(container.uow_factory)


async def test_a_voided_market_gives_nobody_a_rate_on_the_leaderboard(
    voided: Settled, api: Api, container: Container, factory: Factory
) -> None:
    """A.2: the rates are taken over RESOLVED markets. A void counts for nobody, though
    its trades were trades and the credits it left are credits."""
    erin = voided.trader("erin")

    board = await api.read(erin, "/leaderboard", LeaderboardResponse)
    listed = by_username(board)

    for user in voided.traders:
        row = listed[user.username]
        assert (row.settled_markets, row.win_rate, row.avg_payout_ratio) == (0, None, None)
        assert row.trades == len(api.trades[user.id]) > 0
        assert row.credits_cr == to_cr(await factory.balance_micro(user))
    # By credits: Erin has her gain on top of the grant. Frank and Gina have the grant
    # back to the last micro-credit, and tie in the order they registered.
    assert [row.username for row in board.items[:3]] == ["erin", "frank", "gina"]
    assert listed["frank"].credits_cr == listed["gina"].credits_cr == to_cr(GRANT)
    await assert_books_balance(container.uow_factory)


# --- What a market looks like on the way -------------------------------------------------------


async def test_a_proposal_is_for_its_proposer_and_the_admins_until_it_is_approved(
    api: Api, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    paula = await factory.user()
    alice = await factory.user(balance_micro=GRANT)
    admin = await factory.admin()
    proposed = await api.propose(paula, clock.now(), MarketTier.MEDIUM)

    own = await api.market(paula, proposed.id)
    for_admin = await api.market(admin, proposed.id)
    listed = await api.read(alice, "/markets", MarketListResponse)
    pending = await api.read(admin, "/markets", MarketListResponse, status="PROPOSED")

    assert (own.status, own.tier, own.requested_tier) == (
        MarketStatus.PROPOSED,
        None,
        MarketTier.MEDIUM,
    )
    assert (own.proposer_email, for_admin.proposer_email) == (None, paula.email)
    assert (own.viewer.can_trade, own.viewer.cannot_trade_reason) == (
        False,
        TradeBlockReason.MARKET_NOT_OPEN,
    )
    assert listed.items == []
    assert [item.id for item in pending.items] == [proposed.id]
    refusal(await api.send(alice, "GET", f"/markets/{proposed.id}"), 404, "NOT_FOUND")
    refusal(
        await api.send(alice, "GET", "/markets", query={"status": "PROPOSED"}), 403, "FORBIDDEN"
    )
    refusal(
        await api.trade(alice, proposed.id, side="BUY", outcome="YES", amount_cr="25"),
        404,
        "NOT_FOUND",
    )

    opened = await api.done(admin, proposed.id, "approve", tier="MEDIUM")

    assert (opened.status, opened.tier) == (MarketStatus.OPEN, MarketTier.MEDIUM)
    assert (await api.market(alice, proposed.id)).viewer.can_trade
    own_now = await api.market(paula, proposed.id)
    assert own_now.viewer.cannot_trade_reason is TradeBlockReason.OWN_MARKET
    barred = await api.trade(paula, proposed.id, side="BUY", outcome="YES", amount_cr="25")
    assert refusal(barred, 403, "FORBIDDEN")["message"] == OWN_MARKET_MESSAGE
    await assert_books_balance(container.uow_factory)


async def test_the_market_list_shows_the_price_of_the_last_trade_and_the_status_of_the_last_action(
    api: Api, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The list is served from Redis for a few seconds. A committed trade and a
    committed admin action each drop it, so the next reader sees what they did."""
    paula = await factory.user()
    alice = await factory.user(balance_micro=GRANT)
    admin = await factory.admin()
    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    await api.done(admin, market_id, "approve", tier="DEFAULT")

    (fresh,) = (await api.read(alice, "/markets", MarketListResponse)).items
    assert await container.market_cache.get(PUBLIC_LIST) is not None, "kept for the next reader"
    bought = await api.tap_buy(alice, market_id, Outcome.YES, largest)
    assert await container.market_cache.get(PUBLIC_LIST) is None, "dropped by the trade"
    (traded,) = (await api.read(alice, "/markets", MarketListResponse)).items
    await api.done(admin, market_id, "halt", reason="The source is in doubt.")
    (halted,) = (await api.read(alice, "/markets", MarketListResponse)).items

    assert (fresh.p_yes, fresh.version, fresh.volume_cr) == (0.5, 0, NOTHING)
    assert (traded.p_yes, traded.p_no) == (bought.p_yes_after, bought.p_no_after)
    assert (traded.version, traded.volume_cr) == (bought.market_version, bought.amount_cr)
    assert (traded.status, traded.trading_open) == (MarketStatus.OPEN, True)
    assert (halted.status, halted.trading_open) == (MarketStatus.HALTED, False)
    assert (await api.market(alice, market_id)).p_yes == traded.p_yes
    await assert_books_balance(container.uow_factory)


async def test_a_trader_at_the_stake_limit_is_offered_no_buy_and_refused_one(
    api: Api, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """The market page and the trading service agree on the limit: once every button is
    greyed out, tapping one anyway is refused with what is left, which is nothing."""
    paula = await factory.user()
    carol = await factory.user(balance_micro=GRANT)
    admin = await factory.admin()
    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    await api.done(admin, market_id, "approve", tier="DEFAULT")
    bought = await api.tap_buy(carol, market_id, Outcome.YES, largest)
    before = await written(container)

    page = await api.market(carol, market_id)
    cheapest = smallest(page.viewer.stake_presets)
    over = await api.trade(
        carol,
        market_id,
        side="BUY",
        outcome="NO",
        amount_cr=format_cr(to_micro(cheapest.amount_cr)),
    )

    assert bought.stake_left_cr == page.viewer.stake_left_cr == 0
    assert page.viewer.net_stake_cr == page.viewer.stake_limit_cr == bought.net_stake_cr
    assert [preset.allowed for preset in page.viewer.stake_presets] == [False] * len(
        page.viewer.stake_presets
    )
    assert refusal(over, 409, "LIMIT_EXCEEDED")["details"] == {
        "limit": Limit.MAX_STAKE_PER_USER_MARKET,
        "stake_limit_cr": format_cr(to_micro(page.viewer.stake_limit_cr)),
        "stake_left_cr": "0",
    }
    assert await written(container) == before
    await assert_books_balance(container.uow_factory)


async def test_an_open_phone_is_told_of_every_trade_and_every_admin_action_on_its_market(
    api: Api, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    """SPEC §8 step 9 and §9: the stream of a market starts with its state, then carries
    a quote event for every committed trade, with the price that trade was answered
    with, and a status event for every committed admin action."""
    (paula,) = await registered(factory, clock, "paula")
    (alice,) = await registered(factory, clock, "alice", balance_micro=GRANT)
    admin = await factory.admin()
    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    await api.done(admin, market_id, "approve", tier="DEFAULT")

    async def heard(events: AsyncIterator[LiveEvent]) -> LiveEvent:
        return await asyncio.wait_for(anext(events), timeout=STREAM_SECONDS)

    async with container.live_quotes.subscribe(market_id) as events:
        at_connect = await heard(events)
        bought = await api.tap_buy(alice, market_id, Outcome.YES, largest)
        after_the_buy = await heard(events)
        sold = await api.tap_sell(alice, market_id, Outcome.YES, SellPortion.HALF)
        after_the_sale = await heard(events)
        statuses: list[LiveEvent] = []
        for action, body in (
            ("halt", {"reason": "The source is in doubt."}),
            ("resume", {}),
            ("close", {}),
            ("resolve", {"outcome": "YES", "evidence": EVIDENCE}),
        ):
            await api.done(admin, market_id, action, **body)
            statuses.append(await heard(events))

    assert isinstance(at_connect, QuoteEvent)
    assert (at_connect.version, at_connect.p_yes, at_connect.volume_cr) == (0, 0.5, NOTHING)
    for event, trade, volume in (
        (after_the_buy, bought, bought.amount_cr),
        (after_the_sale, sold, bought.amount_cr + sold.amount_cr),
    ):
        assert isinstance(event, QuoteEvent)
        assert (event.market_id, event.version) == (market_id, trade.market_version)
        assert (event.p_yes, event.p_no) == (trade.p_yes_after, trade.p_no_after)
        assert event.volume_cr == volume
    assert [
        (event.status, event.trading_open) for event in statuses if isinstance(event, StatusEvent)
    ] == [
        (MarketStatus.HALTED, False),
        (MarketStatus.OPEN, True),
        (MarketStatus.CLOSED, False),
        (MarketStatus.RESOLVED, False),
    ]
    await assert_books_balance(container.uow_factory)


async def test_the_chart_of_a_market_starts_at_its_approval_and_follows_every_trade(
    api: Api,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The approval queues the opening point of the chart and every trade queues its
    own (A.4). Until the relay has copied them the chart is read from Postgres; once
    it has, the same chart comes from the history store, here one kept in memory where
    ScyllaDB stands."""
    store = MemoryHistory()
    await store.ensure_schema()
    chart_service = HistoryService(
        container.uow_factory, container.settings, container.clock, container.market_view, store
    )
    monkeypatch.setattr(container, "history", chart_service)
    relay = OutboxRelay(container.uow_factory, store, container.settings.scylla, container.clock)
    (paula,) = await registered(factory, clock, "paula")
    alice, bob = await registered(factory, clock, "alice", "bob", balance_micro=GRANT)
    admin = await factory.admin()
    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    opened = await api.done(admin, market_id, "approve", tier="DEFAULT")
    bought: list[TradeResponse] = []
    for who, outcome, choose in (
        (alice, Outcome.YES, largest),
        (bob, Outcome.NO, smallest),
        (bob, Outcome.YES, smallest),
    ):
        clock.advance(MINUTE)
        bought.append(await api.tap_buy(who, market_id, outcome, choose))
    path = f"/markets/{market_id}/history"

    from_postgres = await api.read(alice, path, HistoryResponse)
    delivered = await relay.drain_once()
    from_the_store = await api.read(alice, path, HistoryResponse)

    assert from_postgres.source is HistorySource.POSTGRES
    assert delivered == 1 + len(bought), "the opening point and one point a trade"
    assert from_the_store.source is HistorySource.SCYLLA
    assert from_the_store.points == from_postgres.points
    assert opened.opened_at is not None
    assert from_the_store.points[0] == HistoryPoint(t=opened.opened_at, p_yes=0.5, version=0)
    assert [(point.version, point.t, point.p_yes) for point in from_the_store.points[1:]] == [
        (trade.market_version, trade.created_at, trade.p_yes_after) for trade in bought
    ]
    assert await unpublished_ids(container.uow_factory) == []
    await assert_books_balance(container.uow_factory)


# --- When the books of a market are wrong ------------------------------------------------------


async def test_a_trade_on_broken_books_halts_the_market_and_tells_the_admins_on_the_risk_page(
    api: Api,
    container: Container,
    factory: Factory,
    clock: FrozenClock,
    owner_engine: AsyncEngine,
) -> None:
    """SPEC §5, the circuit breaker, across the services: a share nobody bought appears
    in a position, the next trade is rolled back and answered 503, the market is
    halted, and the risk page shows why. An admin acknowledges the alert; resuming the
    market does not mend the books, and the next trade halts it again. Once the books
    are right, the market trades."""
    paula = await factory.user()
    alice, bob = [await factory.user(balance_micro=GRANT) for _ in range(2)]
    admin = await factory.admin()
    market_id = (await api.propose(paula, clock.now(), MarketTier.DEFAULT)).id
    await api.done(admin, market_id, "approve", tier="DEFAULT")
    await api.tap_buy(alice, market_id, Outcome.YES, smallest)
    await assert_books_balance(container.uow_factory)

    async def bend(by: int) -> None:
        async with owner_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE positions SET shares_micro = shares_micro + :by"
                    " WHERE market_id = :market_id"
                ),
                {"by": by, "market_id": market_id},
            )

    await bend(1)
    balance = await factory.balance_micro(bob)
    trades = await count_rows(container.uow_factory, Trade)

    tripped = await api.trade(bob, market_id, side="BUY", outcome="NO", amount_cr="25")

    refusal(tripped, 503, "SERVICE_UNAVAILABLE")
    assert await factory.balance_micro(bob) == balance
    assert await count_rows(container.uow_factory, Trade) == trades
    assert (await stored(container, market_id)).status is MarketStatus.HALTED
    assert (await api.market(bob, market_id)).status is MarketStatus.HALTED
    risk = await api.read(admin, "/admin/risk", RiskResponse)
    (alert,) = risk.alerts
    (row,) = risk.markets
    assert (alert.severity, alert.message) == (
        AlertSeverity.CRITICAL,
        "Invariant violated: positions_match_market",
    )
    assert (alert.context["market_id"], alert.context["user_id"]) == (str(market_id), str(bob.id))
    assert (row.id, row.status) == (market_id, MarketStatus.HALTED)
    (halt,) = [
        entry
        for entry in await all_rows(container, AuditLogEntry)
        if entry.action == AuditAction.MARKET_HALT
    ]
    assert halt.actor_id is None, "the system halted it, not a person"

    acknowledged = await api.send(admin, "POST", f"/admin/alerts/{alert.id}/ack")
    twice = await api.send(admin, "POST", f"/admin/alerts/{alert.id}/ack")

    assert acknowledged.status_code == 200, acknowledged.text
    assert AlertItem.model_validate(acknowledged.json()).acked_at == clock.now()
    refusal(twice, 409, "CONFLICT")
    assert (await api.read(admin, "/admin/risk", RiskResponse)).alerts == []

    # Resuming does not mend the books: the next trade finds them as they are.
    await api.done(admin, market_id, "resume")
    refusal(
        await api.trade(bob, market_id, side="BUY", outcome="NO", amount_cr="25"),
        503,
        "SERVICE_UNAVAILABLE",
    )
    assert (await stored(container, market_id)).status is MarketStatus.HALTED
    assert await count_rows(container.uow_factory, Alert) == 2
    found = violations(await read_books(container.uow_factory))
    assert [line.partition(":")[0] for line in found] == ["positions_match_market"]

    await bend(-1)
    await api.done(admin, market_id, "resume")
    api.executed(bob, await api.trade(bob, market_id, side="BUY", outcome="NO", amount_cr="25"))

    assert (await stored(container, market_id)).status is MarketStatus.OPEN
    assert await factory.balance_micro(bob) == balance - api.paid(bob)
    await assert_books_balance(container.uow_factory)


# --- From the first sign-in to the counter -----------------------------------------------------


def somebody(mailbox: str, name: str) -> PortalIdentity:
    """Somebody the portal signed in. The role is not sent: the API derives it from the
    address, and `portal_headers` only takes the address and the name."""
    return PortalIdentity(email=mailbox, name=name, role=UserRole.USER)


class Portal:
    """The routes, called with the two headers of the portal and nothing else: the
    real `authenticate_request`, the real `IdentityService`, the upkeep it starts."""

    def __init__(self, client: httpx.AsyncClient, headers: PortalHeaders) -> None:
        self._client = client
        self._headers = headers

    async def send(
        self,
        who: PortalIdentity,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: UUID | None = None,
    ) -> httpx.Response:
        headers = self._headers(who) | ({} if key is None else {"Idempotency-Key": str(key)})
        return await self._client.request(method, f"/api{path}", json=body, headers=headers)

    async def ok[ModelT: BaseModel](
        self,
        who: PortalIdentity,
        method: str,
        path: str,
        model: type[ModelT],
        body: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        key: UUID | None = None,
    ) -> ModelT:
        response = await self.send(who, method, path, body, key=key)
        assert response.status_code == status_code, response.text
        return model.model_validate(response.json())


@pytest.fixture
def portal(client: httpx.AsyncClient, portal_headers: PortalHeaders) -> Portal:
    return Portal(client, portal_headers)


async def test_a_credit_is_followed_from_the_first_sign_in_to_the_counter_and_none_is_made_or_lost(
    portal: Portal, container: Container, clock: FrozenClock
) -> None:
    """Nothing is put in place by the test: five people the portal names set up their
    profile, a code is redeemed, a market is proposed, approved, traded and resolved,
    and a winner turns credits into a voucher a barista scans. Every request carries
    the portal's headers and nothing else.

    At the end every credit is accounted for (SPEC §1): what `vis:issuance` gave out is
    the signup grants and the one code; every user's balance is the sum of the lines
    of their ledger; and what the risk page calls the users' balances is their sum.
    """
    settings = container.settings
    grant = settings.grants.signup_grant_micro
    product = settings.vouchers.products[0]
    board = somebody(TEST_ADMIN_EMAILS[0], "Vera Vorstand")
    counter = somebody(TEST_BARISTA_EMAILS[0], "Bruno Barista")
    nora = somebody("nora@ethz.ch", "Nora Neu")
    omar = somebody("omar@ethz.ch", "Omar Omlette")
    pia = somebody("pia@ethz.ch", "Pia Pfeiffer")
    usernames = {board: "the_board", counter: "the_counter", nora: "nora", omar: "omar", pia: "pia"}
    roles = {board: UserRole.ADMIN, counter: UserRole.BARISTA}

    # Everybody signs in for the first time and sets up a profile.
    ids: dict[PortalIdentity, UUID] = {}
    for who, username in usernames.items():
        gate = await portal.send(who, "GET", "/me")
        assert refusal(gate, 403, "PROFILE_REQUIRED")["details"] == {
            "name": who.name,
            "email": who.email,
        }
        saved = await portal.ok(
            who,
            "PUT",
            "/me/profile",
            ProfileSavedResponse,
            {"username": username, "degree_level": "OTHER", "degree_programme": "ETH staff"},
        )
        assert saved.signup_granted
        assert (saved.balance_cr, saved.role) == (to_cr(grant), roles.get(who, UserRole.USER))
        assert (saved.email, saved.name, saved.username) == (who.email, who.name, username)
        ids[who] = saved.id
    await assert_books_balance(container.uow_factory)

    # The board hands out codes; one is redeemed, and not by the board.
    batch = await portal.ok(
        board,
        "POST",
        "/admin/code-batches",
        CodeBatchCreatedResponse,
        {
            "label": "Welcome week",
            "count": 2,
            "value_cr": str(CODE_VALUE),
            "expires_at": (clock.now() + WEEK).isoformat(),
            "one_per_user": True,
        },
        status_code=201,
    )
    first, second = (code.code for code in batch.codes)
    redeemed = await portal.ok(nora, "POST", "/codes/redeem", RedeemCodeResponse, {"code": first})
    own = await portal.send(board, "POST", "/codes/redeem", {"code": second})
    greedy = await portal.send(nora, "POST", "/codes/redeem", {"code": second})
    assert (redeemed.value_cr, redeemed.balance_cr) == (CODE_VALUE, to_cr(grant) + CODE_VALUE)
    assert refusal(own, 403, "FORBIDDEN")["message"] == OWN_BATCH_MESSAGE
    assert refusal(greedy, 409, "LIMIT_EXCEEDED")["message"] == ONE_PER_USER_MESSAGE
    await assert_books_balance(container.uow_factory)

    # Omar proposes, the board approves, Nora and Pia trade, the board settles.
    close_at = clock.now() + WEEK
    proposed = await portal.ok(
        omar,
        "POST",
        "/markets",
        MarketDetailResponse,
        {
            "question": "Will the Polymensa serve Älplermagronen on Friday?",
            "yes_definition": "It is on the lunch menu that day.",
            "no_definition": "It is not on the lunch menu that day.",
            "resolution_criteria": "The menu published for that Friday decides.",
            "resolution_source": "The menu plan on ethz.ch",
            "close_at": close_at.isoformat(),
            "resolve_by": (close_at + WEEK).isoformat(),
            "requested_tier": "TIGHT",
        },
        status_code=201,
    )
    market = f"/markets/{proposed.id}"
    await portal.ok(
        board, "POST", f"/admin{market}/approve", MarketDetailResponse, {"tier": "TIGHT"}
    )
    clock.advance(MINUTE)
    yes = await portal.ok(
        nora,
        "POST",
        f"{market}/trades",
        TradeResponse,
        {"side": "BUY", "outcome": "YES", "amount_cr": "100"},
        status_code=201,
        key=uuid7(),
    )
    clock.advance(MINUTE)
    no = await portal.ok(
        pia,
        "POST",
        f"{market}/trades",
        TradeResponse,
        {"side": "BUY", "outcome": "NO", "amount_cr": "50"},
        status_code=201,
        key=uuid7(),
    )
    clock.advance(HOUR)
    await portal.ok(board, "POST", f"/admin{market}/close", MarketDetailResponse, {})
    settled = await portal.ok(
        board,
        "POST",
        f"/admin{market}/resolve",
        MarketDetailResponse,
        {"outcome": "YES", "evidence": EVIDENCE},
    )
    assert (settled.status, settled.outcome) == (MarketStatus.RESOLVED, ResolutionOutcome.YES)
    await assert_books_balance(container.uow_factory)

    # Nora spends part of what she has at the counter.
    offered = await portal.ok(nora, "GET", "/vouchers/products", VoucherProductListResponse)
    voucher = await portal.ok(
        nora,
        "POST",
        "/vouchers",
        VoucherIssuedResponse,
        {"product_id": product.id},
        status_code=201,
    )
    not_staff = await portal.send(nora, "POST", "/barista/vouchers/redeem", {"code": voucher.code})
    scanned = await portal.ok(
        counter, "POST", "/barista/vouchers/redeem", VoucherRedeemResponse, {"code": voucher.code}
    )
    scanned_twice = await portal.send(
        counter, "POST", "/barista/vouchers/redeem", {"code": voucher.code}
    )
    assert offered.items[0].affordable
    refusal(not_staff, 403, "FORBIDDEN")
    assert (scanned.voucher_id, scanned.product_name) == (voucher.id, product.name)
    refusal(scanned_twice, 400, "CODE_INVALID")
    await assert_books_balance(container.uow_factory)

    # Every credit is accounted for.
    balances: dict[PortalIdentity, Decimal] = {}
    for who in usernames:
        me = await portal.ok(who, "GET", "/me", MeResponse)
        ledger = await portal.ok(who, "GET", "/me/ledger", LedgerListResponse)
        assert sum(item.amount_cr for item in ledger.items) == me.balance_cr, who.email
        balances[who] = me.balance_cr
    nora_ledger = await portal.ok(nora, "GET", "/me/ledger", LedgerListResponse)
    assert [(item.description, item.amount_cr) for item in nora_ledger.items] == [
        (ledger_description(JournalKind.VOUCHER_ISSUE, product.name), -to_cr(product.price_micro)),
        (ledger_description(JournalKind.MARKET_RESOLUTION), yes.shares),
        (ledger_description(JournalKind.TRADE_BUY), -yes.amount_cr),
        (ledger_description(JournalKind.CODE_REDEMPTION), CODE_VALUE),
        (ledger_description(JournalKind.SIGNUP_GRANT), to_cr(grant)),
    ]
    assert balances[pia] == to_cr(grant) - no.amount_cr, "NO lost: the stake is gone"
    assert balances[omar] == balances[board] == balances[counter] == to_cr(grant)

    issued = -await system_balance(container.uow_factory, AccountKind.VIS_ISSUANCE)
    assert issued == len(usernames) * grant + to_micro(redeemed.value_cr)
    assert (
        await system_balance(container.uow_factory, AccountKind.VIS_REDEEMED) == product.price_micro
    )
    assert await system_balance(container.uow_factory, AccountKind.VIS_REDEMPTION_PENDING) == 0
    risk = await portal.ok(board, "GET", "/admin/risk", RiskResponse)
    assert risk.obligations.user_balances_cr == sum(balances.values())
    assert risk.obligations.pending_vouchers_cr == 0
    assert risk.obligations.market_exposure_cr == 0
    assert risk.markets == []
    assert risk.unredeemed_code_value_cr == CODE_VALUE, "the code nobody redeemed"
    assert risk.code_issuance_this_month_cr == 2 * CODE_VALUE
    audited = {(row.action, row.actor_id) for row in await all_rows(container, AuditLogEntry)}
    assert audited == {
        (action, ids[board])
        for action in (
            AuditAction.CODE_BATCH_CREATE,
            AuditAction.MARKET_APPROVE,
            AuditAction.MARKET_CLOSE,
            AuditAction.MARKET_RESOLVE,
        )
    }
    assert violations(await read_books(container.uow_factory)) == []
    await assert_books_balance(container.uow_factory)
