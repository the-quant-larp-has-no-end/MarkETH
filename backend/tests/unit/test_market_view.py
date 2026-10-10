"""`MarketView`: the numbers every screen shows of a market (SPEC §4, A.2, A.4).

The market of these tests is one of the DEFAULT tier of the test settings, and the
limits a result depends on (the stake limit, the stake presets, the share shortcuts, the
smallest trade) are read from them. The slider of a buy is a rule about settings and
two amounts, so its tests name their own: a step, a smallest trade, a stake left and a
balance. What a viewer can buy in shares depends on the market maker's prices as well:
every expected number of shares, cost and proceeds is asked of `app.pricing.lmsr`.
"""

import logging
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any, Final
from uuid import UUID, uuid7

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from app.core.config import Settings
from app.core.enums import (
    STORED_MARKET_STATUSES,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
)
from app.core.errors import InsufficientBalanceError, LimitExceededError
from app.core.units import MICRO, format_cr, to_cr, to_micro
from app.models import Market, Position, Resolution
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.markets import BuyEntry, BuySlider, SellPortion, SellSlider
from app.schemas.trading import TradeSize
from app.services.market_view import (
    EVEN_ODDS_MICRO,
    HIDDEN_STATUSES,
    TRADING_STATUSES,
    MarketView,
)
from app.services.quotes import QuoteService
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.economy import DEFAULT_B, PRECISION, STAKE_LIMIT, TIER_B
from tests.support.settings import load_base_settings

CR = MICRO
DAY = timedelta(days=1)
USER_ID = uuid7()

TRADED_YES = 191 * CR
"""YES shares that take a fresh market of the DEFAULT tier from 50% to about 54.8%:
close to what a full stake buys there (SPEC §4 "Choosing b")."""

ODD_HOLDING = 190_902_829
"""A holding that cannot be halved to six places: one µshare more than a full stake buys
in a fresh market of the DEFAULT tier."""


@pytest.fixture
def view(base_settings: Settings) -> MarketView:
    return MarketView(base_settings)


def market(**overrides: Any) -> Market:
    values: dict[str, Any] = {
        "id": uuid7(),
        "question": "Will it snow on the Polyterrasse before December?",
        "yes_def": "Snow is lying.",
        "no_def": "No snow is lying.",
        "resolution_criteria": "The webcam decides.",
        "resolution_source": "ethz.ch/webcam",
        "status": MarketStatus.OPEN,
        "tier": MarketTier.DEFAULT,
        "budget_micro": lmsr.max_loss_micro(DEFAULT_B),
        "b_micro": DEFAULT_B,
        "q_yes_micro": 0,
        "q_no_micro": 0,
        "version": 0,
        "volume_micro": 0,
        "close_at": TEST_NOW + 7 * DAY,
        "resolve_by": TEST_NOW + 14 * DAY,
        "created_by": USER_ID,
        "created_at": TEST_NOW,
    }
    return Market(**(values | overrides))


def position(market_id: UUID, outcome: Outcome, **amounts: int) -> Position:
    values = {"shares_micro": 0, "cost_micro": 0, "proceeds_micro": 0} | amounts
    return Position(user_id=USER_ID, market_id=market_id, outcome=outcome, **values)


def resolution(market_id: UUID, outcome: ResolutionOutcome) -> Resolution:
    return Resolution(
        market_id=market_id,
        outcome=outcome,
        evidence="The webcam showed it.",
        resolved_by=USER_ID,
        created_at=TEST_NOW,
    )


def test_an_open_market_before_its_deadline_is_open(view: MarketView) -> None:
    open_market = market()
    assert view.effective_status(open_market, TEST_NOW) is MarketStatus.OPEN
    assert view.is_trading_open(open_market, TEST_NOW)


@pytest.mark.parametrize("late_by", [timedelta(0), timedelta(microseconds=1), DAY])
def test_an_open_market_is_reported_closed_from_its_deadline_on(
    view: MarketView, late_by: timedelta
) -> None:
    """A.4: no job flips the column; `now < close_at` is checked wherever it matters."""
    open_market = market()
    at = open_market.close_at + late_by
    assert view.effective_status(open_market, at) is MarketStatus.CLOSED
    assert not view.is_trading_open(open_market, at)


def test_a_proposal_before_its_deadline_is_proposed(view: MarketView) -> None:
    proposal = market(status=MarketStatus.PROPOSED, tier=None, b_micro=0, budget_micro=0)
    just_before = proposal.close_at - timedelta(microseconds=1)

    assert view.effective_status(proposal, TEST_NOW) is MarketStatus.PROPOSED
    assert view.effective_status(proposal, just_before) is MarketStatus.PROPOSED


@pytest.mark.parametrize("late_by", [timedelta(0), timedelta(microseconds=1), DAY])
def test_a_proposal_is_reported_expired_from_its_deadline_on(
    view: MarketView, late_by: timedelta
) -> None:
    """The instant of `close_at` itself is past the deadline, as it is for an OPEN
    market. The row is not touched: no job flips the column here either."""
    proposal = market(status=MarketStatus.PROPOSED, tier=None, b_micro=0, budget_micro=0)
    at = proposal.close_at + late_by

    assert view.effective_status(proposal, at) is MarketStatus.EXPIRED
    assert proposal.status is MarketStatus.PROPOSED
    assert not view.is_trading_open(proposal, at)


@pytest.mark.parametrize(
    "status",
    [
        status
        for status in STORED_MARKET_STATUSES
        if status not in (MarketStatus.OPEN, MarketStatus.PROPOSED)
    ],
)
def test_only_an_open_market_and_a_proposal_change_status_with_time(
    view: MarketView, status: MarketStatus
) -> None:
    stored = market(status=status)
    for at in (TEST_NOW, stored.close_at, stored.close_at + DAY):
        assert view.effective_status(stored, at) is status
        assert not view.is_trading_open(stored, at)


def test_the_effective_status_of_every_stored_one_is_stored_except_expired(
    view: MarketView,
) -> None:
    """EXPIRED is the one status that is reported and never kept: every other answer
    of `effective_status` is a status a row may hold."""
    reported = {
        view.effective_status(market(status=status), at)
        for status in STORED_MARKET_STATUSES
        for at in (TEST_NOW, TEST_NOW + 30 * DAY)
    }

    assert reported == set(MarketStatus)
    assert reported - set(STORED_MARKET_STATUSES) == {MarketStatus.EXPIRED}


@pytest.mark.parametrize(
    ("asked", "stored"),
    [
        ({MarketStatus.OPEN}, {MarketStatus.OPEN}),
        ({MarketStatus.CLOSED}, {MarketStatus.CLOSED, MarketStatus.OPEN}),
        ({MarketStatus.PROPOSED}, {MarketStatus.PROPOSED}),
        ({MarketStatus.EXPIRED}, {MarketStatus.PROPOSED}),
        (
            {MarketStatus.EXPIRED, MarketStatus.REJECTED},
            {MarketStatus.PROPOSED, MarketStatus.REJECTED},
        ),
        (set(), set()),
        (set(MarketStatus), set(STORED_MARKET_STATUSES)),
    ],
    ids=["open", "closed", "proposed", "expired", "expired-and-rejected", "none", "all"],
)
def test_a_list_reads_the_stored_statuses_its_effective_ones_can_have(
    view: MarketView, asked: set[MarketStatus], stored: set[MarketStatus]
) -> None:
    assert view.stored_statuses(asked) == stored


@pytest.mark.parametrize("effective", list(MarketStatus))
def test_every_effective_status_is_found_among_the_stored_statuses_read_for_it(
    view: MarketView, effective: MarketStatus
) -> None:
    """Whatever `effective_status` can answer for a row, a list that asks for that
    answer reads the row: the two functions are each other's inverse."""
    read = view.stored_statuses({effective})

    assert MarketStatus.EXPIRED not in read
    for status in STORED_MARKET_STATUSES:
        for at in (TEST_NOW, TEST_NOW + 30 * DAY):
            if view.effective_status(market(status=status), at) is effective:
                assert status in read


# --- When trading ended ------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(TRADING_STATUSES))
def test_trading_is_not_over_in_an_open_or_a_halted_market_before_its_deadline(
    view: MarketView, status: MarketStatus
) -> None:
    """A halted market trades nothing for now, and it may be resumed."""
    tradable = market(status=status)
    just_before = tradable.close_at - timedelta(microseconds=1)

    assert view.trading_ended_at(tradable, TEST_NOW) is None
    assert view.trading_ended_at(tradable, just_before) is None


@pytest.mark.parametrize("status", sorted(TRADING_STATUSES))
@pytest.mark.parametrize("late_by", [timedelta(0), timedelta(microseconds=1), 30 * DAY])
def test_from_its_deadline_on_trading_in_an_open_or_a_halted_market_ended_at_the_deadline(
    view: MarketView, status: MarketStatus, late_by: timedelta
) -> None:
    """Nobody closed it, so no row says when: `close_at` is the moment, also for a
    halted market, which could have been resumed until then and not after."""
    past = market(status=status)

    assert view.trading_ended_at(past, past.close_at + late_by) == past.close_at


