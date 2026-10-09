"""Live quotes over Redis pub/sub (SPEC §8 step 9, §9, A.4)."""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid7

import pytest
from redis.asyncio import Redis

from app.core.config import RedisSettings
from app.integrations.redis.client import RedisClient, RedisUnavailableError
from app.integrations.redis.quote_bus import QuoteBus

pytestmark = pytest.mark.integration

type Events = asyncio.Queue[dict[str, Any]]


@pytest.fixture
async def bus(redis_client: RedisClient) -> AsyncIterator[QuoteBus]:
    bus = QuoteBus(redis_client)
    await bus.start()
    assert bus.is_connected
    yield bus
    await bus.stop()


async def next_event(events: Events) -> dict[str, Any]:
    return await asyncio.wait_for(events.get(), timeout=5)


async def eventually(condition: Callable[[], bool]) -> None:
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0.01)
    pytest.fail("the condition did not become true within five seconds")


async def drain(events: Events) -> list[dict[str, Any]]:
    """Everything a subscription still hands out before it reports its end."""
    received = []
    try:
        while True:
            received.append(await next_event(events))
    except asyncio.QueueShutDown:
        return received


async def test_published_event_reaches_subscribers_of_that_market_only(bus: QuoteBus) -> None:
    traded, other = uuid7(), uuid7()
    quote = {"type": "quote", "version": 3, "p_yes": 0.548}

    async with (
        bus.subscribe(traded) as first,
        bus.subscribe(traded) as second,
        bus.subscribe(other) as elsewhere,
    ):
        await bus.publish(traded, quote)
        await bus.publish(other, {"type": "status", "status": "HALTED"})

        assert await next_event(first) == quote
        assert await next_event(second) == quote
        assert await next_event(elsewhere) == {"type": "status", "status": "HALTED"}
        assert first.empty()
        assert second.empty()
        assert elsewhere.empty()


async def test_event_published_by_another_process_arrives(
    bus: QuoteBus, redis_settings: RedisSettings
) -> None:
    market_id = uuid7()
    other_process = RedisClient(redis_settings)
    try:
        async with bus.subscribe(market_id) as events:
            await QuoteBus(other_process).publish(market_id, {"version": 1})

            assert await next_event(events) == {"version": 1}
    finally:
        await other_process.close()


async def test_events_published_after_a_subscription_closed_are_not_kept(bus: QuoteBus) -> None:
    market_id = uuid7()
    async with bus.subscribe(market_id) as closed:
        pass

    async with bus.subscribe(market_id) as events:
        await bus.publish(market_id, {"version": 1})
        assert await next_event(events) == {"version": 1}

    assert closed.empty()


async def test_dropped_connection_ends_open_subscriptions_and_the_bus_reconnects(
    bus: QuoteBus, raw_redis: Redis
) -> None:
    market_id = uuid7()

    async with bus.subscribe(market_id) as events:
        await bus.publish(market_id, {"version": 1})
        assert await next_event(events) == {"version": 1}

        assert await raw_redis.client_kill_filter(_type="pubsub") == 1

        with pytest.raises(asyncio.QueueShutDown):
            await next_event(events)

    assert not bus.is_connected
    with pytest.raises(RedisUnavailableError):
        async with bus.subscribe(market_id):
            pytest.fail("a subscription opened while the listener was disconnected")

    await eventually(lambda: bus.is_connected)
    async with bus.subscribe(market_id) as events:
        await bus.publish(market_id, {"version": 2})
        assert await next_event(events) == {"version": 2}


async def test_subscriber_that_falls_behind_is_ended_after_what_it_already_has(
    bus: QuoteBus,
) -> None:
    slow, watched = uuid7(), uuid7()

    async with bus.subscribe(slow) as backlog, bus.subscribe(watched) as marker:
        for version in range(200):
            await bus.publish(slow, {"version": version})
        # One connection delivers in order: once this arrives, all 200 have been handled.
        await bus.publish(watched, {"done": True})
        assert await next_event(marker) == {"done": True}

        received = await drain(backlog)

    assert 0 < len(received) < 200
    assert received == [{"version": version} for version in range(len(received))]


async def test_stop_ends_open_subscriptions(redis_client: RedisClient) -> None:
    bus = QuoteBus(redis_client)
    await bus.start()

    async with bus.subscribe(uuid7()) as events:
        await bus.stop()

        with pytest.raises(asyncio.QueueShutDown):
            await next_event(events)
    assert not bus.is_connected


async def test_event_that_is_not_json_is_dropped_without_raising(bus: QuoteBus) -> None:
    market_id = uuid7()

    async with bus.subscribe(market_id) as events:
        await bus.publish(market_id, {"when": object()})
        await bus.publish(market_id, {"version": 1})

        assert await next_event(events) == {"version": 1}
