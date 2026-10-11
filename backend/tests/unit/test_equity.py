"""Equity replay keeps exact token values and samples atomic historical changes."""

from collections.abc import AsyncIterator, Iterable
from datetime import timedelta
from uuid import UUID, uuid7

from app.core.enums import Outcome
from app.core.units import MICRO, to_cr
from app.repositories.equity import EquityEvent
from app.services.equity import equity_history
from tests.support.clock import TEST_NOW

MARKET = uuid7()


def event(
    hour: int,
    *,
    balance: int = 0,
    market: UUID | None = None,
    outcome: Outcome | None = None,
    shares: int = 0,
    price: int | None = None,
    settled: bool = False,
) -> EquityEvent:
    return EquityEvent(
        TEST_NOW + timedelta(hours=hour), balance, market, outcome, shares, price, settled
    )


async def stream(events: Iterable[EquityEvent]) -> AsyncIterator[EquityEvent]:
    for item in events:
        yield item


async def test_buys_sells_and_other_traders_revalue_both_outcomes_exactly() -> None:
    points = await equity_history(
        stream(
            [
                event(0, balance=100 * MICRO),
                event(1, market=MARKET, outcome=Outcome.YES, shares=3 * MICRO + 1, price=500_001),
                event(1, balance=-2 * MICRO),
                event(2, market=MARKET, outcome=Outcome.NO, shares=2 * MICRO + 1, price=500_001),
                event(2, balance=-MICRO),
                event(3, market=MARKET, price=600_001),
                event(4, market=MARKET, outcome=Outcome.YES, shares=-MICRO, price=550_001),
                event(4, balance=550_000),
            ]
        ),
        start=TEST_NOW,
        until=TEST_NOW + timedelta(hours=5),
        current_micro=99_550_000,
        max_points=100,
    )
    # Floor each holding separately: the extra microshare rounds away.
    assert [point.total_equity_cr for point in points] == [
        to_cr(100 * MICRO),
        to_cr(99_500_003),
        to_cr(99_500_001),
        to_cr(99_600_001),
        to_cr(99_550_000),
        to_cr(99_550_000),
    ]


async def test_payouts_and_zero_payout_settlements_remove_the_position_value() -> None:
    losing = uuid7()
    points = await equity_history(
        stream(
            [
                event(0, balance=100 * MICRO),
                event(1, market=MARKET, outcome=Outcome.YES, shares=10 * MICRO, price=600_000),
                event(1, balance=-5 * MICRO),
                event(2, market=losing, outcome=Outcome.NO, shares=10 * MICRO, price=600_000),
                event(2, balance=-5 * MICRO),
                event(3, balance=10 * MICRO),
                event(3, market=MARKET, settled=True),
                event(4, market=losing, settled=True),
            ]
        ),
        start=TEST_NOW,
        until=TEST_NOW + timedelta(hours=5),
        current_micro=100 * MICRO,
        max_points=100,
    )
    assert [point.total_equity_cr for point in points] == [
        to_cr(n * MICRO) for n in [100, 101, 100, 104, 100, 100]
    ]


async def test_void_refunds_grants_and_voucher_spending_are_balance_movements() -> None:
    points = await equity_history(
        stream(
            [
                event(0, balance=100 * MICRO),
                event(1, market=MARKET, outcome=Outcome.YES, shares=10 * MICRO, price=600_000),
                event(1, balance=-5 * MICRO),
                event(2, balance=5 * MICRO),
                event(2, market=MARKET, settled=True),
                event(3, balance=20 * MICRO),
                event(4, balance=-35 * MICRO),
                event(5, balance=35 * MICRO),
            ]
        ),
        start=TEST_NOW,
        until=TEST_NOW + timedelta(hours=6),
        current_micro=120 * MICRO,
        max_points=100,
    )
    assert [point.total_equity_cr for point in points] == [
        to_cr(n * MICRO) for n in [100, 101, 100, 120, 85, 120, 120]
    ]


async def test_sampling_is_bounded_and_keeps_the_first_and_current_exact_amounts() -> None:
    for limit in (2, 3, 20):
        points = await equity_history(
            stream(event(hour, balance=MICRO) for hour in range(1000)),
            start=TEST_NOW,
            until=TEST_NOW + timedelta(hours=1000),
            current_micro=1000 * MICRO,
            max_points=limit,
        )
        assert len(points) <= limit
        assert points[0].total_equity_cr == to_cr(MICRO)
        assert points[-1].total_equity_cr == to_cr(1000 * MICRO)
        assert points[-1].t == TEST_NOW + timedelta(hours=1000)
        assert [point.t for point in points] == sorted(point.t for point in points)


async def test_plotting_does_not_replace_exact_large_amounts() -> None:
    total = 912_345_678_901_234_567
    points = await equity_history(
        stream([event(0, balance=total)]),
        start=TEST_NOW,
        until=TEST_NOW + timedelta(hours=1),
        current_micro=total,
        max_points=2,
    )
    assert points[-1].total_equity_cr == to_cr(total)
    assert points[-1].model_dump(mode="json")["total_equity_cr"] == "912345678901.234567"