@pytest.mark.parametrize("status", [MarketStatus.CLOSED, MarketStatus.RESOLVED, MarketStatus.VOID])
def test_trading_in_a_market_that_was_closed_ended_when_it_was_closed_or_at_its_deadline(
    view: MarketView, status: MarketStatus
) -> None:
    """Whichever came first. A market is resolved or voided no earlier than it was
    closed, so `closed_at` is all a settled one needs. The answer does not move with
    the clock."""
    early = market(status=status, closed_at=TEST_NOW + 2 * DAY)
    late = market(status=status, closed_at=TEST_NOW + 9 * DAY)
    on_time = market(status=status, closed_at=TEST_NOW + 7 * DAY)

    for at in (TEST_NOW, TEST_NOW + 3 * DAY, TEST_NOW + 30 * DAY):
        assert view.trading_ended_at(early, at) == TEST_NOW + 2 * DAY
        assert view.trading_ended_at(late, at) == late.close_at == TEST_NOW + 7 * DAY
        assert view.trading_ended_at(on_time, at) == on_time.close_at


def test_a_closed_market_whose_row_does_not_say_when_ended_at_its_deadline(
    view: MarketView,
) -> None:
    closed = market(status=MarketStatus.CLOSED)

    assert closed.closed_at is None
    assert view.trading_ended_at(closed, TEST_NOW) == closed.close_at


@given(
    status=st.sampled_from([s for s in STORED_MARKET_STATUSES if s not in HIDDEN_STATUSES]),
    closed_after=st.none() | st.timedeltas(min_value=timedelta(0), max_value=30 * DAY),
    asked_after=st.timedeltas(min_value=timedelta(0), max_value=30 * DAY),
)
def test_trading_never_ended_after_the_deadline_and_never_after_now_once_it_is_over(
    base_settings: Settings,
    status: MarketStatus,
    closed_after: timedelta | None,
    asked_after: timedelta,
) -> None:
    """For a market in any status a chart is drawn of: the end of trading is at most
    `close_at`, and it is None exactly while the row says OPEN or HALTED and the
    deadline has not come."""
    view = MarketView(base_settings)
    closed_at = None if closed_after is None else TEST_NOW + closed_after
    opened = market(status=status, closed_at=closed_at)
    now = TEST_NOW + asked_after

    ended_at = view.trading_ended_at(opened, now)

    can_still_trade = status in TRADING_STATUSES and now < opened.close_at
    assert (ended_at is None) == can_still_trade
    if ended_at is not None:
        assert ended_at <= opened.close_at
        assert ended_at in (opened.close_at, closed_at)


def test_a_fresh_market_is_priced_at_fifty_fifty(view: MarketView) -> None:
    assert view.p_yes_micro(market()) == EVEN_ODDS_MICRO
    assert view.probabilities(EVEN_ODDS_MICRO) == (0.5, 0.5)


def test_a_market_that_was_never_opened_shows_even_odds(view: MarketView) -> None:
    proposed = market(status=MarketStatus.PROPOSED, tier=None, b_micro=0, budget_micro=0)
    assert view.p_yes_micro(proposed) == EVEN_ODDS_MICRO


def test_the_price_follows_the_quantities(view: MarketView) -> None:
    """SPEC §4: about 191 YES shares at the default b move the price to about 54.8%."""
    traded = market(q_yes_micro=TRADED_YES)
    p_yes_micro = view.p_yes_micro(traded)
    assert 547_000 < p_yes_micro < 548_500
    p_yes, p_no = view.probabilities(p_yes_micro)
    assert (p_yes, p_no) == (0.548, 0.452)


def test_no_is_the_complement_of_the_rounded_yes(view: MarketView) -> None:
    for p_yes_micro in (0, 1, 333_333, 499_500, 547_499, 999_999, MICRO):
        p_yes, p_no = view.probabilities(p_yes_micro)
        assert round(p_yes + p_no, 9) == 1.0


def test_average_price_is_credits_per_share_at_display_precision(view: MarketView) -> None:
    assert view.average_price(amount_micro=100 * CR, shares_micro=TRADED_YES) == 0.524
    assert view.average_price(amount_micro=1, shares_micro=1) == 1.0
    with pytest.raises(ValueError, match="shares_micro"):
        view.average_price(amount_micro=1, shares_micro=0)


def test_summary_reports_the_effective_status_and_display_numbers(view: MarketView) -> None:
    traded = market(q_yes_micro=TRADED_YES, version=3, volume_micro=125 * CR)
    summary = view.summary(traded, TEST_NOW, None)
    assert summary.id == traded.id
    assert summary.status is MarketStatus.OPEN
    assert summary.trading_open is True
    assert (summary.p_yes, summary.p_no) == (0.548, 0.452)
    assert summary.volume_cr == to_cr(125 * CR)
    assert summary.version == 3
    assert summary.outcome is None

    late = view.summary(traded, traded.close_at, None)
    assert late.status is MarketStatus.CLOSED
    assert late.trading_open is False


def test_summary_carries_the_outcome_of_a_settled_market(view: MarketView) -> None:
    resolved = market(status=MarketStatus.RESOLVED)
    summary = view.summary(resolved, TEST_NOW, resolution(resolved.id, ResolutionOutcome.NO))
    assert summary.status is MarketStatus.RESOLVED
    assert summary.outcome is ResolutionOutcome.NO


@pytest.mark.parametrize("status", [MarketStatus.RESOLVED, MarketStatus.VOID])
def test_summary_refuses_a_settled_market_without_its_resolution(
    view: MarketView, status: MarketStatus
) -> None:
    with pytest.raises(ValueError, match="no resolution"):
        view.summary(market(status=status), TEST_NOW, None)


def test_net_stake_is_cost_minus_proceeds_over_both_outcomes(
    view: MarketView, base_settings: Settings
) -> None:
    """SPEC §4: selling lowers the net stake, so a user can buy again up to the limit."""
    limit = base_settings.pricing.max_stake_per_user_market_micro
    market_id = uuid7()
    positions = [
        position(market_id, Outcome.YES, cost_micro=80 * CR, proceeds_micro=30 * CR),
        position(market_id, Outcome.NO, cost_micro=20 * CR),
    ]
    assert view.net_stake_micro(positions) == 70 * CR
    assert view.stake_left_micro(70 * CR) == limit - 70 * CR
    assert view.net_stake_micro([]) == 0
    assert view.stake_left_micro(0) == limit


def test_net_stake_is_floored_at_zero_and_stake_left_at_the_limit(
    view: MarketView, base_settings: Settings
) -> None:
    limit = base_settings.pricing.max_stake_per_user_market_micro
    market_id = uuid7()
    in_profit = [position(market_id, Outcome.YES, cost_micro=40 * CR, proceeds_micro=90 * CR)]
    assert view.net_stake_micro(in_profit) == 0
    assert view.stake_left_micro(0) == limit
    assert view.stake_left_micro(limit - 1) == 1
    assert view.stake_left_micro(limit) == 0
    assert view.stake_left_micro(limit + 1) == 0


