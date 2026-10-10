"""`MarketView`: the numbers every screen shows of a market (SPEC §4, A.2, A.4).

The market of these tests is one of the DEFAULT tier of the test settings, and the
limits a result depends on (the stake limit, the stake presets, the smallest trade) are
read from them.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import pytest

from app.core.config import Settings
from app.core.enums import MarketStatus, MarketTier, Outcome, ResolutionOutcome
from app.core.units import MICRO, format_cr, to_cr, to_micro
from app.models import Market, Position, Resolution
from app.pricing import lmsr
from app.schemas.markets import SellPortion
from app.services.market_view import EVEN_ODDS_MICRO, MarketView
from tests.support.clock import TEST_NOW
from tests.support.economy import DEFAULT_B

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
        "requested_tier": MarketTier.DEFAULT,
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


@pytest.mark.parametrize(
    "status", [status for status in MarketStatus if status is not MarketStatus.OPEN]
)
def test_only_an_open_market_changes_status_with_time(
    view: MarketView, status: MarketStatus
) -> None:
    stored = market(status=status)
    for at in (TEST_NOW, stored.close_at + DAY):
        assert view.effective_status(stored, at) is status
        assert not view.is_trading_open(stored, at)


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
