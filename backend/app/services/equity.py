"""Replay equity in integer micro-units; keep a bounded chart without storing snapshots."""

from collections.abc import AsyncIterable
from datetime import datetime
from uuid import UUID

from app.core.enums import Outcome
from app.core.units import MICRO, to_cr
from app.repositories.equity import EquityEvent
from app.schemas.me import EquityPoint


def equity_point(at: datetime, value_micro: int) -> EquityPoint:
    exact = to_cr(value_micro)
    return EquityPoint(t=at, total_equity_cr=exact, plot_value=float(exact))


async def equity_history(
    events: AsyncIterable[EquityEvent],
    *,
    start: datetime,
    until: datetime,
    current_micro: int,
    max_points: int,
) -> list[EquityPoint]:
    """Value each outcome separately, rounding down as the portfolio does.

    Retain the opening figure, the last change in each time bucket and the current
    figure. Both the database cursor and the chart use bounded memory. Events at
    the same instant are combined before sampling; payouts replace the settled
    position value, rather than being added to it.
    """
    balance = 0
    value = 0
    shares: dict[UUID, dict[Outcome, int]] = {}
    values: dict[UUID, int] = {}
    settled: set[UUID] = set()
    samples: dict[int, EquityPoint] = {}
    opening = equity_point(start, 0)
    span = max((until - start).total_seconds(), 1)
    buckets = max_points - 2
    previous_at: datetime | None = None
    previous_value: int | None = None

    def record(at: datetime) -> None:
        nonlocal opening, previous_value
        total = balance + value
        if at <= opening.t:
            opening = equity_point(at, total)
        elif total != previous_value and buckets > 0:
            bucket = min(buckets - 1, max(0, int((at - start).total_seconds() / span * buckets)))
            samples[bucket] = equity_point(at, total)
        previous_value = total

    async for event in events:
        if previous_at is not None and event.at != previous_at:
            record(previous_at)
        previous_at = event.at
        balance += event.balance_delta_micro
        market_id = event.market_id
        if market_id is None:
            continue
        value -= values.get(market_id, 0)
        held = shares.setdefault(market_id, dict.fromkeys(Outcome, 0))
        if event.outcome is not None:
            held[event.outcome] += event.shares_delta_micro
        if event.settled:
            settled.add(market_id)
        if market_id in settled:
            values[market_id] = 0
        elif event.p_yes_micro is not None:
            values[market_id] = (
                held[Outcome.YES] * event.p_yes_micro // MICRO
                + held[Outcome.NO] * (MICRO - event.p_yes_micro) // MICRO
            )
        value += values.get(market_id, 0)
    if previous_at is not None:
        record(previous_at)

    points = [opening, *samples.values()]
    current = equity_point(until, current_micro)
    if points[-1].t == until:
        points[-1] = current
    else:
        points.append(current)
    return points
