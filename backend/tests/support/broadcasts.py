"""Broadcasts in a test: rows as the lifecycle leaves them, what the database holds of
them, and a stand-in for what a stored end calls (SPEC §6).

    row = await broadcast(container, market, proposer, BroadcastStatus.APPROVED)
    live = await broadcast(container, market, admin, BroadcastStatus.LIVE, on_air=True)

writes a row straight to the database, without the service under test and without the
media box: the row a request, an approval, a start or an end leaves behind.

    ends = RecordedEnds()
    service = service_with(container, on_end=ends)

is the broadcast service as the container wires it, with `ends` where the live chat
stands. It takes the place of `container.broadcasts`, so a request to the application
reaches it too.

    calls = Calls(client, as_user)
    response = await calls.start_as_admin(admin, market)
    assert refusal(await calls.end(member, live.id)) == (404, "NOT_FOUND", "Not found.", {})

are the eleven operations as requests to the application, each made as the user the
test names.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

import httpx
from sqlalchemy import select

from app.container import Container
from app.core.enums import BroadcastEndReason, BroadcastStatus, MarketStatus, MarketTier
from app.models import Market, User
from app.models.broadcast import Broadcast
from app.models.ops import AuditLogEntry
from app.pricing import lmsr
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.broadcasts import BroadcastEnd, BroadcastService
from tests.support.factories import ActAs, Actor

ACCEPTED: Final[dict[str, Any]] = {"policy_accepted": True}
"""The body of a request for a live stream."""

PART: Final[str] = "part-0001"
"""The id a studio of the tests gives the first run of its recorder."""

STARTED: Final[frozenset[BroadcastStatus]] = frozenset(
    {BroadcastStatus.LIVE, BroadcastStatus.ENDED}
)
"""The statuses of a row that was started."""


async def broadcast(
    container: Container,
    market: Market,
    broadcaster: User,
    status: BroadcastStatus = BroadcastStatus.LIVE,
    *,
    decided_by: User | None = None,
    on_air: bool = False,
    end_reason: BroadcastEndReason = BroadcastEndReason.BROADCASTER,
    resumed_from: Broadcast | None = None,
) -> Broadcast:
    """A broadcast of `market` by `broadcaster` in `status`, as the lifecycle leaves the
    row, with every instant the clock's now.

    `decided_by` is the admin who approved or refused; left out, the broadcaster
    decided, which is the row of an admin who started without a request. `on_air`
    gives a started row something to play. An ENDED row ends now for `end_reason`,
    by its broadcaster unless the reason says it ended by itself.
    """
    now = container.clock.now()
    row = Broadcast(
        market_id=market.id,
        broadcaster_id=broadcaster.id,
        status=status,
        requested_at=now,
        resumed_from=None if resumed_from is None else resumed_from.id,
    )
    if status is not BroadcastStatus.REQUESTED:
        row.decided_by = (decided_by or broadcaster).id
        row.decided_at = now
    if status is BroadcastStatus.REFUSED:
        row.refusal_reason = "Refused in a test."
    if status in STARTED:
        row.started_at = now
        row.last_chunk_at = now
        row.on_air_at = now if on_air else None
    if status is BroadcastStatus.ENDED:
        row.ended_at = now
        row.end_reason = end_reason
        if end_reason is BroadcastEndReason.BROADCASTER:
            row.ended_by = broadcaster.id
    async with container.uow_factory() as uow:
        await uow.broadcasts.add(row)
    return row


async def stored(container: Container, broadcast_id: UUID) -> Broadcast:
    """The row of a broadcast as the database holds it now."""
    async with container.uow_factory() as uow:
        row = await uow.broadcasts.get(broadcast_id)
    assert row is not None, f"no broadcast {broadcast_id}"
    return row


async def broadcasts_of(container: Container, market: Market) -> Sequence[Broadcast]:
    """Every broadcast of a market, in the order the rows were written."""
    statement = select(Broadcast).where(Broadcast.market_id == market.id).order_by(Broadcast.id)
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(statement)).all()


async def all_broadcasts(container: Container) -> Sequence[Broadcast]:
    """Every broadcast there is, in the order the rows were written."""
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(Broadcast).order_by(Broadcast.id))).all()


async def audit_rows(container: Container) -> Sequence[AuditLogEntry]:
    """The audit log, in the order it was written."""
    async with container.uow_factory() as uow:
        return (await uow.session.scalars(select(AuditLogEntry).order_by(AuditLogEntry.id))).all()


def as_json(instant: datetime) -> str:
    """An instant as a response carries it."""
    return instant.isoformat().replace("+00:00", "Z")


class RecordedEnds:
    """Stands where the live chat stands for the broadcast service (`BroadcastEnd`):
    remembers which broadcasts it was asked to clear, and what the transaction that
    asked read of each at that moment."""

    def __init__(self) -> None:
        self.cleared: list[UUID] = []
        self.seen: list[dict[str, Any]] = []
        """Per call: the status, the reason and who ended the broadcast, as the
        caller's transaction holds them when it asks."""

    async def clear(self, uow: UnitOfWork, broadcast_id: UUID) -> int:
        statement = select(
            Broadcast.status, Broadcast.end_reason, Broadcast.ended_by, Broadcast.ended_at
        ).where(Broadcast.id == broadcast_id)
        status, end_reason, ended_by, ended_at = (await uow.session.execute(statement)).one()
        self.cleared.append(broadcast_id)
        self.seen.append(
            {
                "status": status,
                "end_reason": end_reason,
                "ended_by": ended_by,
                "ended_at": ended_at,
            }
        )
        return 0


