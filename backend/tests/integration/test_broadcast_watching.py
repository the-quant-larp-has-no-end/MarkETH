"""Watching a live stream, and the lists of the ones that run (SPEC §6).

The proxy serves a playlist and its segments to a caller the API admitted for that
broadcast: it asks `GET /broadcasts/{id}/access` for every viewer and remembers a yes
for a few seconds. Whoever is inside the market watches while the broadcast is on air
and a place is free. Everybody else gets one answer, whatever the reason, except a full
house.

The application runs on Postgres and Redis, with `FakeMediaBox` where the media box
stands (tests/support/media.py). The rows are written as the lifecycle leaves them.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, Final
from uuid import uuid7

import httpx
import pytest

from app.container import Container
from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import BroadcastEndReason, BroadcastStatus, MarketStatus, Outcome
from app.core.units import MICRO
from app.services.broadcasts import FULL_MESSAGE, NOT_AVAILABLE_MESSAGE
from tests.support.books import assert_books_balance
from tests.support.broadcasts import (
    Calls,
    as_json,
    broadcast,
    private_market,
    refusal,
    stored,
)
from tests.support.clock import TEST_NOW, FrozenClock
from tests.support.factories import DEFAULT_DEGREE_PROGRAMME, ActAs, Factory
from tests.support.media import FakeMediaBox, behind, webm
from tests.support.streams import MARKETS, broadcast_of, listening, settled

pytestmark = pytest.mark.integration

NOT_FOUND: Final[tuple[int, str, str, dict[str, Any]]] = (404, "NOT_FOUND", "Not found.", {})

NOT_AVAILABLE: Final[tuple[int, str, str, dict[str, Any]]] = (
    403,
    "FORBIDDEN",
    NOT_AVAILABLE_MESSAGE,
    {},
)
"""The one answer to whoever may not watch, whatever the reason."""

FULL: Final[tuple[int, str, str, dict[str, Any]]] = (403, "FORBIDDEN", FULL_MESSAGE, {})

OTHER_PROGRAMME: Final[str] = next(
    programme
    for level in CATALOGUE
    for programme in level.programmes
    if programme != DEFAULT_DEGREE_PROGRAMME
)
"""A programme the users of the factory are not in."""

SECOND: Final[timedelta] = timedelta(seconds=1)


@pytest.fixture
async def box() -> AsyncIterator[FakeMediaBox]:
    async with FakeMediaBox() as listening_box:
        yield listening_box


@pytest.fixture
def limits() -> dict[str, object]:
    """Other values of the section "Live video" a test runs with."""
    return {}


@pytest.fixture
def container_settings(
    container_settings: Settings, box: FakeMediaBox, limits: dict[str, object]
) -> Settings:
    """The settings of the application, with the media box of the test behind them."""
    return behind(container_settings, box, **limits)


@pytest.fixture
def calls(client: httpx.AsyncClient, as_user: ActAs) -> Calls:
    return Calls(client, as_user)


# --- The question of the proxy --------------------------------------------------------


async def test_whoever_is_inside_the_market_watches_while_the_live_stream_is_on_air(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    barista = await factory.barista()
    market = await factory.market(created_by=proposer)
    on_air = await broadcast(
        container, market, proposer, BroadcastStatus.LIVE, decided_by=admin, on_air=True
    )

    for who in (member, barista, proposer, admin):
        response = await calls.access(who, on_air.id)
        assert response.status_code == 200, response.text
        assert response.json() == {"broadcast_id": str(on_air.id)}
        # The proxy remembers a yes for its own figure, and obeys these over it.
        assert not {"cache-control", "expires", "set-cookie"} & set(response.headers)
    assert container.broadcast_audience.count(on_air.id) == 4


async def test_whatever_cannot_be_watched_is_refused_with_one_answer(
    calls: Calls, container: Container, factory: Factory
) -> None:
    """No such broadcast, not on air, not started, over: the proxy and whoever asks it
    cannot tell them apart, and none of them is a 404."""
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    markets = [await factory.market(created_by=proposer) for _ in range(5)]
    not_watchable = [
        await broadcast(container, markets[0], proposer, BroadcastStatus.REQUESTED),
        await broadcast(
            container, markets[1], proposer, BroadcastStatus.APPROVED, decided_by=admin
        ),
        await broadcast(container, markets[2], proposer, BroadcastStatus.REFUSED, decided_by=admin),
        await broadcast(container, markets[3], proposer, BroadcastStatus.LIVE, decided_by=admin),
        await broadcast(
            container, markets[4], proposer, BroadcastStatus.ENDED, decided_by=admin, on_air=True
        ),
    ]

    answers = [await calls.access(member, row.id) for row in not_watchable]
    answers.append(await calls.access(member, uuid7()))
    answers.append(await calls.access(proposer, not_watchable[3].id))
    answers.append(await calls.access(admin, not_watchable[0].id))

    for answer in answers:
        assert refusal(answer) == NOT_AVAILABLE
    assert len({answer.content for answer in answers}) == 1, "one answer, one body"
    assert all(container.broadcast_audience.count(row.id) == 0 for row in not_watchable)


async def test_a_live_stream_the_clock_ended_is_not_watched_and_the_question_stores_its_end(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox, clock: FrozenClock
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market()
    on_air = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    stall = timedelta(seconds=container.settings.broadcast.stall_seconds)
    clock.advance(stall - SECOND)
    assert (await calls.access(member, on_air.id)).status_code == 200
    assert container.broadcast_audience.count(on_air.id) == 1
    clock.advance(SECOND)

    answer = await calls.access(member, on_air.id)

    assert refusal(answer) == NOT_AVAILABLE
    row = await stored(container, on_air.id)
    assert (row.status, row.end_reason, row.ended_at) == (
        BroadcastStatus.ENDED,
        BroadcastEndReason.STALLED,
        TEST_NOW + stall,
    )
    assert container.broadcast_audience.count(on_air.id) == 0, "the places are given back"
    assert box.asked("stop") == [{"op": "stop", "id": str(on_air.id)}]


async def test_an_id_that_is_none_is_no_question_about_a_broadcast(
    calls: Calls, factory: Factory
) -> None:
    member = await factory.user()

    response = await calls.access(member, "not-an-id")

    assert (response.status_code, response.json()["code"]) == (422, "VALIDATION_ERROR")


async def test_the_end_of_a_live_stream_ends_the_watching_and_gives_the_places_back(
    calls: Calls, container: Container, factory: Factory
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market = await factory.market()
    on_air = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    assert (await calls.access(member, on_air.id)).status_code == 200

    await calls.end(admin, on_air.id)

    assert refusal(await calls.access(member, on_air.id)) == NOT_AVAILABLE
    assert container.broadcast_audience.count(on_air.id) == 0


# --- The places -----------------------------------------------------------------------


@pytest.mark.parametrize("limits", [{"max_viewers": 2}])
async def test_the_person_beyond_the_places_is_refused_and_gets_in_once_a_place_is_given_back(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    first, second, third = [await factory.user() for _ in range(3)]
    market = await factory.market()
    on_air = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    seat = timedelta(seconds=container.settings.broadcast.seat_seconds)

    assert (await calls.read(third, market.id)).json()["viewer"]["can_watch"] is True
    admitted = [await calls.access(who, on_air.id) for who in (first, second, third)]

    assert [answer.status_code for answer in admitted] == [200, 200, 403]
    assert refusal(admitted[2]) == FULL
    watching = {
        who.username: (await calls.read(who, market.id)).json()["viewer"]
        for who in (first, second, third)
    }
    assert [(seen["can_watch"], seen["full"]) for seen in watching.values()] == [
        (True, False),
        (True, False),
        (False, True),
    ]
    shown_to_the_third = (await calls.read(third, market.id)).json()["broadcast"]
    assert shown_to_the_third["on_air"] is True, "the market and its stream stay in sight"

    # The two keep watching: the proxy asks about them again, and they keep their places.
    clock.advance(seat - SECOND)
    assert [(await calls.access(who, on_air.id)).status_code for who in (first, second)] == [
        200,
        200,
    ]
    assert refusal(await calls.access(third, on_air.id)) == FULL

    # The first stops watching: nobody asks about them for `seat_seconds`.
    clock.advance(seat - SECOND)
    assert (await calls.access(second, on_air.id)).status_code == 200
    clock.advance(SECOND)
    assert (await calls.access(third, on_air.id)).status_code == 200
    assert refusal(await calls.access(first, on_air.id)) == FULL


# --- The lists ------------------------------------------------------------------------


async def test_live_now_lists_what_is_on_air_and_nothing_else_earliest_start_first(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, proposer, member = await factory.admin(), await factory.user(), await factory.user()
    markets = [
        await factory.market(created_by=proposer, question=f"Is market {number} on air?")
        for number in range(6)
    ]
    later_one = await broadcast(container, markets[0], admin, BroadcastStatus.LIVE, on_air=True)
    clock.set(TEST_NOW - timedelta(minutes=1))
    earlier_one = await broadcast(container, markets[1], admin, BroadcastStatus.LIVE, on_air=True)
    clock.set(TEST_NOW)
    await broadcast(container, markets[2], admin, BroadcastStatus.LIVE)
    await broadcast(container, markets[3], admin, BroadcastStatus.ENDED, on_air=True)
    await broadcast(container, markets[4], proposer, BroadcastStatus.REQUESTED)
    await broadcast(container, markets[5], proposer, BroadcastStatus.APPROVED, decided_by=admin)

    for who in (member, proposer, admin):
        response = await calls.live(who)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": [
                {
                    "broadcast_id": str(earlier_one.id),
                    "market_id": str(markets[1].id),
                    "question": "Is market 1 on air?",
                    "started_at": as_json(TEST_NOW - timedelta(minutes=1)),
                },
                {
                    "broadcast_id": str(later_one.id),
                    "market_id": str(markets[0].id),
                    "question": "Is market 0 on air?",
                    "started_at": as_json(TEST_NOW),
                },
            ]
        }


async def test_live_now_is_empty_while_nothing_is_on_air(calls: Calls, factory: Factory) -> None:
    member = await factory.user()

    assert (await calls.live(member)).json() == {"items": []}


async def test_the_admins_list_holds_every_open_live_stream_with_who_broadcasts(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    markets = [
        await factory.market(created_by=proposer, question=f"Does market {number} wait?")
        for number in range(5)
    ]
    live = await broadcast(container, markets[0], admin, BroadcastStatus.LIVE, on_air=True)
    clock.advance(SECOND)
    approved = await broadcast(
        container, markets[1], proposer, BroadcastStatus.APPROVED, decided_by=admin
    )
    clock.advance(SECOND)
    asked = await broadcast(container, markets[2], proposer, BroadcastStatus.REQUESTED)
    await broadcast(container, markets[3], proposer, BroadcastStatus.REFUSED, decided_by=admin)
    await broadcast(container, markets[4], proposer, BroadcastStatus.ENDED, decided_by=admin)

    response = await calls.admin_list(admin)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(item["id"], item["status"], item["question"]) for item in items] == [
        (str(live.id), "LIVE", "Does market 0 wait?"),
        (str(approved.id), "APPROVED", "Does market 1 wait?"),
        (str(asked.id), "REQUESTED", "Does market 2 wait?"),
    ]
    assert [item["requested_by"] for item in items] == [
        {"username": admin.username, "email": admin.email},
        {"username": proposer.username, "email": proposer.email},
        {"username": proposer.username, "email": proposer.email},
    ]
    assert items[0] == {
        "id": str(live.id),
        "market_id": str(markets[0].id),
        "status": "LIVE",
        "on_air": True,
        "playlist_url": f"/api/broadcasts/{live.id}/hls/index.m3u8",
        "requested_at": as_json(TEST_NOW),
        "started_at": as_json(TEST_NOW),
        "ended_at": None,
        "end_reason": None,
        "refusal_reason": None,
        "requested_by": {"username": admin.username, "email": admin.email},
        "question": "Does market 0 wait?",
    }


async def test_a_live_stream_the_clock_ended_leaves_both_lists_and_the_read_stores_its_end(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, member = await factory.admin(), await factory.user()
    market, other_market = await factory.market(), await factory.market()
    stalls = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds) - SECOND)
    stays = await broadcast(container, other_market, admin, BroadcastStatus.LIVE, on_air=True)
    assert len((await calls.live(member)).json()["items"]) == 2
    clock.advance(SECOND)

    live_now = await calls.live(member)

    assert [item["broadcast_id"] for item in live_now.json()["items"]] == [str(stays.id)]
    assert (await stored(container, stalls.id)).status is BroadcastStatus.ENDED
    assert [item["id"] for item in (await calls.admin_list(admin)).json()["items"]] == [
        str(stays.id)
    ]


async def test_the_admins_list_stores_the_end_the_clock_brought_as_well(
    calls: Calls, container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin = await factory.admin()
    market = await factory.market()
    stalls = await broadcast(container, market, admin, BroadcastStatus.LIVE)
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))

    response = await calls.admin_list(admin)

    assert response.json() == {"items": []}
    assert (await stored(container, stalls.id)).end_reason is BroadcastEndReason.STALLED


# --- A market for one programme -------------------------------------------------------


async def test_the_live_stream_of_a_private_market_stays_inside_the_market(
    calls: Calls, container: Container, factory: Factory, box: FakeMediaBox
) -> None:
    """The application has no way to make such a market yet. Every operation asks
    `MarketAccess.get_inside` all the same: an outsider gets the answers an id nobody
    has gets, and hears nothing."""
    admin = await factory.admin()
    proposer, member = await factory.user(), await factory.user()
    outsider = await factory.user(degree_programme=OTHER_PROGRAMME)
    holder = await factory.user(degree_programme=OTHER_PROGRAMME, balance_micro=5 * MICRO)
    market = await private_market(container, proposer, DEFAULT_DEGREE_PROGRAMME)
    await factory.position(holder, market, Outcome.YES, shares_micro=MICRO)
    on_air = await broadcast(
        container, market, proposer, BroadcastStatus.LIVE, decided_by=admin, on_air=True
    )

    async with listening(
        container.live, factory.identity(outsider), [broadcast_of(market), MARKETS]
    ) as heard:
        for inside in (member, proposer, admin):
            seen = (await calls.read(inside, market.id)).json()
            assert seen["broadcast"]["status"] == "LIVE"
            assert (await calls.access(inside, on_air.id)).status_code == 200
            assert [item["market_id"] for item in (await calls.live(inside)).json()["items"]] == [
                str(market.id)
            ]

        # Somebody who holds shares of the market from outside its audience keeps the
        # page of the market and nothing other people put there.
        for outside in (outsider, holder):
            assert refusal(await calls.read(outside, market.id)) == NOT_FOUND
            assert refusal(await calls.request(outside, market.id)) == NOT_FOUND
            assert refusal(await calls.start(outside, on_air.id)) == NOT_FOUND
            assert refusal(await calls.chunk(outside, on_air.id, 0, webm())) == NOT_FOUND
            assert refusal(await calls.end(outside, on_air.id)) == NOT_FOUND
            assert (await calls.live(outside)).json() == {"items": []}
            refused = await calls.access(outside, on_air.id)
            unknown = await calls.access(outside, uuid7())
            assert refusal(refused) == NOT_AVAILABLE
            assert refused.content == unknown.content

        await calls.end(proposer, on_air.id)
        await settled()

    assert heard.bodies == []
    assert box.asked("chunk") == []
    assert (await stored(container, on_air.id)).ended_by == proposer.id
    assert container.broadcast_audience.count(on_air.id) == 0
    await assert_books_balance(container.uow_factory)


async def test_a_proposal_has_no_live_stream_to_watch(
    calls: Calls, container: Container, factory: Factory
) -> None:
    """A row that names a market nobody is inside of is not watched by anybody."""
    admin, proposer = await factory.admin(), await factory.user()
    proposal = await factory.market(status=MarketStatus.PROPOSED, created_by=proposer)
    row = await broadcast(
        container, proposal, proposer, BroadcastStatus.LIVE, decided_by=admin, on_air=True
    )

    for who in (admin, proposer):
        assert refusal(await calls.access(who, row.id)) == NOT_AVAILABLE
    assert (await calls.live(admin)).json() == {"items": []}
    assert (await calls.admin_list(admin)).json() == {"items": []}


# --- What the live chat asks ----------------------------------------------------------


async def test_the_chat_is_told_which_broadcast_of_a_market_is_live(
    container: Container, factory: Factory, clock: FrozenClock
) -> None:
    admin, proposer = await factory.admin(), await factory.user()
    markets = [await factory.market(created_by=proposer) for _ in range(6)]
    live = await broadcast(container, markets[0], admin, BroadcastStatus.LIVE)
    for market, status in zip(
        markets[1:5],
        (
            BroadcastStatus.REQUESTED,
            BroadcastStatus.APPROVED,
            BroadcastStatus.REFUSED,
            BroadcastStatus.ENDED,
        ),
        strict=True,
    ):
        await broadcast(container, market, proposer, status, decided_by=admin)
    lookup = container.broadcast_lookup

    async with container.uow_factory() as uow:
        found = [await lookup.live_broadcast_id(uow, market.id) for market in markets]
    clock.advance(timedelta(seconds=container.settings.broadcast.stall_seconds))
    async with container.uow_factory() as uow:
        once_it_stalled = await lookup.live_broadcast_id(uow, markets[0].id)

    assert found == [live.id, None, None, None, None, None]
    assert once_it_stalled is None
    assert (await stored(container, live.id)).status is BroadcastStatus.LIVE, (
        "the lookup runs in the chat's transaction and writes nothing"
    )