def test_holdings_are_valued_at_the_current_price_rounded_down(view: MarketView) -> None:
    traded = market(q_yes_micro=TRADED_YES)
    p_yes_micro = view.p_yes_micro(traded)
    positions = [
        position(traded.id, Outcome.YES, shares_micro=TRADED_YES),
        position(traded.id, Outcome.NO, shares_micro=3),
    ]
    yes, no = view.holdings(traded, positions, None)
    assert (yes.outcome, no.outcome) == (Outcome.YES, Outcome.NO)
    assert yes.shares == to_cr(TRADED_YES)
    assert yes.value_cr == to_cr(TRADED_YES * p_yes_micro // MICRO)
    assert no.value_cr == to_cr(3 * (MICRO - p_yes_micro) // MICRO)


def test_holdings_always_list_yes_then_no_even_when_empty(view: MarketView) -> None:
    yes, no = view.holdings(market(), [], None)
    nothing = to_cr(0)
    assert (yes.outcome, yes.shares, yes.value_cr) == (Outcome.YES, nothing, nothing)
    assert (no.outcome, no.shares, no.value_cr) == (Outcome.NO, nothing, nothing)


def test_in_a_resolved_market_a_winning_share_is_worth_one_credit(view: MarketView) -> None:
    resolved = market(status=MarketStatus.RESOLVED, q_yes_micro=2 * CR, q_no_micro=CR)
    positions = [
        position(resolved.id, Outcome.YES, shares_micro=2 * CR),
        position(resolved.id, Outcome.NO, shares_micro=CR),
    ]
    yes, no = view.holdings(resolved, positions, resolution(resolved.id, ResolutionOutcome.NO))
    assert yes.value_cr == 0
    assert no.value_cr == to_cr(CR)


def test_in_a_void_market_holdings_are_worth_nothing(view: MarketView) -> None:
    void = market(status=MarketStatus.VOID)
    assert (
        view.holding_value_micro(void, Outcome.YES, CR, resolution(void.id, ResolutionOutcome.VOID))
        == 0
    )


def test_summary_times_are_the_rows_own(view: MarketView) -> None:
    stored = market()
    summary = view.summary(stored, TEST_NOW, None)
    assert isinstance(summary.close_at, datetime)
    assert (summary.close_at, summary.resolve_by) == (stored.close_at, stored.resolve_by)


# --- What a settled market paid or gave back ---------------------------------------


def test_a_resolved_market_paid_one_credit_per_share_of_the_winning_outcome(
    view: MarketView,
) -> None:
    """Resolution leaves positions as they are, so the payout is read from the shares."""
    market_id = uuid7()
    held = [
        position(market_id, Outcome.YES, shares_micro=2 * CR, cost_micro=CR),
        position(market_id, Outcome.NO, shares_micro=CR, cost_micro=CR, proceeds_micro=3 * CR),
    ]

    assert view.payout_micro(held, resolution(market_id, ResolutionOutcome.YES)) == 2 * CR
    assert view.payout_micro(held, resolution(market_id, ResolutionOutcome.NO)) == CR
    assert view.refund_micro(held, resolution(market_id, ResolutionOutcome.YES)) is None


def test_a_resolved_market_paid_nothing_to_someone_without_winning_shares(
    view: MarketView,
) -> None:
    """Nothing is an amount, 0, not the absence of one: the market did resolve."""
    market_id = uuid7()
    lost = [position(market_id, Outcome.NO, shares_micro=5 * CR, cost_micro=2 * CR)]
    sold_out = [position(market_id, Outcome.YES, cost_micro=2 * CR, proceeds_micro=3 * CR)]
    resolved_yes = resolution(market_id, ResolutionOutcome.YES)

    assert view.payout_micro(lost, resolved_yes) == 0
    assert view.payout_micro(sold_out, resolved_yes) == 0
    assert view.payout_micro([], resolved_yes) == 0


def test_a_void_market_gave_back_the_net_stake(view: MarketView) -> None:
    """A void zeroes the shares and keeps cost and proceeds, which the refund is read from."""
    market_id = uuid7()
    voided = resolution(market_id, ResolutionOutcome.VOID)
    out_of_pocket = [
        position(market_id, Outcome.YES, cost_micro=80 * CR, proceeds_micro=30 * CR),
        position(market_id, Outcome.NO, cost_micro=20 * CR),
    ]
    in_profit = [position(market_id, Outcome.YES, cost_micro=40 * CR, proceeds_micro=90 * CR)]

    assert view.refund_micro(out_of_pocket, voided) == 70 * CR
    assert view.refund_micro(out_of_pocket, voided) == view.net_stake_micro(out_of_pocket)
    assert view.refund_micro(in_profit, voided) == 0
    assert view.refund_micro([], voided) == 0
    assert view.payout_micro(out_of_pocket, voided) is None


def test_a_market_that_is_not_settled_has_paid_and_given_back_nothing_yet(
    view: MarketView,
) -> None:
    held = [position(uuid7(), Outcome.YES, shares_micro=2 * CR, cost_micro=CR)]

    assert view.payout_micro(held, None) is None
    assert view.refund_micro(held, None) is None


def test_voiding_a_market_costs_the_net_stakes_it_refunds_minus_what_its_account_holds(
    view: MarketView,
) -> None:
    """What traders who sold above their cost took out is what the account lacks."""
    assert view.void_cost_micro(net_stakes_micro=0, account_balance_micro=0) == 0
    assert view.void_cost_micro(net_stakes_micro=70 * CR, account_balance_micro=70 * CR) == 0
    assert view.void_cost_micro(net_stakes_micro=70 * CR, account_balance_micro=58 * CR) == 12 * CR
    assert view.void_cost_micro(net_stakes_micro=1, account_balance_micro=0) == 1


def test_the_void_cost_of_a_book_is_what_its_sellers_took_out_above_their_cost(
    view: MarketView,
) -> None:
    """Three traders: one still in, one who sold at a gain, one who sold at a loss. The
    account holds every cost minus every proceeds; a void refunds each net stake, and
    the gain of the second is what `vis:mm_pnl` would have to add."""
    market_id = uuid7()
    still_in = [position(market_id, Outcome.YES, cost_micro=60 * CR)]
    sold_at_a_gain = [position(market_id, Outcome.YES, cost_micro=40 * CR, proceeds_micro=52 * CR)]
    sold_at_a_loss = [position(market_id, Outcome.NO, cost_micro=30 * CR, proceeds_micro=20 * CR)]
    books = (still_in, sold_at_a_gain, sold_at_a_loss)
    account_micro = sum(p.cost_micro - p.proceeds_micro for held in books for p in held)
    net_stakes_micro = sum(view.net_stake_micro(held) for held in books)

    assert (account_micro, net_stakes_micro) == (58 * CR, 70 * CR)
    assert view.void_cost_micro(net_stakes_micro, account_micro) == 12 * CR


# --- Stake presets (A.11) ----------------------------------------------------------


def test_the_stake_limit_is_the_one_of_the_settings(
    view: MarketView, base_settings: Settings
) -> None:
    assert view.stake_limit_micro == base_settings.pricing.max_stake_per_user_market_micro
    assert view.stake_left_micro(0) == view.stake_limit_micro


def test_stake_presets_are_the_configured_amounts_in_their_order(
    view: MarketView, base_settings: Settings
) -> None:
    amounts = base_settings.pricing.stake_presets_micro
    assert len(amounts) >= 2
    plenty = 10 * max(amounts)

    presets = view.stake_presets(stake_left_micro=plenty, balance_micro=plenty, can_trade=True)

    assert [preset.amount_cr for preset in presets] == [to_cr(amount) for amount in amounts]
    assert all(preset.allowed for preset in presets)
    assert [preset.model_dump(mode="json")["amount_cr"] for preset in presets] == [
        format_cr(amount) for amount in amounts
    ], "an amount leaves as the decimal string every amount is"


def test_a_stake_preset_needs_the_balance_to_pay_for_it(
    view: MarketView, base_settings: Settings
) -> None:
    amounts = sorted(base_settings.pricing.stake_presets_micro)
    plenty = 10 * amounts[-1]

    def affordable(balance_micro: int) -> list[int]:
        presets = view.stake_presets(plenty, balance_micro, can_trade=True)
        return sorted(to_micro(preset.amount_cr) for preset in presets if preset.allowed)

    assert affordable(amounts[0] - 1) == []
    assert affordable(amounts[0]) == amounts[:1], "a balance of exactly the amount is enough"
    assert affordable(amounts[1]) == amounts[:2]
    assert affordable(amounts[-1]) == amounts


def test_a_stake_preset_needs_that_much_stake_left(
    view: MarketView, base_settings: Settings
) -> None:
    amounts = sorted(base_settings.pricing.stake_presets_micro)
    plenty = 10 * amounts[-1]

    def within_the_limit(stake_left_micro: int) -> list[int]:
        presets = view.stake_presets(stake_left_micro, plenty, can_trade=True)
        return sorted(to_micro(preset.amount_cr) for preset in presets if preset.allowed)

    assert within_the_limit(0) == []
    assert within_the_limit(amounts[0] - 1) == []
    assert within_the_limit(amounts[0]) == amounts[:1], "exactly the stake left still fits"
    assert within_the_limit(amounts[-1]) == amounts


def test_no_stake_preset_is_allowed_to_someone_who_may_not_trade(
    view: MarketView, base_settings: Settings
) -> None:
    plenty = 10 * max(base_settings.pricing.stake_presets_micro)

    presets = view.stake_presets(plenty, plenty, can_trade=False)

    assert len(presets) == len(base_settings.pricing.stake_presets_micro)
    assert not any(preset.allowed for preset in presets)


def test_a_stake_preset_below_the_minimum_trade_is_not_allowed(base_settings: Settings) -> None:
    """A button the trade would answer with TRADE_TOO_SMALL must not be offered."""
    minimum = base_settings.pricing.min_trade_micro
    pricing = base_settings.pricing.model_copy(
        update={"stake_presets_micro": [minimum - 1, minimum, minimum + 1]}
    )
    view = MarketView(base_settings.model_copy(update={"pricing": pricing}))

    presets = view.stake_presets(10 * minimum, 10 * minimum, can_trade=True)

    assert [preset.allowed for preset in presets] == [False, True, True]


def test_an_allowed_stake_preset_passes_the_stake_check_of_a_trade(
    view: MarketView, base_settings: Settings
) -> None:
    """The trade refuses a buy when cost - proceeds + amount exceeds the limit (A.4).
    The stake left is computed from the net stake, which is never below zero, so it
    never promises more than that check allows: also not to a user in profit, whose
    cost minus proceeds is negative."""
    limit = base_settings.pricing.max_stake_per_user_market_micro
    amounts = base_settings.pricing.stake_presets_micro
    market_id = uuid7()
    books = [
        [],
        [position(market_id, Outcome.YES, cost_micro=limit - min(amounts))],
        [position(market_id, Outcome.YES, cost_micro=limit - min(amounts) + 1)],
        [position(market_id, Outcome.YES, cost_micro=limit, proceeds_micro=limit // 4)],
        [position(market_id, Outcome.NO, cost_micro=limit // 2, proceeds_micro=2 * limit)],
    ]

    for positions in books:
        paid_in = sum(p.cost_micro - p.proceeds_micro for p in positions)
        stake_left = view.stake_left_micro(view.net_stake_micro(positions))
        for preset in view.stake_presets(stake_left, 10 * limit, can_trade=True):
            passes = paid_in + to_micro(preset.amount_cr) <= limit
            assert passes or not preset.allowed
            if paid_in >= 0:
                assert preset.allowed == passes, "out of profit the two are the same rule"


# --- The slider of a buy (A.11) ------------------------------------------------------


def slider_view(*, step_cr: int = 1, min_trade_micro: int = CR) -> MarketView:
    """A `MarketView` on the test settings with these two in place of
    `TRADE_STAKE_STEP_CR` and `MIN_TRADE_CR`."""
    settings = load_base_settings()
    pricing = settings.pricing.model_copy(
        update={"stake_step_micro": step_cr * CR, "min_trade_micro": min_trade_micro}
    )
    return MarketView(settings.model_copy(update={"pricing": pricing}))


def slider(
    stake_left_micro: int, balance_micro: int, *, step_cr: int = 1, min_trade_micro: int = CR
) -> tuple[int, int, int] | None:
    """`(min, max, step)` of the slider somebody who may trade gets, None for none."""
    found = slider_view(step_cr=step_cr, min_trade_micro=min_trade_micro).buy_slider(
        stake_left_micro, balance_micro, can_trade=True
    )
    return None if found is None else (found.min, found.max, found.step)


def test_the_slider_of_the_settings_runs_over_the_whole_stake_limit_in_whole_credits(
    view: MarketView, base_settings: Settings
) -> None:
    """With the tokens and nothing staked yet: from the smallest trade to the limit,
    in steps of TRADE_STAKE_STEP_CR."""
    pricing = base_settings.pricing
    limit, step = pricing.max_stake_per_user_market_micro, pricing.stake_step_micro
    assert step == CR, ".example.env steps by one credit"
    assert (pricing.min_trade_micro, limit % step) == (CR, 0)

    found = view.buy_slider(view.stake_left_micro(0), 10 * limit, can_trade=True)

    assert found == BuySlider(min=1, max=limit // CR, step=1)
    assert found.model_dump(mode="json") == {"min": 1, "max": limit // CR, "step": 1}, (
        "whole credits as JSON integers, which is what a range input takes"
    )


def test_the_slider_ends_at_the_stake_left_or_at_the_balance_whichever_is_less() -> None:
    assert slider(40 * CR, 70 * CR) == (1, 40, 1)
    assert slider(70 * CR, 40 * CR) == (1, 40, 1)
    assert slider(40 * CR, 40 * CR) == (1, 40, 1)


def test_what_is_beyond_a_whole_step_of_the_balance_or_the_stake_does_not_count() -> None:
    """A fractional balance: 10.5 credits buy for 10 on a slider of whole credits."""
    assert slider(100 * CR, 10 * CR + CR // 2) == (1, 10, 1)
    assert slider(100 * CR, 11 * CR - 1) == (1, 10, 1)
    assert slider(100 * CR, 11 * CR) == (1, 11, 1)
    assert slider(7 * CR + CR // 4, 100 * CR) == (1, 7, 1)
    # With a step of five credits, 14.999999 credits are two steps and 15 are three.
    assert slider(100 * CR, 15 * CR - 1, step_cr=5) == (5, 10, 5)
    assert slider(100 * CR, 15 * CR, step_cr=5) == (5, 15, 5)


def test_a_balance_below_the_smallest_buy_leaves_no_slider() -> None:
    """One micro-credit short of the smallest trade, and with nothing at all."""
    assert slider(100 * CR, CR - 1) is None
    assert slider(100 * CR, 0) is None
    assert slider(100 * CR, CR) == (1, 1, 1), "exactly the smallest buy is a slider of one position"
    # The smallest trade is 25 credits here, and the balance holds 24.999999 of them.
    assert slider(100 * CR, 25 * CR - 1, min_trade_micro=25 * CR) is None
    assert slider(100 * CR, 25 * CR, min_trade_micro=25 * CR) == (25, 25, 1)


def test_a_stake_left_below_the_step_leaves_no_slider() -> None:
    assert slider(5 * CR - 1, 100 * CR, step_cr=5) is None
    assert slider(0, 100 * CR, step_cr=5) is None
    assert slider(5 * CR, 100 * CR, step_cr=5) == (5, 5, 5)
    assert slider(CR - 1, 100 * CR) is None
    assert slider(0, 100 * CR) is None


def test_the_slider_starts_at_the_smallest_multiple_of_the_step_that_is_a_trade() -> None:
    plenty = 100 * CR

    def starts_at(step_cr: int, min_trade_micro: int) -> int:
        found = slider(plenty, plenty, step_cr=step_cr, min_trade_micro=min_trade_micro)
        assert found is not None
        return found[0]

    assert starts_at(1, CR) == 1
    assert starts_at(1, CR // 4) == 1, (
        "a quarter of a credit is a trade; the slider counts whole ones"
    )
    assert starts_at(1, 2 * CR + CR // 2) == 3
    assert starts_at(5, CR) == 5
    assert starts_at(5, 5 * CR) == 5
    assert starts_at(5, 5 * CR + 1) == 10
    assert starts_at(5, 12 * CR) == 15
    assert starts_at(25, CR) == 25
    # No smallest trade at all: a buy is still for more than nothing.
    assert starts_at(1, 0) == 1
    assert starts_at(5, 0) == 5


def test_no_slider_where_the_smallest_buy_is_beyond_what_is_left() -> None:
    """The smallest multiple of the step that is a trade is 15; 14.999999 are left."""
    assert slider(15 * CR - 1, 100 * CR, step_cr=5, min_trade_micro=12 * CR) is None
    assert slider(15 * CR, 100 * CR, step_cr=5, min_trade_micro=12 * CR) == (15, 15, 5)


def test_there_is_no_slider_for_someone_who_may_not_trade(
    view: MarketView, base_settings: Settings
) -> None:
    plenty = 10 * base_settings.pricing.max_stake_per_user_market_micro

    assert view.buy_slider(plenty, plenty, can_trade=True) is not None
    assert view.buy_slider(plenty, plenty, can_trade=False) is None


@given(
    step_cr=st.integers(min_value=1, max_value=25),
    min_trade_micro=st.integers(min_value=0, max_value=60 * CR),
    stake_left_micro=st.integers(min_value=0, max_value=120 * CR),
    balance_micro=st.integers(min_value=0, max_value=120 * CR),
)
def test_the_slider_is_the_first_and_the_last_step_a_buy_passes_at(
    step_cr: int, min_trade_micro: int, stake_left_micro: int, balance_micro: int
) -> None:
    """Whatever the step, the smallest trade, the stake left and the balance: counted
    out one step at a time, the slider runs from the first multiple of the step that
    is a trade, within the stake and covered by the balance, to the last one, and
    there is none exactly when there is no such multiple."""
    step_micro = step_cr * CR
    passing = [
        amount_micro
        for amount_micro in range(step_micro, 120 * CR + 1, step_micro)
        if min_trade_micro <= amount_micro <= stake_left_micro and amount_micro <= balance_micro
    ]

    found = slider(
        stake_left_micro, balance_micro, step_cr=step_cr, min_trade_micro=min_trade_micro
    )

    if not passing:
        assert found is None
    else:
        assert found == (passing[0] // CR, passing[-1] // CR, step_cr)


def test_a_stake_preset_on_a_step_of_the_slider_is_allowed_exactly_within_it(
    view: MarketView, base_settings: Settings
) -> None:
    """The buttons and the slider are one rule: a preset that is a multiple of the step
    is allowed exactly when the slider reaches it."""
    pricing = base_settings.pricing
    step = pricing.stake_step_micro
    limit = pricing.max_stake_per_user_market_micro
    assert all(amount % step == 0 for amount in pricing.stake_presets_micro)

    for stake_left in (0, CR - 1, min(pricing.stake_presets_micro), limit // 2 + 1, limit):
        for balance in (0, CR, limit // 3, 10 * limit):
            found = view.buy_slider(stake_left, balance, can_trade=True)
            for preset in view.stake_presets(stake_left, balance, can_trade=True):
                amount = to_micro(preset.amount_cr)
                within = found is not None and found.min <= amount // CR <= found.max
                assert preset.allowed == within, (stake_left, balance, amount)


# --- Whether any amount is a buy (A.11) ----------------------------------------------


def test_nothing_can_be_bought_once_the_stake_limit_is_reached(
    view: MarketView, base_settings: Settings
) -> None:
    """At the limit no amount passes, whatever the balance, and what a sale gives back
    can be bought again from the smallest trade on."""
    pricing = base_settings.pricing
    limit, smallest = pricing.max_stake_per_user_market_micro, pricing.min_trade_micro
    plenty = 10 * limit

    assert view.can_buy(view.stake_left_micro(0), plenty, can_trade=True)
    assert not view.can_buy(view.stake_left_micro(limit), plenty, can_trade=True)
    assert not view.can_buy(smallest - 1, plenty, can_trade=True)
    assert view.can_buy(smallest, plenty, can_trade=True)


def test_nothing_can_be_bought_with_a_balance_below_the_smallest_trade(
    view: MarketView, base_settings: Settings
) -> None:
    pricing = base_settings.pricing
    limit, smallest = pricing.max_stake_per_user_market_micro, pricing.min_trade_micro

    assert not view.can_buy(limit, smallest - 1, can_trade=True)
    assert view.can_buy(limit, smallest, can_trade=True)


def test_nothing_can_be_bought_by_someone_who_may_not_trade(
    view: MarketView, base_settings: Settings
) -> None:
    plenty = 10 * base_settings.pricing.max_stake_per_user_market_micro

    assert view.can_buy(plenty, plenty, can_trade=True)
    assert not view.can_buy(plenty, plenty, can_trade=False)


@given(
    step_cr=st.integers(min_value=1, max_value=25),
    min_trade_micro=st.integers(min_value=0, max_value=60 * CR),
    stake_left_micro=st.integers(min_value=0, max_value=120 * CR),
    balance_micro=st.integers(min_value=0, max_value=120 * CR),
)
def test_something_can_be_bought_wherever_there_is_a_slider_and_between_its_steps(
    step_cr: int, min_trade_micro: int, stake_left_micro: int, balance_micro: int
) -> None:
    """The smallest trade is the first amount that passes, so something can be bought
    exactly when the stake left and the balance cover it. A slider is a whole step of
    that, so there is none without it, and it can be missing where less than a step is
    left."""
    view = slider_view(step_cr=step_cr, min_trade_micro=min_trade_micro)

    found = view.can_buy(stake_left_micro, balance_micro, can_trade=True)

    assert found == (min_trade_micro <= stake_left_micro and min_trade_micro <= balance_micro)
    if view.buy_slider(stake_left_micro, balance_micro, can_trade=True) is not None:
        assert found


# --- What a viewer can buy, in shares (A.11) -----------------------------------------

PLENTY: Final[int] = 10 * STAKE_LIMIT
"""A balance, or a stake left, that decides nothing."""


@cache
def share_settings(
    *, step: int = 1, min_trade_shares_micro: int = CR, shortcuts: tuple[int, ...] | None = None
) -> Settings:
    """The test settings with these in place of `TRADE_SHARE_STEP`, `MIN_TRADE_SHARES`
    and, in whole shares, `TRADE_SHARE_PRESETS`."""
    settings = load_base_settings()
    update: dict[str, Any] = {
        "share_step_micro": step * CR,
        "min_trade_shares_micro": min_trade_shares_micro,
    }
    if shortcuts is not None:
        update["share_presets_micro"] = [count * CR for count in shortcuts]
    return settings.model_copy(update={"pricing": settings.pricing.model_copy(update=update)})


def shares_view(
    *, step: int = 1, min_trade_shares_micro: int = CR, shortcuts: tuple[int, ...] | None = None
) -> MarketView:
    """A `MarketView` on `share_settings(...)`."""
    return MarketView(
        share_settings(
            step=step, min_trade_shares_micro=min_trade_shares_micro, shortcuts=shortcuts
        )
    )


def no_database() -> UnitOfWork:
    raise AssertionError("pricing a size and the two checks of a buy read no row")


def quotes_on(settings: Settings) -> QuoteService:
    """A `QuoteService` for its two rules that read no row: `price` and `check_buy`."""
    return QuoteService(no_database, settings, FrozenClock(), MarketView(settings))


def buy_of(outcome: Outcome, shares: int) -> TradeSize:
    """A buy of whole `shares`, as the trade sheet sends a position of the slider."""
    return TradeSize(side=TradeSide.BUY, outcome=outcome, shares=Decimal(shares))


def state_of(traded: Market) -> lmsr.MarketState:
    return lmsr.MarketState(q_yes=traded.q_yes_micro, q_no=traded.q_no_micro, b=traded.b_micro)


def most_for(traded: Market, outcome: Outcome, budget_micro: int) -> int:
    """The µshares of `outcome` the pricing module gives for `budget_micro` in `traded`."""
    quote = lmsr.quote_buy_for_budget(state_of(traded), outcome, budget_micro, precision=PRECISION)
    return quote.shares_micro


def cost_of(traded: Market, outcome: Outcome, shares_micro: int) -> int:
    """What the pricing module asks for `shares_micro` of `outcome` in `traded`."""
    quote = lmsr.quote_buy_shares(state_of(traded), outcome, shares_micro, precision=PRECISION)
    return quote.amount_micro


def proceeds_of(traded: Market, outcome: Outcome, shares_micro: int) -> int:
    """What the pricing module pays for `shares_micro` of `outcome` sold in `traded`."""
    quote = lmsr.quote_sell_shares(state_of(traded), outcome, shares_micro, precision=PRECISION)
    return quote.amount_micro


def range_of(entry: BuyEntry) -> tuple[int, int, int] | None:
    """`(min, max, step)` of the slider of `entry`, None for none."""
    found = entry.slider
    return None if found is None else (found.min, found.max, found.step)


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """How often each quote function of the pricing module is called from here on.
    The functions run as they are."""
    calls: Counter[str] = Counter()

    def counted(name: str) -> Callable[..., lmsr.TradeQuote]:
        function: Callable[..., lmsr.TradeQuote] = getattr(lmsr, name)

        def call(*args: Any, **kwargs: Any) -> lmsr.TradeQuote:
            calls[name] += 1
            return function(*args, **kwargs)

        return call

    for name in ("quote_buy_for_budget", "quote_buy_shares", "quote_sell_shares"):
        monkeypatch.setattr(lmsr, name, counted(name))
    return calls


def test_a_viewer_gets_a_buy_entry_for_each_outcome_yes_then_no(
    view: MarketView, base_settings: Settings
) -> None:
    shortcuts = base_settings.pricing.share_presets_micro

    entries = view.buy_entries(market(), STAKE_LIMIT, PLENTY, can_trade=True)

    assert [entry.outcome for entry in entries] == [Outcome.YES, Outcome.NO]
    for entry in entries:
        assert [to_micro(preset.shares) for preset in entry.presets] == list(shortcuts), (
            "one shortcut per TRADE_SHARE_PRESETS, in that order"
        )


@pytest.mark.parametrize("b_micro", sorted(TIER_B.values()))
def test_in_a_fresh_market_the_slider_ends_at_the_whole_shares_the_stake_limit_buys(
    view: MarketView, base_settings: Settings, b_micro: int
) -> None:
    """With the tokens and nothing staked yet: from the smallest trade to the most whole
    shares `quote_buy_for_budget` gives for MAX_STAKE_PER_USER_MARKET, in steps of
    TRADE_SHARE_STEP."""
    pricing = base_settings.pricing
    assert (pricing.share_step_micro, pricing.min_trade_shares_micro) == (CR, CR)
    fresh = market(b_micro=b_micro, budget_micro=lmsr.max_loss_micro(b_micro))
    most = most_for(fresh, Outcome.YES, STAKE_LIMIT)

    yes, no = view.buy_entries(fresh, view.stake_left_micro(0), PLENTY, can_trade=True)

    found = yes.slider
    assert found == BuySlider(min=1, max=most // CR, step=1)
    assert found is not None
    assert no.slider == found, "at even odds both outcomes cost the same"
    assert found.max > STAKE_LIMIT // CR, "a share costs less than a credit"
    assert found.model_dump(mode="json") == {"min": 1, "max": most // CR, "step": 1}, (
        "whole shares as JSON integers, which is what a range input takes"
    )


def test_the_lesser_of_the_stake_left_and_the_balance_sets_the_range(view: MarketView) -> None:
    traded = market(q_yes_micro=TRADED_YES)
    less, more = 40 * CR, 70 * CR
    expected = [
        (1, most_for(traded, outcome, less) // CR, 1) for outcome in (Outcome.YES, Outcome.NO)
    ]

    for stake_left_micro, balance_micro in ((less, more), (more, less), (less, less)):
        entries = view.buy_entries(traded, stake_left_micro, balance_micro, can_trade=True)
        assert [range_of(entry) for entry in entries] == expected
    wider = view.buy_entries(traded, more, more, can_trade=True)
    assert [range_of(entry) for entry in wider] != expected


def test_each_outcome_has_its_own_range_and_the_one_that_leads_ends_first(
    view: MarketView,
) -> None:
    """Where YES costs more than NO, the same tokens buy fewer YES shares."""
    traded = market(q_yes_micro=TRADED_YES)

    yes, no = view.buy_entries(traded, STAKE_LIMIT, PLENTY, can_trade=True)

    assert range_of(yes) == (1, most_for(traded, Outcome.YES, STAKE_LIMIT) // CR, 1)
    assert range_of(no) == (1, most_for(traded, Outcome.NO, STAKE_LIMIT) // CR, 1)
    assert yes.slider is not None
    assert no.slider is not None
    assert yes.slider.max < no.slider.max


def test_the_slider_of_shares_starts_at_the_smallest_multiple_of_the_step_that_is_a_trade() -> None:
    def starts_at(step: int, min_trade_shares_micro: int) -> int:
        chosen = shares_view(step=step, min_trade_shares_micro=min_trade_shares_micro)
        found = chosen.buy_entries(market(), STAKE_LIMIT, PLENTY, can_trade=True)[0].slider
        assert found is not None
        assert found.step == step
        return found.min

    assert starts_at(1, CR) == 1
    assert starts_at(1, CR // 4) == 1, (
        "a quarter of a share is a trade; the slider counts whole ones"
    )
    assert starts_at(2, 2 * CR + CR // 2) == 4, "MIN_TRADE_SHARES of 2.5 in steps of 2"
    assert starts_at(5, CR) == 5
    assert starts_at(5, 5 * CR) == 5
    assert starts_at(5, 5 * CR + 1) == 10
    # No smallest trade at all: a buy is still of more than nothing.
    assert starts_at(1, 0) == 1
    assert starts_at(5, 0) == 5


def test_shares_beyond_a_whole_step_do_not_count() -> None:
    """In steps of five shares the slider ends at the last multiple of five the budget
    pays for."""
    traded = market(q_yes_micro=TRADED_YES)
    entries = shares_view(step=5).buy_entries(traded, STAKE_LIMIT, PLENTY, can_trade=True)

    for entry in entries:
        most = most_for(traded, entry.outcome, STAKE_LIMIT)
        found = entry.slider
        assert found is not None
        assert (found.min, found.step) == (5, 5)
        assert found.max % 5 == 0
        assert found.max * CR <= most < (found.max + 5) * CR


def test_no_slider_where_the_budget_does_not_cover_the_smallest_buy(
    view: MarketView, base_settings: Settings
) -> None:
    """One micro-credit short of what the smallest buy costs, in the account or left of
    the stake; and exactly that."""
    fresh = market()
    cost = cost_of(fresh, Outcome.YES, base_settings.pricing.min_trade_shares_micro)

    short_of_tokens = view.buy_entries(fresh, STAKE_LIMIT, cost - 1, can_trade=True)
    short_of_stake = view.buy_entries(fresh, cost - 1, PLENTY, can_trade=True)
    exact = view.buy_entries(fresh, STAKE_LIMIT, cost, can_trade=True)

    assert [range_of(entry) for entry in short_of_tokens] == [None, None]
    assert [range_of(entry) for entry in short_of_stake] == [None, None]
    assert [range_of(entry) for entry in exact] == [(1, 1, 1), (1, 1, 1)], (
        "exactly the smallest buy is a slider of one position"
    )


def test_someone_who_may_not_trade_gets_no_slider_and_no_shortcut_and_nothing_is_priced(
    view: MarketView, base_settings: Settings, priced: Counter[str]
) -> None:
    """A market that was never opened has no b, which the pricing module refuses: it is
    not asked."""
    proposal = market(status=MarketStatus.PROPOSED, tier=None, b_micro=0, budget_micro=0)

    for subject in (proposal, market(q_yes_micro=TRADED_YES)):
        entries = view.buy_entries(subject, PLENTY, PLENTY, can_trade=False)

        assert [entry.outcome for entry in entries] == [Outcome.YES, Outcome.NO]
        assert [range_of(entry) for entry in entries] == [None, None]
        for entry in entries:
            assert len(entry.presets) == len(base_settings.pricing.share_presets_micro)
            assert not any(preset.allowed for preset in entry.presets)
    assert sum(priced.values()) == 0


@pytest.mark.parametrize(("stake_left_micro", "balance_micro"), [(0, PLENTY), (PLENTY, 0), (0, 0)])
def test_nothing_left_to_put_in_or_nothing_in_the_account_is_no_range_and_nothing_is_priced(
    view: MarketView, priced: Counter[str], stake_left_micro: int, balance_micro: int
) -> None:
    entries = view.buy_entries(market(), stake_left_micro, balance_micro, can_trade=True)

    assert [range_of(entry) for entry in entries] == [None, None]
    assert not any(preset.allowed for entry in entries for preset in entry.presets)
    assert sum(priced.values()) == 0


def test_a_shortcut_is_allowed_exactly_when_the_budget_pays_for_its_shares(
    view: MarketView, base_settings: Settings
) -> None:
    """At the edges: a budget of what a shortcut costs, and one micro-credit less."""
    traded = market(q_yes_micro=TRADED_YES)
    shortcuts = base_settings.pricing.share_presets_micro

    for place, outcome in enumerate((Outcome.YES, Outcome.NO)):
        for shares_micro in shortcuts:
            cost = cost_of(traded, outcome, shares_micro)
            for budget_micro in (cost - 1, cost):
                entry = view.buy_entries(traded, PLENTY, budget_micro, can_trade=True)[place]
                most = most_for(traded, outcome, budget_micro)
                allowed = dict(
                    zip(shortcuts, (preset.allowed for preset in entry.presets), strict=True)
                )
                assert allowed == {size: size <= most for size in shortcuts}
                assert allowed[shares_micro] == (budget_micro == cost)


def test_a_shortcut_below_the_minimum_trade_is_not_allowed() -> None:
    """With MIN_TRADE_SHARES at 50, the shortcut of 10 shares is no trade."""
    chosen = shares_view(min_trade_shares_micro=50 * CR, shortcuts=(10, 50, 100))

    entries = chosen.buy_entries(market(), STAKE_LIMIT, PLENTY, can_trade=True)

    for entry in entries:
        assert [preset.allowed for preset in entry.presets] == [False, True, True]


@given(
    q_yes=st.integers(min_value=0, max_value=4 * DEFAULT_B),
    q_no=st.integers(min_value=0, max_value=4 * DEFAULT_B),
    paid_micro=st.integers(min_value=0, max_value=STAKE_LIMIT),
    back_micro=st.integers(min_value=0, max_value=STAKE_LIMIT),
    balance_micro=st.integers(min_value=0, max_value=2 * STAKE_LIMIT),
    step=st.integers(min_value=1, max_value=7),
    where=st.floats(min_value=0, max_value=1),
)
@hypothesis_settings(max_examples=200, deadline=None)
def test_the_slider_is_where_a_buy_passes_and_the_step_beyond_it_is_refused(
    q_yes: int,
    q_no: int,
    paid_micro: int,
    back_micro: int,
    balance_micro: int,
    step: int,
    where: float,
) -> None:
    """For somebody whose buys cost at least what their sells brought in, whatever the
    prices, the stake, the balance and the step: the first position of each slider,
    the last one and one between them pass `QuoteService.price` and `check_buy`, the
    two rules of a trade that size a buy. One step beyond the last is refused, on the
    stake limit when it costs more than the stake left and on the balance otherwise.
    Where there is no slider, the smallest step is refused the same way."""
    settings = share_settings(step=step)
    view, quotes = MarketView(settings), quotes_on(settings)
    traded = market(q_yes_micro=q_yes, q_no_micro=q_no)
    positions = [
        position(
            traded.id,
            Outcome.YES,
            cost_micro=paid_micro,
            proceeds_micro=min(back_micro, paid_micro),
        )
    ]
    stake_left_micro = view.stake_left_micro(view.net_stake_micro(positions))

    for entry in view.buy_entries(traded, stake_left_micro, balance_micro, can_trade=True):
        found = entry.slider
        beyond = step
        if found is not None:
            steps = (found.max - found.min) // found.step
            between = found.min + int(steps * where) * found.step
            for shares in {found.min, between, found.max}:
                quote = quotes.price(traded, buy_of(entry.outcome, shares))
                quotes.check_buy(positions, balance_micro, quote)
            beyond = found.max + found.step

        quote = quotes.price(traded, buy_of(entry.outcome, beyond))
        refusal = (
            LimitExceededError
            if quote.amount_micro > stake_left_micro
            else InsufficientBalanceError
        )
        with pytest.raises(refusal):
            quotes.check_buy(positions, balance_micro, quote)


@given(
    q_yes=st.integers(min_value=0, max_value=4 * DEFAULT_B),
    q_no=st.integers(min_value=0, max_value=4 * DEFAULT_B),
    paid_micro=st.integers(min_value=0, max_value=STAKE_LIMIT),
    profit_micro=st.integers(min_value=1, max_value=STAKE_LIMIT),
    balance_micro=st.integers(min_value=0, max_value=2 * STAKE_LIMIT),
    step=st.integers(min_value=1, max_value=7),
    where=st.floats(min_value=0, max_value=1),
)
@hypothesis_settings(max_examples=200, deadline=None)
def test_somebody_whose_sells_brought_in_more_than_their_buys_cost_is_granted_the_slider_too(
    q_yes: int,
    q_no: int,
    paid_micro: int,
    profit_micro: int,
    balance_micro: int,
    step: int,
    where: float,
) -> None:
    """The range is the one of the stake left, which is never below zero and so never
    more than the trade allows (A.4): every position of it passes."""
    settings = share_settings(step=step)
    view, quotes = MarketView(settings), quotes_on(settings)
    traded = market(q_yes_micro=q_yes, q_no_micro=q_no)
    positions = [
        position(
            traded.id,
            Outcome.NO,
            cost_micro=paid_micro,
            proceeds_micro=paid_micro + profit_micro,
        )
    ]
    stake_left_micro = view.stake_left_micro(view.net_stake_micro(positions))
    assert stake_left_micro == STAKE_LIMIT

    for entry in view.buy_entries(traded, stake_left_micro, balance_micro, can_trade=True):
        found = entry.slider
        if found is None:
            continue
        steps = (found.max - found.min) // found.step
        between = found.min + int(steps * where) * found.step
        for shares in {found.min, between, found.max}:
            quote = quotes.price(traded, buy_of(entry.outcome, shares))
            quotes.check_buy(positions, balance_micro, quote)


@given(
    q_yes=st.integers(min_value=0, max_value=6 * DEFAULT_B),
    q_no=st.integers(min_value=0, max_value=6 * DEFAULT_B),
    outcome=st.sampled_from(Outcome),
    budget_micro=st.integers(min_value=1, max_value=2 * STAKE_LIMIT),
)
@hypothesis_settings(max_examples=300, deadline=None)
def test_the_most_shares_of_a_budget_cost_at_most_the_budget_and_one_more_microshare_costs_more(
    q_yes: int, q_no: int, outcome: Outcome, budget_micro: int
) -> None:
    """What the ranges rest on: `most_shares_micro` is the largest size whose cost,
    rounded up, the budget covers."""
    traded = market(q_yes_micro=q_yes, q_no_micro=q_no)

    most = shares_view().most_shares_micro(traded, outcome, budget_micro)

    assert cost_of(traded, outcome, most) <= budget_micro < cost_of(traded, outcome, most + 1)


def test_the_most_shares_of_no_budget_are_none(view: MarketView, priced: Counter[str]) -> None:
    assert view.most_shares_micro(market(), Outcome.YES, 0) == 0
    assert sum(priced.values()) == 0


def test_a_read_prices_one_budget_per_outcome_and_one_sale_per_sell_shortcut(
    view: MarketView, priced: Counter[str]
) -> None:
    """Never a quote per step or per shortcut of a buy: the page of a market is read
    often."""
    traded = market(q_yes_micro=TRADED_YES, q_no_micro=40 * CR)
    held = [
        position(traded.id, Outcome.YES, shares_micro=30 * CR),
        position(traded.id, Outcome.NO, shares_micro=20 * CR),
    ]

    view.buy_entries(traded, STAKE_LIMIT, PLENTY, can_trade=True)
    assert priced == {"quote_buy_for_budget": 2}

    view.sell_presets(traded, held, can_trade=True)
    assert priced == {"quote_buy_for_budget": 2, "quote_sell_shares": 4}


def test_a_budget_quote_that_fails_is_no_range_for_that_outcome_and_one_error_line(
    view: MarketView, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The rounding guard of `quote_buy_for_budget` is an `AssertionError`. Where it
    fails for the budget of one viewer, that viewer still reads the market: the outcome
    has no range, the other one has its own, and the log says so once. The quote of
    that budget fails as loudly as it does."""
    traded = market(q_yes_micro=TRADED_YES)
    expected_no = range_of(view.buy_entries(traded, STAKE_LIMIT, PLENTY, can_trade=True)[1])
    as_it_is = lmsr.quote_buy_for_budget

    def failing_for_yes(
        state: lmsr.MarketState, outcome: Outcome, budget_micro: int, *, precision: int
    ) -> lmsr.TradeQuote:
        if outcome is Outcome.YES:
            raise AssertionError("3 µshares cost 2 µCR, above the budget of 1 µCR")
        return as_it_is(state, outcome, budget_micro, precision=precision)

    monkeypatch.setattr(lmsr, "quote_buy_for_budget", failing_for_yes)
    with caplog.at_level(logging.ERROR, logger="app.services.market_view"):
        yes, no = view.buy_entries(traded, STAKE_LIMIT, PLENTY, can_trade=True)

    assert yes.slider is None
    assert not any(preset.allowed for preset in yes.presets)
    assert expected_no is not None
    assert range_of(no) == expected_no
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    line = errors[0].getMessage()
    assert str(traded.id) in line
    assert "YES" in line
    assert "above the budget of 1 µCR" in line
    with pytest.raises(AssertionError):
        view.most_shares_micro(traded, Outcome.YES, STAKE_LIMIT)
    budget = TradeSize(side=TradeSide.BUY, outcome=Outcome.YES, amount_cr=to_cr(STAKE_LIMIT))
    with pytest.raises(AssertionError):
        quotes_on(load_base_settings()).price(traded, budget)


# --- Sell presets (A.11) -----------------------------------------------------------


def test_sell_presets_are_half_and_all_of_each_holding(view: MarketView) -> None:
    """SPEC §12 sells "half": 190.902829 shares cannot be halved to six places by the
    client, so the server says how many shares half is."""
    traded = market(q_yes_micro=ODD_HOLDING)
    held = [position(traded.id, Outcome.YES, shares_micro=ODD_HOLDING, cost_micro=100 * CR)]

    presets = view.sell_presets(traded, held, can_trade=True)

    assert [(p.outcome, p.portion) for p in presets] == [
        (Outcome.YES, SellPortion.HALF),
        (Outcome.YES, SellPortion.ALL),
        (Outcome.NO, SellPortion.HALF),
        (Outcome.NO, SellPortion.ALL),
    ]
    assert [p.model_dump(mode="json")["shares"] for p in presets] == [
        "95.451414",
        "190.902829",
        "0",
        "0",
    ]
    assert [p.allowed for p in presets] == [True, True, False, False]


def test_a_sell_shortcut_says_what_it_brings_in(view: MarketView) -> None:
    """What `quote_sell_shares` pays for its size on the quantities of the market: the
    client prices nothing."""
    traded = market(q_yes_micro=ODD_HOLDING, q_no_micro=60 * CR)
    held = [
        position(traded.id, Outcome.YES, shares_micro=ODD_HOLDING),
        position(traded.id, Outcome.NO, shares_micro=25 * CR),
    ]

    presets = view.sell_presets(traded, held, can_trade=True)

    assert [to_micro(p.proceeds_cr) for p in presets] == [
        proceeds_of(traded, Outcome.YES, ODD_HOLDING // 2),
        proceeds_of(traded, Outcome.YES, ODD_HOLDING),
        proceeds_of(traded, Outcome.NO, 25 * CR // 2),
        proceeds_of(traded, Outcome.NO, 25 * CR),
    ]
    assert all(p.proceeds_cr > 0 for p in presets)
    half, everything = presets[0], presets[1]
    assert half.proceeds_cr < everything.proceeds_cr < 2 * half.proceeds_cr, (
        "the second half sells at a lower price than the first"
    )
    assert half.model_dump(mode="json")["proceeds_cr"] == format_cr(
        proceeds_of(traded, Outcome.YES, ODD_HOLDING // 2)
    )


def test_a_sell_shortcut_of_no_shares_brings_in_nothing_and_is_not_priced(
    view: MarketView, priced: Counter[str]
) -> None:
    """Nothing is held of NO, and half of one micro-share of YES is nothing."""
    traded = market(q_yes_micro=TRADED_YES)
    held = [position(traded.id, Outcome.YES, shares_micro=1)]

    presets = view.sell_presets(traded, held, can_trade=True)

    assert [to_micro(p.shares) for p in presets] == [0, 1, 0, 0]
    assert [to_micro(p.proceeds_cr) for p in presets] == [
        0,
        proceeds_of(traded, Outcome.YES, 1),
        0,
        0,
    ]
    assert priced["quote_sell_shares"] == 2, "the one above and the one of the shortcut"
    assert not any(p.allowed for p in presets)


def test_a_sell_preset_below_the_minimum_trade_size_is_not_allowed(
    view: MarketView, base_settings: Settings
) -> None:
    """Half of one and a half times `MIN_TRADE_SHARES` is too small, all of it is not.
    The half that is too small still says what it is worth."""
    minimum = base_settings.pricing.min_trade_shares_micro
    one_and_a_half = minimum + minimum // 2
    traded = market(q_no_micro=one_and_a_half)
    held = [position(traded.id, Outcome.NO, shares_micro=one_and_a_half)]

    presets = {(p.outcome, p.portion): p for p in view.sell_presets(traded, held, can_trade=True)}

    half = presets[Outcome.NO, SellPortion.HALF]
    assert half.shares == to_cr(one_and_a_half // 2)
    assert not half.allowed
    assert to_micro(half.proceeds_cr) == proceeds_of(traded, Outcome.NO, one_and_a_half // 2)
    assert half.proceeds_cr > 0
    assert presets[Outcome.NO, SellPortion.ALL].allowed
    exactly_the_minimum = [position(traded.id, Outcome.NO, shares_micro=minimum)]
    assert view.sell_presets(traded, exactly_the_minimum, can_trade=True)[3].allowed
    just_below = [position(traded.id, Outcome.NO, shares_micro=minimum - 1)]
    assert not view.sell_presets(traded, just_below, can_trade=True)[3].allowed


def test_no_sell_preset_is_allowed_to_someone_who_may_not_trade(view: MarketView) -> None:
    traded = market(q_yes_micro=2 * CR, q_no_micro=CR)
    held = [
        position(traded.id, Outcome.YES, shares_micro=2 * CR),
        position(traded.id, Outcome.NO, shares_micro=CR),
    ]

    presets = view.sell_presets(traded, held, can_trade=False)

    assert [p.shares for p in presets] == [to_cr(CR), to_cr(2 * CR), to_cr(CR // 2), to_cr(CR)]
    assert not any(p.allowed for p in presets)


def test_no_sell_shortcut_is_priced_for_someone_who_may_not_trade(
    view: MarketView, priced: Counter[str]
) -> None:
    """A settled market among them, and a row without a b, which the pricing module
    refuses: neither is handed to it."""
    settled = market(status=MarketStatus.RESOLVED, q_yes_micro=2 * CR, q_no_micro=CR)
    without_b = market(status=MarketStatus.PROPOSED, tier=None, b_micro=0, budget_micro=0)

    for subject in (settled, without_b):
        held = [
            position(subject.id, Outcome.YES, shares_micro=2 * CR),
            position(subject.id, Outcome.NO, shares_micro=CR),
        ]
        presets = view.sell_presets(subject, held, can_trade=False)

        assert [to_micro(p.shares) for p in presets] == [CR, 2 * CR, CR // 2, CR]
        assert [to_micro(p.proceeds_cr) for p in presets] == [0, 0, 0, 0]
    assert sum(priced.values()) == 0


def test_a_holding_above_what_the_market_sold_is_refused_before_it_is_priced(
    view: MarketView, priced: Counter[str]
) -> None:
    """Nobody can hold more of an outcome than its market sold: such a row is a broken
    database, and the pricing module must not see the negative quantity it leaves."""
    traded = market(q_yes_micro=CR)
    held = [position(traded.id, Outcome.YES, shares_micro=2 * CR)]

    with pytest.raises(ValueError, match=str(traded.id)):
        view.sell_presets(traded, held, can_trade=True)

    assert sum(priced.values()) == 0


def test_selling_half_and_then_all_sells_everything(view: MarketView) -> None:
    """Half rounds down, so an odd number of micro-shares leaves nothing behind."""
    odd = ODD_HOLDING
    traded = market(q_yes_micro=odd)
    half = view.sell_presets(
        traded, [position(traded.id, Outcome.YES, shares_micro=odd)], can_trade=True
    )[0]
    rest = odd - 95_451_414

    then_all = view.sell_presets(
        traded, [position(traded.id, Outcome.YES, shares_micro=rest)], can_trade=True
    )[1]

    assert (half.shares, then_all.shares) == (to_cr(95_451_414), to_cr(95_451_415))


# --- The slider of a sell (A.11) -----------------------------------------------------


def sell_range(
    yes_micro: int,
    no_micro: int = 0,
    *,
    step: int = 1,
    min_trade_shares_micro: int = CR,
    can_trade: bool = True,
) -> list[tuple[int, int, int] | None]:
    """`(min, max, step)` of the slider of a sell per outcome, YES then NO, None for
    none, for somebody who holds these µshares."""
    traded = market(q_yes_micro=yes_micro, q_no_micro=no_micro)
    held = [
        position(traded.id, Outcome.YES, shares_micro=yes_micro),
        position(traded.id, Outcome.NO, shares_micro=no_micro),
    ]
    entries = shares_view(step=step, min_trade_shares_micro=min_trade_shares_micro).sell_entries(
        held, can_trade
    )
    assert [entry.outcome for entry in entries] == [Outcome.YES, Outcome.NO]
    return [
        None if entry.slider is None else (entry.slider.min, entry.slider.max, entry.slider.step)
        for entry in entries
    ]


def test_the_slider_of_a_sell_runs_over_the_whole_shares_of_each_holding(
    view: MarketView,
) -> None:
    traded = market(q_yes_micro=40 * CR, q_no_micro=25 * CR)
    held = [
        position(traded.id, Outcome.YES, shares_micro=40 * CR),
        position(traded.id, Outcome.NO, shares_micro=25 * CR),
    ]

    entries = view.sell_entries(held, can_trade=True)

    assert [(entry.outcome, entry.slider) for entry in entries] == [
        (Outcome.YES, SellSlider(min=1, max=40, step=1)),
        (Outcome.NO, SellSlider(min=1, max=25, step=1)),
    ]


def test_someone_without_positions_gets_two_entries_without_a_slider(view: MarketView) -> None:
    entries = view.sell_entries([], can_trade=True)

    assert [(entry.outcome, entry.slider) for entry in entries] == [
        (Outcome.YES, None),
        (Outcome.NO, None),
    ]


def test_shares_held_beyond_a_whole_step_are_not_on_the_slider_of_a_sell() -> None:
    assert sell_range(ODD_HOLDING) == [(1, ODD_HOLDING // CR, 1), None]
    assert sell_range(48 * CR + CR // 2, 11 * CR - 1) == [(1, 48, 1), (1, 10, 1)]
    assert sell_range(15 * CR - 1, 15 * CR, step=5) == [(5, 10, 5), (5, 15, 5)]


def test_a_holding_below_the_smallest_sale_leaves_no_slider() -> None:
    assert sell_range(CR - 1, CR) == [None, (1, 1, 1)]
    assert sell_range(5 * CR - 1, 5 * CR, step=5) == [None, (5, 5, 5)]
    assert sell_range(25 * CR - 1, 25 * CR, min_trade_shares_micro=25 * CR) == [None, (25, 25, 1)]
    assert sell_range(3 * CR, min_trade_shares_micro=CR // 4) == [(1, 3, 1), None], (
        "a quarter of a share is a trade; the slider counts whole ones"
    )


def test_there_is_no_slider_of_a_sell_for_someone_who_may_not_trade() -> None:
    assert sell_range(40 * CR, 25 * CR, can_trade=False) == [None, None]


@given(
    step=st.integers(min_value=1, max_value=7),
    min_trade_shares_micro=st.integers(min_value=1, max_value=12 * CR),
    held_micro=st.integers(min_value=0, max_value=60 * CR),
)
def test_the_slider_of_a_sell_is_the_first_and_the_last_step_a_sell_passes_at(
    step: int, min_trade_shares_micro: int, held_micro: int
) -> None:
    """Whatever the step, the smallest trade and the holding: counted out one step at
    a time, the slider runs from the first multiple of the step that is a trade and
    is held to the last one, and there is none exactly when there is no such
    multiple."""
    step_micro = step * CR
    passing = [
        shares_micro
        for shares_micro in range(step_micro, 60 * CR + 1, step_micro)
        if min_trade_shares_micro <= shares_micro <= held_micro
    ]

    found = sell_range(held_micro, step=step, min_trade_shares_micro=min_trade_shares_micro)[0]

    if not passing:
        assert found is None
    else:
        assert found == (passing[0] // CR, passing[-1] // CR, step)
