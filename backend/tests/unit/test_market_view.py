"""`MarketView`: the numbers every screen shows of a market (SPEC §4, A.2, A.4).

The market of these tests is one of the DEFAULT tier of the test settings, and the
limits a result depends on (the stake limit, the stake presets, the smallest trade) are
read from them. The slider of a buy is a rule about three settings and two amounts, so
its tests name their own: a step, a smallest trade, a stake left and a balance.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.config import Settings
from app.core.enums import (
    STORED_MARKET_STATUSES,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
)
from app.core.units import MICRO, format_cr, to_cr, to_micro
from app.models import Market, Position, Resolution
from app.pricing import lmsr
from app.schemas.markets import BuySlider, SellPortion
from app.services.market_view import EVEN_ODDS_MICRO, MarketView
from tests.support.clock import TEST_NOW
from tests.support.economy import DEFAULT_B
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


# --- Sell presets (A.11) -----------------------------------------------------------


def test_sell_presets_are_half_and_all_of_each_holding(view: MarketView) -> None:
    """SPEC §12 sells "half": 190.902829 shares cannot be halved to six places by the
    client, so the server says how many shares half is."""
    traded = market(q_yes_micro=ODD_HOLDING)
    held = [position(traded.id, Outcome.YES, shares_micro=ODD_HOLDING, cost_micro=100 * CR)]

    presets = view.sell_presets(held, can_trade=True)

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


def test_a_sell_preset_below_the_minimum_trade_size_is_not_allowed(
    view: MarketView, base_settings: Settings
) -> None:
    """Half of one and a half times `MIN_TRADE_SHARES` is too small, all of it is not."""
    minimum = base_settings.pricing.min_trade_shares_micro
    one_and_a_half = minimum + minimum // 2
    traded = market(q_no_micro=one_and_a_half)
    held = [position(traded.id, Outcome.NO, shares_micro=one_and_a_half)]

    presets = {(p.outcome, p.portion): p for p in view.sell_presets(held, can_trade=True)}

    assert presets[Outcome.NO, SellPortion.HALF].shares == to_cr(one_and_a_half // 2)
    assert not presets[Outcome.NO, SellPortion.HALF].allowed
    assert presets[Outcome.NO, SellPortion.ALL].allowed
    exactly_the_minimum = [position(traded.id, Outcome.NO, shares_micro=minimum)]
    assert view.sell_presets(exactly_the_minimum, can_trade=True)[3].allowed
    just_below = [position(traded.id, Outcome.NO, shares_micro=minimum - 1)]
    assert not view.sell_presets(just_below, can_trade=True)[3].allowed


def test_no_sell_preset_is_allowed_to_someone_who_may_not_trade(view: MarketView) -> None:
    traded = market(q_yes_micro=2 * CR, q_no_micro=CR)
    held = [
        position(traded.id, Outcome.YES, shares_micro=2 * CR),
        position(traded.id, Outcome.NO, shares_micro=CR),
    ]

    presets = view.sell_presets(held, can_trade=False)

    assert [p.shares for p in presets] == [to_cr(CR), to_cr(2 * CR), to_cr(CR // 2), to_cr(CR)]
    assert not any(p.allowed for p in presets)


def test_selling_half_and_then_all_sells_everything(view: MarketView) -> None:
    """Half rounds down, so an odd number of micro-shares leaves nothing behind."""
    odd = ODD_HOLDING
    half = view.sell_presets([position(uuid7(), Outcome.YES, shares_micro=odd)], True)[0]
    rest = odd - 95_451_414

    then_all = view.sell_presets([position(uuid7(), Outcome.YES, shares_micro=rest)], True)[1]

    assert (half.shares, then_all.shares) == (to_cr(95_451_414), to_cr(95_451_415))
