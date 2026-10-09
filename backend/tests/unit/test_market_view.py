"""`MarketView`: the numbers every screen shows of a market (SPEC §4, A.2, A.4)."""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

import pytest

from app.core.config import Settings
from app.core.enums import MarketStatus, MarketTier, Outcome, ResolutionOutcome
from app.core.units import MICRO, to_cr
from app.models import Market, Position, Resolution
from app.schemas.markets import SellPortion
from app.services.market_view import EVEN_ODDS_MICRO, MarketView
from tests.support.clock import TEST_NOW

CR = MICRO
DAY = timedelta(days=1)
USER_ID = uuid7()


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
        "budget_micro": 6_931_472,
        "b_micro": 10 * CR,
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
    """SPEC §4: about 1.91 YES shares at b = 10 move the price to about 54.8%."""
    traded = market(q_yes_micro=1_910_000)
    p_yes_micro = view.p_yes_micro(traded)
    assert 547_000 < p_yes_micro < 548_500
    p_yes, p_no = view.probabilities(p_yes_micro)
    assert (p_yes, p_no) == (0.548, 0.452)


def test_no_is_the_complement_of_the_rounded_yes(view: MarketView) -> None:
    for p_yes_micro in (0, 1, 333_333, 499_500, 547_499, 999_999, MICRO):
        p_yes, p_no = view.probabilities(p_yes_micro)
        assert round(p_yes + p_no, 9) == 1.0


def test_average_price_is_credits_per_share_at_display_precision(view: MarketView) -> None:
    assert view.average_price(amount_micro=1_000_000, shares_micro=1_910_000) == 0.524
    assert view.average_price(amount_micro=1, shares_micro=1) == 1.0
    with pytest.raises(ValueError, match="shares_micro"):
        view.average_price(amount_micro=1, shares_micro=0)


def test_summary_reports_the_effective_status_and_display_numbers(view: MarketView) -> None:
    traded = market(q_yes_micro=1_910_000, version=3, volume_micro=1_250_000)
    summary = view.summary(traded, TEST_NOW, None)
    assert summary.id == traded.id
    assert summary.status is MarketStatus.OPEN
    assert summary.trading_open is True
    assert (summary.p_yes, summary.p_no) == (0.548, 0.452)
    assert summary.volume_cr == to_cr(1_250_000)
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


def test_net_stake_is_cost_minus_proceeds_over_both_outcomes(view: MarketView) -> None:
    """SPEC §4: selling lowers the net stake, so a user can buy again up to the limit."""
    market_id = uuid7()
    positions = [
        position(market_id, Outcome.YES, cost_micro=800_000, proceeds_micro=300_000),
        position(market_id, Outcome.NO, cost_micro=200_000),
    ]
    assert view.net_stake_micro(positions) == 700_000
    assert view.stake_left_micro(700_000) == 300_000
    assert view.net_stake_micro([]) == 0
    assert view.stake_left_micro(0) == MICRO


def test_net_stake_is_floored_at_zero_and_stake_left_at_the_limit(view: MarketView) -> None:
    market_id = uuid7()
    in_profit = [position(market_id, Outcome.YES, cost_micro=400_000, proceeds_micro=900_000)]
    assert view.net_stake_micro(in_profit) == 0
    assert view.stake_left_micro(0) == MICRO
    assert view.stake_left_micro(MICRO) == 0
    assert view.stake_left_micro(MICRO + 1) == 0


def test_holdings_are_valued_at_the_current_price_rounded_down(view: MarketView) -> None:
    traded = market(q_yes_micro=1_910_000)
    p_yes_micro = view.p_yes_micro(traded)
    positions = [
        position(traded.id, Outcome.YES, shares_micro=1_910_000),
        position(traded.id, Outcome.NO, shares_micro=3),
    ]
    yes, no = view.holdings(traded, positions, None)
    assert (yes.outcome, no.outcome) == (Outcome.YES, Outcome.NO)
    assert yes.shares == to_cr(1_910_000)
    assert yes.value_cr == to_cr(1_910_000 * p_yes_micro // MICRO)
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


# --- Sell presets (A.11) -----------------------------------------------------------


def test_sell_presets_are_half_and_all_of_each_holding(view: MarketView) -> None:
    """SPEC §12 sells "half": 1.909029 shares cannot be halved to six places by the
    client, so the server says how many shares half is."""
    traded = market(q_yes_micro=1_909_029)
    held = [position(traded.id, Outcome.YES, shares_micro=1_909_029, cost_micro=CR)]

    presets = view.sell_presets(held, can_trade=True)

    assert [(p.outcome, p.portion) for p in presets] == [
        (Outcome.YES, SellPortion.HALF),
        (Outcome.YES, SellPortion.ALL),
        (Outcome.NO, SellPortion.HALF),
        (Outcome.NO, SellPortion.ALL),
    ]
    assert [p.model_dump(mode="json")["shares"] for p in presets] == [
        "0.954514",
        "1.909029",
        "0",
        "0",
    ]
    assert [p.allowed for p in presets] == [True, True, False, False]


def test_a_sell_preset_below_the_minimum_trade_size_is_not_allowed(view: MarketView) -> None:
    """`MIN_TRADE_SHARES` is 0.01: half of 0.015 shares is too small, all of it is not."""
    traded = market(q_no_micro=15_000)
    held = [position(traded.id, Outcome.NO, shares_micro=15_000)]

    presets = {(p.outcome, p.portion): p for p in view.sell_presets(held, can_trade=True)}

    assert presets[Outcome.NO, SellPortion.HALF].shares == to_cr(7_500)
    assert not presets[Outcome.NO, SellPortion.HALF].allowed
    assert presets[Outcome.NO, SellPortion.ALL].allowed
    exactly_the_minimum = [position(traded.id, Outcome.NO, shares_micro=10_000)]
    assert view.sell_presets(exactly_the_minimum, can_trade=True)[3].allowed


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
    odd = 1_909_029
    half = view.sell_presets([position(uuid7(), Outcome.YES, shares_micro=odd)], True)[0]
    rest = odd - 954_514

    then_all = view.sell_presets([position(uuid7(), Outcome.YES, shares_micro=rest)], True)[1]

    assert (half.shares, then_all.shares) == (to_cr(954_514), to_cr(954_515))