def service_with(
    container: Container, *, on_end: BroadcastEnd, uow_factory: UowFactory | None = None
) -> BroadcastService:
    """The broadcast service as the container wires it, with `on_end` where the live
    chat stands, and on the units of work of `uow_factory` when a test brings its own.
    It stands where the routes find it from then on."""
    service = BroadcastService(
        uow_factory or container.uow_factory,
        container.settings,
        container.clock,
        container.broadcast_view,
        container.market_view,
        container.market_access,
        container.audit,
        container.media,
        container.broadcast_audience,
        container.live_events,
        on_end,
    )
    container.broadcasts = service
    return service


async def private_market(container: Container, proposer: User, programme: str) -> Market:
    """An OPEN market for the members of `programme`, as an approval leaves it. The
    application has no way to make one yet; every operation of a broadcast asks
    `MarketAccess.get_inside` all the same, and a test holds it to that."""
    now = container.clock.now()
    pricing = container.settings.pricing
    b_micro = pricing.tier_b_micro(MarketTier.DEFAULT)
    market = Market(
        question="Will the exam of Analysis I be held in the HPH?",
        yes_def="It is held there.",
        no_def="It is held somewhere else.",
        resolution_criteria="The exam plan decides.",
        resolution_source="ethz.ch/exams",
        status=MarketStatus.OPEN,
        tier=MarketTier.DEFAULT,
        b_micro=b_micro,
        budget_micro=lmsr.max_loss_micro(b_micro, precision=pricing.decimal_precision),
        close_at=now + timedelta(days=7),
        resolve_by=now + timedelta(days=14),
        created_by=proposer.id,
        approved_by=proposer.id,
        audience_programme=programme,
        created_at=now,
        opened_at=now,
    )
    async with container.uow_factory() as uow:
        await uow.markets.add(market)
        await uow.accounts.create_market_account(market.id)
    return market


def refusal(response: httpx.Response) -> tuple[int, str, str, dict[str, Any]]:
    """What a refused request was answered with: the status, the code, the sentence
    and the details."""
    body = response.json()
    return response.status_code, body["code"], body["message"], body["details"]


class Calls:
    """The eleven operations of a broadcast as requests to the application, each made
    as the user it names. `who` is whoever `as_user` takes."""

    def __init__(self, client: httpx.AsyncClient, as_user: ActAs) -> None:
        self._client = client
        self._as_user = as_user

    async def read(self, who: Actor, market_id: UUID) -> httpx.Response:
        self._as_user(who)
        return await self._client.get(f"/api/markets/{market_id}/broadcast")

    async def request(
        self, who: Actor, market_id: UUID, body: dict[str, Any] | None = None
    ) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(
            f"/api/markets/{market_id}/broadcast/request", json=ACCEPTED if body is None else body
        )

    async def start(self, who: Actor, broadcast_id: UUID) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(f"/api/broadcasts/{broadcast_id}/start")

    async def chunk(
        self, who: Actor, broadcast_id: UUID, seq: int, data: bytes, *, part: str = PART
    ) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(
            f"/api/broadcasts/{broadcast_id}/chunks",
            params={"part": part, "seq": seq},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )

    async def end(self, who: Actor, broadcast_id: UUID) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(f"/api/broadcasts/{broadcast_id}/end")

    async def live(self, who: Actor) -> httpx.Response:
        self._as_user(who)
        return await self._client.get("/api/broadcasts/live")

    async def access(self, who: Actor, broadcast_id: UUID | str) -> httpx.Response:
        self._as_user(who)
        return await self._client.get(f"/api/broadcasts/{broadcast_id}/access")

    async def admin_list(self, who: Actor) -> httpx.Response:
        self._as_user(who)
        return await self._client.get("/api/admin/broadcasts")

    async def approve(self, who: Actor, broadcast_id: UUID) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(f"/api/admin/broadcasts/{broadcast_id}/approve")

    async def refuse(
        self, who: Actor, broadcast_id: UUID, reason: str = "Not this week."
    ) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(
            f"/api/admin/broadcasts/{broadcast_id}/refuse", json={"reason": reason}
        )

    async def start_as_admin(self, who: Actor, market_id: UUID) -> httpx.Response:
        self._as_user(who)
        return await self._client.post(f"/api/admin/markets/{market_id}/broadcast")
