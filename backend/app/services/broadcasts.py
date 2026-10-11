"""A market's live video: asking, deciding, going live, sending, ending and watching
(SPEC §6).

    (request)      -> REQUESTED
    REQUESTED      -> APPROVED (approve) | REFUSED (refuse)
    APPROVED       -> LIVE (start)       | REFUSED (refuse)
    (admin start)  -> LIVE
    LIVE           -> ENDED (end, or by itself: no piece for too long, the time limit)

The proposer of a market asks, an admin approves or refuses, and the proposer goes
live; an admin goes live on a market without asking. REFUSED and ENDED are final: a
market's next broadcast is another row. A broadcast that ended by itself lets its
broadcaster go live again on the approval it ran on (`BroadcastView.resumable`): the
row that follows names the ended one in `resumed_from`. A broadcast does not end
because its market is closed or settled.

What the operations share:

* **Who.** Every reader and writer is inside the market: `MarketAccess.get_inside`,
  whose refusal is the 404 an id no market has gets. A broadcast of a market the
  caller is not inside of is that 404 too, and for the proxy's question (`admit`) the
  one 403 every other refusal about a viewer has. The operations of an admin check
  `actor.is_admin` themselves.
* **One writer at a time.** Every write of a broadcast's life takes the advisory lock
  `BROADCAST_LOCK` first, so "one open broadcast per market" and "at most
  `BROADCAST_MAX_LIVE` at once" are a check and a write with nothing in between. Then
  one row of `broadcasts`, then the chat's own lock inside `on_end.clear`. No market
  row and no account is locked, and no credit moves.
* **An end the clock brought.** A row stored LIVE is over from the instant
  `BroadcastView.over` names. No job stores that. An operation that meets such a row
  stops, stores the end in a transaction of its own (`_store_end`), and starts again:
  what it then reads, decides and answers is what is stored. Every transaction that
  stores an end clears the chat of that broadcast (`on_end.clear`).
* **Told to the open tabs.** Every change announces `broadcast.changed` inside its
  transaction: a request, an approval and a refusal to the broadcaster and to the
  admins; a start, the moment a broadcast is on air and every stored end to whoever
  is inside the market (`LiveEvents.about`).
* **The answer** of a write is the market's live stream as the caller sees it, built
  inside the transaction from what it wrote.
* **The media box** is asked inside the transaction of a start, as its last step, so a
  start that cannot have its place writes nothing. Everything else the box is told
  follows a commit and is best effort: the box ends by its own timers what the API
  never stops.

No count of viewers is reported to anybody.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

from app.core.clock import Clock, start_of_day
from app.core.config import Settings
from app.core.enums import BroadcastEndReason, BroadcastStatus
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    LimitExceededError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser
from app.core.live import Audience, LiveEvent, Topic, TopicKind
from app.integrations.media.client import (
    STOP_IDS,
    ChunkRefusal,
    MediaClient,
    MediaUnavailableError,
)
from app.models.broadcast import Broadcast
from app.models.market import Market
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.broadcasts import (
    AdminBroadcastItem,
    AdminBroadcastListResponse,
    BroadcastAccessResponse,
    BroadcastChunkResponse,
    LiveBroadcastItem,
    LiveBroadcastListResponse,
    MarketBroadcastResponse,
    RefuseBroadcastRequest,
)
from app.schemas.common import Limit
from app.schemas.live import BroadcastChangedEvent
from app.services import UowFactory
from app.services.audit import AuditAction, AuditService, AuditTarget
from app.services.broadcast_audience import BroadcastAudience
from app.services.broadcast_view import BroadcastView
from app.services.live_events import LiveEvents
from app.services.market_access import MarketAccess
from app.services.market_view import MarketView
from app.services.markets import refuse_nul

logger = logging.getLogger(__name__)

ADMINS_START_MESSAGE: Final[str] = "As an admin you start a live stream yourself."
NOT_THE_PROPOSER_MESSAGE: Final[str] = (
    "Only the person who proposed a market can ask for its live stream."
)
NOT_STREAMABLE_MESSAGE: Final[str] = (
    "A live stream is possible while a market is open, paused or waiting for its result."
)
OPEN_BROADCAST_MESSAGE: Final[str] = "This market already has a live stream or a request for one."
REQUEST_LIMIT_MESSAGE: Final[str] = (
    "You have reached today's limit of live stream requests. Try again tomorrow."
)
NOT_THE_BROADCASTER_MESSAGE: Final[str] = "Only the person this live stream belongs to can do this."
NOT_APPROVED_MESSAGE: Final[str] = "This live stream is not approved."
ENDED_MESSAGE: Final[str] = "This live stream has ended."
NOT_STARTED_MESSAGE: Final[str] = "This live stream has not started."
SLOT_TAKEN_MESSAGE: Final[str] = "Another live stream is on right now. Try again when it has ended."
MEDIA_UNAVAILABLE_MESSAGE: Final[str] = "Live video is unavailable right now. Try again shortly."
NO_DATA_MESSAGE: Final[str] = "The request has no video data."
TOO_LARGE_MESSAGE: Final[str] = "The request is too large."
DECIDED_MESSAGE: Final[str] = "This request has been decided already."
LIVE_NOT_REFUSED_MESSAGE: Final[str] = "This live stream is on. End it instead."
NOT_AVAILABLE_MESSAGE: Final[str] = "This live stream is not available."
FULL_MESSAGE: Final[str] = "The live stream is full right now. Try again in a moment."
"""The sentences a broadcast is refused with. The proxy answers a playlist it does not
serve with `NOT_AVAILABLE_MESSAGE` too."""

BROADCAST_LOCK: Final[str] = "broadcasts"
"""Name of the advisory lock every write of a broadcast's life takes first."""

SETTLE_ROUNDS: Final[int] = 5
"""How often one operation starts again because it met a broadcast the clock had
ended. An operation meets the broadcast it is about, then the ones that run, and each
is stored once: two rounds are the rule, and the bound is there for a clock that does
not agree with itself."""


class BroadcastEnd(Protocol):
    """What a transaction that stores the end of a broadcast calls: `ChatService`."""

    async def clear(self, uow: UnitOfWork, broadcast_id: UUID) -> int: ...


class _FoundOver(Exception):
    """An operation met broadcasts that are stored LIVE and are over by the clock. It
    wrote nothing and asked the box nothing: their ends are stored, then it starts
    again."""

    def __init__(self, broadcast_ids: Sequence[UUID]) -> None:
        super().__init__(f"{len(broadcast_ids)} broadcast(s) are over and their end is not stored")
        self.broadcast_ids = tuple(broadcast_ids)


@dataclass(slots=True)
class _Passage:
    """What one operation did to the media box and to broadcasts it was not about, over
    all of its rounds."""

    ended: list[UUID] = field(default_factory=list)
    """The broadcasts whose end this operation stored on its way."""

    handed_over: set[UUID] = field(default_factory=set)
    """Of those, the ones a start gave to the box to stop with its own request."""

    placed: UUID | None = None
    """The broadcast this operation took a place in the box for."""


class BroadcastService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        view: BroadcastView,
        market_view: MarketView,
        access: MarketAccess,
        audit: AuditService,
        media: MediaClient,
        audience: BroadcastAudience,
        events: LiveEvents,
        on_end: BroadcastEnd,
    ) -> None:
        self._uow_factory = uow_factory
        self._settings = settings.broadcast
        self._timezone = settings.app.timezone
        self._clock = clock
        self._view = view
        self._market_view = market_view
        self._access = access
        self._audit = audit
        self._media = media
        self._audience = audience
        self._events = events
        self._on_end = on_end

    # --- Reading ------------------------------------------------------------------

    async def for_market(
        self, viewer: AuthenticatedUser, market_id: UUID
    ) -> MarketBroadcastResponse:
        """The live stream of a market as `viewer` sees it
        (`GET /markets/{id}/broadcast`).

        `NotFoundError` unless the viewer is inside the market. The broadcast of the
        answer is the market's latest one, to an admin and to its broadcaster, and to
        everybody else while it is LIVE; `viewer` holds what the caller may do.
        """

        async def read(_: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                now = self._clock.now()
                market = await self._access.get_inside(uow, viewer, market_id)
                return await self._answer(uow, viewer, market, now)

        return await self._settling(read)

    async def live(self, viewer: AuthenticatedUser) -> LiveBroadcastListResponse:
        """The broadcasts that are on air in markets `viewer` is inside of, earliest
        start first (`GET /broadcasts/live`)."""

        async def read(_: _Passage) -> LiveBroadcastListResponse:
            async with self._uow_factory() as uow:
                now = self._clock.now()
                running = await self._running(uow, now)
                on_air = [row for row in running if row.on_air_at is not None]
                if not on_air:
                    return LiveBroadcastListResponse(items=[])
                # Every market of a broadcast that is on air; the caller is told
                # about the ones they are inside of.
                markets = await uow.markets.get_many({row.market_id for row in on_air})
                inside = {
                    market.id: market
                    for market in self._access.inside_among(viewer, markets.values())
                }
                return LiveBroadcastListResponse(
                    items=[
                        LiveBroadcastItem(
                            broadcast_id=row.id,
                            market_id=row.market_id,
                            question=inside[row.market_id].question,
                            started_at=_instant(row.started_at),
                        )
                        for row in on_air
                        if row.market_id in inside
                    ]
                )

        return await self._settling(read)

    async def admin_list(self, actor: AuthenticatedUser) -> AdminBroadcastListResponse:
        """Every open broadcast, earliest request first, each with the question of its
        market and with who broadcasts (`GET /admin/broadcasts`)."""
        _require_admin(actor)

        async def read(_: _Passage) -> AdminBroadcastListResponse:
            async with self._uow_factory() as uow:
                now = self._clock.now()
                # The admin's list: every open broadcast with its market.
                listed = await uow.broadcasts.list_open_with_market_and_broadcaster()
                self._require_settled((row for row, _, _ in listed), now)
                return AdminBroadcastListResponse(
                    items=[
                        AdminBroadcastItem(
                            **dict(self._view.item(row, now, actor, broadcaster)),
                            question=market.question,
                        )
                        for row, market, broadcaster in listed
                        if self._access.is_inside(actor, market)
                    ]
                )

        return await self._settling(read)

    async def admit(self, viewer: AuthenticatedUser, broadcast_id: UUID) -> BroadcastAccessResponse:
        """Give `viewer` a place to watch, or refuse with 403: what the proxy asks
        before it serves a playlist or a segment (`GET /broadcasts/{id}/access`).

        `ForbiddenError(NOT_AVAILABLE_MESSAGE)` for an id no broadcast has, for a
        broadcast that is not on air and for a viewer who is not inside its market:
        one answer, one body, never a 404. `ForbiddenError(FULL_MESSAGE)` when every
        one of the `BROADCAST_MAX_VIEWERS` places is held by somebody else. A yes
        keeps the viewer's place for `seat_seconds`.
        """

        async def read(_: _Passage) -> UUID:
            async with self._uow_factory() as uow:
                now = self._clock.now()
                row = await uow.broadcasts.get(broadcast_id)
                if row is None:
                    raise ForbiddenError(NOT_AVAILABLE_MESSAGE)
                try:
                    await self._access.get_inside(uow, viewer, row.market_id)
                except NotFoundError:
                    raise ForbiddenError(NOT_AVAILABLE_MESSAGE) from None
                self._require_settled((row,), now)
                if not self._view.is_on_air(row, now):
                    raise ForbiddenError(NOT_AVAILABLE_MESSAGE)
                return row.id

        admitted_to = await self._settling(read)
        if not self._audience.admit(admitted_to, viewer.id):
            raise ForbiddenError(FULL_MESSAGE)
        return BroadcastAccessResponse(broadcast_id=admitted_to)

    # --- Asking and deciding ------------------------------------------------------

    async def request(self, user: AuthenticatedUser, market_id: UUID) -> MarketBroadcastResponse:
        """Ask for a live stream as the market's proposer
        (`POST /markets/{id}/broadcast/request`). The caller accepted the usage policy
        with the request: its model takes nothing else.

        Checked in this order, after the 404 of a market the caller is not inside of:
        an admin (`ForbiddenError`, `ADMINS_START_MESSAGE`); somebody who did not
        propose the market (`ForbiddenError`, `NOT_THE_PROPOSER_MESSAGE`); a market
        that is not streamable (`ConflictError`, `NOT_STREAMABLE_MESSAGE`); a market
        with an open broadcast (`ConflictError`, `OPEN_BROADCAST_MESSAGE`); and
        `BROADCAST_REQUESTS_PER_USER_PER_DAY` broadcasts asked for since the day began
        in `APP_TIMEZONE` (`LimitExceededError`, `REQUEST_LIMIT_MESSAGE`,
        `Limit.MAX_BROADCAST_REQUESTS_PER_USER_PER_DAY`).

        Writes a REQUESTED row and tells the caller and the admins.
        """

        async def write(_: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                await uow.advisory_lock(BROADCAST_LOCK)
                now = self._clock.now()
                market = await self._access.get_inside(uow, user, market_id)
                if user.is_admin:
                    raise ForbiddenError(ADMINS_START_MESSAGE)
                if market.created_by != user.id:
                    raise ForbiddenError(NOT_THE_PROPOSER_MESSAGE)
                self._require_streamable(market, now)
                if self._view.is_open(await self._latest(uow, market.id, now), now):
                    raise ConflictError(OPEN_BROADCAST_MESSAGE)
                today = start_of_day(now, self._timezone)
                asked = await uow.broadcasts.count_asked_by_since(user.id, today)
                if asked >= self._settings.requests_per_user_per_day:
                    raise LimitExceededError(
                        REQUEST_LIMIT_MESSAGE,
                        details={"limit": Limit.MAX_BROADCAST_REQUESTS_PER_USER_PER_DAY},
                    )
                row = await uow.broadcasts.add(
                    Broadcast(
                        market_id=market.id,
                        broadcaster_id=user.id,
                        status=BroadcastStatus.REQUESTED,
                        requested_at=now,
                    )
                )
                self._tell_broadcaster_and_admins(uow, row, now)
                return await self._answer(uow, user, market, now)

        return await self._settling(write)

    async def approve(
        self, actor: AuthenticatedUser, broadcast_id: UUID
    ) -> MarketBroadcastResponse:
        """Approve a request as an admin (`POST /admin/broadcasts/{id}/approve`).
        REQUESTED -> APPROVED.

        `NotFoundError` for an id no broadcast has. A broadcast that is not REQUESTED
        is `ConflictError` (`DECIDED_MESSAGE`), and so is one whose market is not
        streamable (`NOT_STREAMABLE_MESSAGE`). Audit `BROADCAST_APPROVE`.
        """
        _require_admin(actor)

        async def write(_: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                row, market, now = await self._locked(uow, actor, broadcast_id)
                if row.status is not BroadcastStatus.REQUESTED:
                    raise ConflictError(DECIDED_MESSAGE)
                self._require_streamable(market, now)
                row.status = BroadcastStatus.APPROVED
                row.decided_by = actor.id
                row.decided_at = now
                await self._record(
                    uow,
                    actor,
                    AuditAction.BROADCAST_APPROVE,
                    row,
                    before={"status": BroadcastStatus.REQUESTED.value},
                    after={},
                )
                self._tell_broadcaster_and_admins(uow, row, now)
                return await self._answer(uow, actor, market, now)

        return await self._settling(write)

    async def refuse(
        self, actor: AuthenticatedUser, broadcast_id: UUID, request: RefuseBroadcastRequest
    ) -> MarketBroadcastResponse:
        """Refuse a request as an admin, or take an approval back
        (`POST /admin/broadcasts/{id}/refuse`). REQUESTED or APPROVED -> REFUSED.

        `NotFoundError` for an id no broadcast has. A broadcast that is LIVE is
        `ConflictError` (`LIVE_NOT_REFUSED_MESSAGE`): it is ended, not refused. One
        that is REFUSED or ENDED is `ConflictError` (`DECIDED_MESSAGE`). The reason
        is stored for the broadcaster and the admins. Audit `BROADCAST_REFUSE`.
        """
        _require_admin(actor)
        refuse_nul(request)

        async def write(_: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                row, market, now = await self._locked(uow, actor, broadcast_id)
                if row.status is BroadcastStatus.LIVE:
                    raise ConflictError(LIVE_NOT_REFUSED_MESSAGE)
                if row.status not in (BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED):
                    raise ConflictError(DECIDED_MESSAGE)
                previous = row.status
                row.status = BroadcastStatus.REFUSED
                row.decided_by = actor.id
                row.decided_at = now
                row.refusal_reason = request.reason
                await self._record(
                    uow,
                    actor,
                    AuditAction.BROADCAST_REFUSE,
                    row,
                    before={"status": previous.value},
                    after={"reason": request.reason},
                )
                self._tell_broadcaster_and_admins(uow, row, now)
                return await self._answer(uow, actor, market, now)

        return await self._settling(write)

    # --- Going live ---------------------------------------------------------------

    async def start_as_admin(
        self, actor: AuthenticatedUser, market_id: UUID
    ) -> MarketBroadcastResponse:
        """Go live on a market as an admin, without a request
        (`POST /admin/markets/{id}/broadcast`). The row is created LIVE, with the admin
        as its broadcaster and as the one who decided.

        Checked in this order, after the 404: a market that is not streamable
        (`ConflictError`, `NOT_STREAMABLE_MESSAGE`); a market with an open broadcast
        (`ConflictError`, `OPEN_BROADCAST_MESSAGE`), unless that is the caller's own
        LIVE one, which is answered again and changes nothing, because the answer to
        a start can get lost; `BROADCAST_MAX_LIVE` broadcasts running
        (`LimitExceededError`, `SLOT_TAKEN_MESSAGE`, `Limit.MAX_LIVE_BROADCASTS`);
        and a media box that does not answer or has no place
        (`ServiceUnavailableError`, `MEDIA_UNAVAILABLE_MESSAGE`), after which nothing
        is written. Audit `BROADCAST_START`.
        """
        _require_admin(actor)

        async def write(passage: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                await uow.advisory_lock(BROADCAST_LOCK)
                now = self._clock.now()
                market = await self._access.get_inside(uow, actor, market_id)
                self._require_streamable(market, now)
                latest = await self._latest(uow, market.id, now)
                if latest is not None and self._view.is_open(latest, now):
                    if latest.status is BroadcastStatus.LIVE and latest.broadcaster_id == actor.id:
                        return await self._answer(uow, actor, market, now)
                    raise ConflictError(OPEN_BROADCAST_MESSAGE)
                await self._require_free_slot(uow, now)
                row = await uow.broadcasts.add(
                    Broadcast(
                        market_id=market.id,
                        broadcaster_id=actor.id,
                        status=BroadcastStatus.LIVE,
                        requested_at=now,
                        decided_by=actor.id,
                        decided_at=now,
                        started_at=now,
                        last_chunk_at=now,
                    )
                )
                await self._record(
                    uow, actor, AuditAction.BROADCAST_START, row, before=None, after={}
                )
                uow.announce(self._to_the_market(market, now))
                answer = await self._answer(uow, actor, market, now)
                await self._take_place(row.id, passage)
                return answer

        return await self._settling(write)

    async def start(self, user: AuthenticatedUser, broadcast_id: UUID) -> MarketBroadcastResponse:
        """Go live as the broadcaster of an approved broadcast
        (`POST /broadcasts/{id}/start`). APPROVED -> LIVE. The broadcast of the answer
        is the one that is LIVE.

        `NotFoundError` for an id no broadcast has, for a broadcast of a market the
        caller is not inside of, and for a caller who is neither its broadcaster nor
        an admin. An admin who is not its broadcaster: `ForbiddenError`
        (`NOT_THE_BROADCASTER_MESSAGE`). Then, in this order:

        * LIVE: answered again, nothing changes and the box is not asked. The answer
          to a start can get lost.
        * REQUESTED or REFUSED: `ConflictError` (`NOT_APPROVED_MESSAGE`).
        * ENDED: the broadcaster goes live again without asking when the broadcast
          ended by itself (`BroadcastView.resumable`). Another row is written LIVE,
          with the market, the broadcaster and the decision of the ended one, and
          `resumed_from` naming it. A start that is sent again for the ended row
          while that other row runs is answered with it. Every other ENDED row is
          `ConflictError` (`ENDED_MESSAGE`, `details {status, end_reason}`): the
          proposer asks again.
        * A market that is not streamable: `ConflictError` (`NOT_STREAMABLE_MESSAGE`).
        * `BROADCAST_MAX_LIVE` broadcasts running: `LimitExceededError`
          (`SLOT_TAKEN_MESSAGE`, `Limit.MAX_LIVE_BROADCASTS`).
        * A media box that does not answer or has no place: `ServiceUnavailableError`
          (`MEDIA_UNAVAILABLE_MESSAGE`), and nothing is written.

        Audit `BROADCAST_START` when the broadcaster is an admin.
        """

        async def write(passage: _Passage) -> MarketBroadcastResponse:
            async with self._uow_factory() as uow:
                row, market, now = await self._locked(uow, user, broadcast_id)
                if row.broadcaster_id != user.id:
                    if user.is_admin:
                        raise ForbiddenError(NOT_THE_BROADCASTER_MESSAGE)
                    raise NotFoundError
                if row.status is BroadcastStatus.LIVE:
                    return await self._answer(uow, user, market, now)
                if row.status in (BroadcastStatus.REQUESTED, BroadcastStatus.REFUSED):
                    raise ConflictError(NOT_APPROVED_MESSAGE)
                previous = row.status
                if previous is BroadcastStatus.ENDED:
                    latest = await self._latest(uow, market.id, now)
                    if _runs_on(latest, row):
                        return await self._answer(uow, user, market, now)
                    if not self._view.resumable(row, latest, now):
                        raise _ended(row)
                self._require_streamable(market, now)
                await self._require_free_slot(uow, now)
                if previous is BroadcastStatus.ENDED:
                    live = await uow.broadcasts.add(
                        Broadcast(
                            market_id=row.market_id,
                            broadcaster_id=row.broadcaster_id,
                            status=BroadcastStatus.LIVE,
                            requested_at=now,
                            decided_by=row.decided_by,
                            decided_at=row.decided_at,
                            resumed_from=row.id,
                            started_at=now,
                            last_chunk_at=now,
                        )
                    )
                else:
                    live = row
                    live.status = BroadcastStatus.LIVE
                    live.started_at = now
                    live.last_chunk_at = now
                if user.is_admin:
                    await self._record(
                        uow,
                        user,
                        AuditAction.BROADCAST_START,
                        live,
                        before={"status": previous.value},
                        after={} if live is row else {"resumed_from": str(row.id)},
                    )
                uow.announce(self._to_the_market(market, now))
                answer = await self._answer(uow, user, market, now)
                await self._take_place(live.id, passage)
                return answer

        return await self._settling(write)

    # --- Sending ------------------------------------------------------------------

    async def admit_upload(self, user: AuthenticatedUser, broadcast_id: UUID) -> None:
        """Whether `user` may send a piece of this broadcast right now, judged before
        the route reads a byte of the body (`POST /broadcasts/{id}/chunks`).

        A refusal about the caller is `NotFoundError` or `ForbiddenError`, which the
        route answers without reading the body: an id no broadcast has, a broadcast
        of a market the caller is not inside of, and a caller who is neither its
        broadcaster nor an admin (404); an admin who is not its broadcaster (403,
        `NOT_THE_BROADCASTER_MESSAGE`); and the broadcaster of a broadcast they
        started as an admin who is no admin any more (403).

        A refusal about the status of the broadcast is `ConflictError`, which the
        route answers once it read the body and dropped it: ENDED (`ENDED_MESSAGE`,
        `details {status, end_reason}`), and REQUESTED, APPROVED or REFUSED
        (`NOT_STARTED_MESSAGE`, `details {status}`).
        """
        await self._uploading(user, broadcast_id)

    async def take_chunk(
        self, user: AuthenticatedUser, broadcast_id: UUID, part: str, seq: int, data: bytes
    ) -> BroadcastChunkResponse:
        """Hand one piece to the media box (`POST /broadcasts/{id}/chunks`).

        `data` is what the route read of the body: at most
        `BROADCAST_CHUNK_MAX_BYTES + 1` bytes, so one byte more than a piece may have
        says that the body is too large, and none says that it is empty.

        1. The caller and the broadcast are judged as `admit_upload` judges them, in
           a read transaction that is over before the box is asked.
        2. No byte: `ValidationFailedError` (`NO_DATA_MESSAGE`). More than
           `BROADCAST_CHUNK_MAX_BYTES`: `ValidationFailedError` (`TOO_LARGE_MESSAGE`).
           The box is not asked.
        3. The box takes the piece, or held it already: `restart` false and the
           `seq` to send next. A piece that does not continue its part: `restart`
           true, and the studio begins another part. A box whose queue is full, that
           has no place or does not answer: `ServiceUnavailableError`
           (`MEDIA_UNAVAILABLE_MESSAGE`), and the studio sends the piece again.
        4. A box that does not hold the broadcast started again since, or stopped the
           broadcast by its own timer. The broadcast is judged once more, so that one
           that ended in the meantime takes no place, and is given its place again.
           A piece that begins a part is then handed over again; every other one is
           `restart` true.
        5. After a piece the box holds, one transaction, and only when there is
           something to write: `last_chunk_at` moves with the first piece and then at
           most every `heartbeat_interval`, and the first answer of the box that says
           the broadcast is playable puts it on air (`on_air_at`), announced once.
        """
        row, market = await self._uploading(user, broadcast_id)
        if not data:
            raise ValidationFailedError(NO_DATA_MESSAGE)
        if len(data) > self._settings.chunk_max_bytes:
            raise ValidationFailedError(TOO_LARGE_MESSAGE)
        try:
            answer = await self._media.chunk(row.id, part, seq, data)
            if answer.refusal is ChunkRefusal.UNKNOWN:
                row, market = await self._uploading(user, broadcast_id)
                if not await self._media.start(row.id):
                    raise ServiceUnavailableError(MEDIA_UNAVAILABLE_MESSAGE)
                if seq == 0:
                    answer = await self._media.chunk(row.id, part, seq, data)
        except MediaUnavailableError as error:
            logger.warning("The media box took no piece of broadcast %s: %s", row.id, error)
            raise ServiceUnavailableError(MEDIA_UNAVAILABLE_MESSAGE) from None
        if answer.refusal is ChunkRefusal.BUSY:
            raise ServiceUnavailableError(MEDIA_UNAVAILABLE_MESSAGE)
        if answer.refusal is not None:
            return BroadcastChunkResponse(restart=True, next_seq=0)

        now = self._clock.now()
        last_chunk_at = _instant(row.last_chunk_at)
        goes_on_air = answer.playable and row.on_air_at is None
        # A row whose `last_chunk_at` is still the instant of its start took no piece.
        due = (
            last_chunk_at == row.started_at
            or now - last_chunk_at >= self._settings.heartbeat_interval
        )
        if goes_on_air or due:
            async with self._uow_factory() as uow:
                if await uow.broadcasts.heartbeat(row.id, now, on_air=goes_on_air):
                    uow.announce(self._to_the_market(market, now))
        return BroadcastChunkResponse(restart=False, next_seq=answer.next_seq)

    # --- Ending -------------------------------------------------------------------

    async def end(self, actor: AuthenticatedUser, broadcast_id: UUID) -> MarketBroadcastResponse:
        """End a broadcast as its broadcaster or as an admin
        (`POST /broadcasts/{id}/end`). LIVE -> ENDED, with the reason BROADCASTER for
        its broadcaster and ADMIN for anybody else.

        `NotFoundError` for an id no broadcast has, for a broadcast of a market the
        caller is not inside of, and for a caller who is neither its broadcaster nor
        an admin. A broadcast that is ENDED is answered again and nothing changes.
        One that is REQUESTED, APPROVED or REFUSED: `ConflictError`
        (`NOT_STARTED_MESSAGE`, `details {status}`).

        The transaction that stores the end clears the chat of the broadcast. Audit
        `BROADCAST_END` when the caller is an admin. After the commit the box is told
        to stop and the places of the viewers are given back; a box that does not
        answer ends the broadcast by its own timer.
        """
        ended: UUID | None = None

        async def write(_: _Passage) -> MarketBroadcastResponse:
            nonlocal ended
            async with self._uow_factory() as uow:
                row, market, now = await self._locked(uow, actor, broadcast_id)
                if row.broadcaster_id != actor.id and not actor.is_admin:
                    raise NotFoundError
                if row.status is BroadcastStatus.ENDED:
                    return await self._answer(uow, actor, market, now)
                if row.status is not BroadcastStatus.LIVE:
                    raise _not_started(row)
                row.status = BroadcastStatus.ENDED
                row.ended_at = now
                row.ended_by = actor.id
                row.end_reason = (
                    BroadcastEndReason.BROADCASTER
                    if row.broadcaster_id == actor.id
                    else BroadcastEndReason.ADMIN
                )
                await uow.flush()
                await self._on_end.clear(uow, row.id)
                if actor.is_admin:
                    await self._record(
                        uow,
                        actor,
                        AuditAction.BROADCAST_END,
                        row,
                        before={"status": BroadcastStatus.LIVE.value},
                        after={"end_reason": row.end_reason.value},
                    )
                uow.announce(self._to_the_market(market, now))
                answer = await self._answer(uow, actor, market, now)
                ended = row.id
                return answer

        answer = await self._settling(write)
        if ended is not None:
            self._audience.forget(ended)
            await self._stop_in_box(ended)
        return answer

    # --- An end the clock brought ---------------------------------------------------

    async def _settling[Answer](self, attempt: Callable[[_Passage], Awaitable[Answer]]) -> Answer:
        """Run `attempt` to its answer, storing on the way the end of every broadcast
        it finds over by the clock.

        `attempt` is one operation: its transaction, from the first read to the
        answer. It raises `_FoundOver` when it meets a row that is stored LIVE and is
        over, before it writes anything or asks the box. Each such end is stored in a
        transaction of its own, and the attempt starts again on what is stored.

        Whatever the attempt comes to, what follows its commits is done here, best
        effort: the box is told to stop every broadcast whose end was stored on the
        way, unless a start handed it to the box with its own request; and a start
        that did not commit gives the place it took in the box back.
        """
        passage = _Passage()
        try:
            for _ in range(SETTLE_ROUNDS):
                try:
                    return await attempt(passage)
                except _FoundOver as found:
                    for broadcast_id in found.broadcast_ids:
                        if await self._store_end(broadcast_id):
                            passage.ended.append(broadcast_id)
            logger.error("An operation on broadcasts did not settle in %d rounds", SETTLE_ROUNDS)
            raise ServiceUnavailableError
        except BaseException:
            # Also when the request is cancelled between the box's yes and the commit: a
            # place that is kept for a broadcast no row calls LIVE shuts every other
            # start out. Should the row be LIVE after all, its next piece takes the
            # place again.
            if passage.placed is not None:
                await self._stop_in_box(passage.placed)
            raise
        finally:
            for broadcast_id in passage.ended:
                if broadcast_id not in passage.handed_over:
                    await self._stop_in_box(broadcast_id)

    async def _store_end(self, broadcast_id: UUID) -> bool:
        """Store the end the clock brought to a broadcast, in a transaction of its own:
        ENDED from the instant and for the reason `BroadcastView.over` names, ended by
        nobody. The chat of the broadcast is cleared with it, and whoever is inside
        the market is told.

        The row is asked again once it is locked. False, and nothing is written, when
        it is not over then: another request stored the end, or a piece arrived in
        between.
        """
        async with self._uow_factory() as uow:
            await uow.advisory_lock(BROADCAST_LOCK)
            row = await uow.broadcasts.get_for_update(broadcast_id)
            now = self._clock.now()
            over = None if row is None else self._view.over(row, now)
            if row is None or over is None:
                return False
            # The end is nobody's request: no viewer stands behind this read, and
            # the event goes to whoever is inside the market.
            market = await uow.markets.get(row.market_id)
            if market is None:
                raise RuntimeError(f"broadcast {row.id} is of a market that does not exist")
            row.status = BroadcastStatus.ENDED
            row.end_reason, row.ended_at = over
            row.ended_by = None
            await uow.flush()
            await self._on_end.clear(uow, row.id)
            uow.announce(self._to_the_market(market, now))
        self._audience.forget(broadcast_id)
        return True

    def _require_settled(self, rows: Iterable[Broadcast | None], now: datetime) -> None:
        """`_FoundOver` when one of `rows` is stored LIVE and is over at `now`."""
        over = [row.id for row in rows if row is not None and self._view.over(row, now) is not None]
        if over:
            raise _FoundOver(over)

    # --- What the operations share --------------------------------------------------

    async def _locked(
        self, uow: UnitOfWork, caller: AuthenticatedUser, broadcast_id: UUID
    ) -> tuple[Broadcast, Market, datetime]:
        """The first steps of a write on one broadcast: the advisory lock, the row for
        update, the clock, and the market for a caller who is inside it. The row is
        settled: its stored status is its status at that instant.

        `NotFoundError` for an id no broadcast has and for a caller who is not inside
        its market.
        """
        await uow.advisory_lock(BROADCAST_LOCK)
        row = await uow.broadcasts.get_for_update(broadcast_id)
        if row is None:
            raise NotFoundError
        now = self._clock.now()
        market = await self._access.get_inside(uow, caller, row.market_id)
        self._require_settled((row,), now)
        return row, market, now

    async def _latest(self, uow: UnitOfWork, market_id: UUID, now: datetime) -> Broadcast | None:
        """The latest broadcast of a market, settled."""
        latest = await uow.broadcasts.latest_for_market(market_id)
        self._require_settled((latest,), now)
        return latest

    async def _running(self, uow: UnitOfWork, now: datetime) -> Sequence[Broadcast]:
        """Every broadcast that is LIVE at `now`, earliest start first, settled."""
        running = await uow.broadcasts.list_live()
        self._require_settled(running, now)
        return running

    async def _require_free_slot(self, uow: UnitOfWork, now: datetime) -> None:
        """`LimitExceededError` while `BROADCAST_MAX_LIVE` broadcasts run. The sentence
        names no market: the caller may not be inside the one that is on."""
        if len(await self._running(uow, now)) >= self._settings.max_live:
            raise LimitExceededError(
                SLOT_TAKEN_MESSAGE, details={"limit": Limit.MAX_LIVE_BROADCASTS}
            )

    def _require_streamable(self, market: Market, now: datetime) -> None:
        if not self._view.streamable(self._market_view.effective_status(market, now)):
            raise ConflictError(NOT_STREAMABLE_MESSAGE)

    async def _uploading(
        self, user: AuthenticatedUser, broadcast_id: UUID
    ) -> tuple[Broadcast, Market]:
        """The LIVE broadcast `user` may send a piece of right now, with its market:
        the judgement of `admit_upload`, in a read transaction of its own."""

        async def read(_: _Passage) -> tuple[Broadcast, Market]:
            async with self._uow_factory() as uow:
                now = self._clock.now()
                row = await uow.broadcasts.get(broadcast_id)
                if row is None:
                    raise NotFoundError
                market = await self._access.get_inside(uow, user, row.market_id)
                if row.broadcaster_id != user.id:
                    if user.is_admin:
                        raise ForbiddenError(NOT_THE_BROADCASTER_MESSAGE)
                    raise NotFoundError
                if row.decided_by == row.broadcaster_id and not user.is_admin:
                    # Started as an admin by somebody who is none any more.
                    raise ForbiddenError
                self._require_settled((row,), now)
                if row.status is BroadcastStatus.ENDED:
                    raise _ended(row)
                if row.status is not BroadcastStatus.LIVE:
                    raise _not_started(row)
                return row, market

        return await self._settling(read)

    async def _answer(
        self, uow: UnitOfWork, viewer: AuthenticatedUser, market: Market, now: datetime
    ) -> MarketBroadcastResponse:
        """The live stream of `market` as `viewer`, who is inside it, sees it at `now`:
        what `GET /markets/{id}/broadcast` answers and what every write answers, read
        in the caller's transaction."""
        latest = await self._latest(uow, market.id, now)
        shown = latest if self._view.shown_to(viewer, latest, now) else None
        broadcaster = (
            await uow.users.get(shown.broadcaster_id)
            if shown is not None and viewer.is_admin
            else None
        )
        return MarketBroadcastResponse(
            broadcast=None if shown is None else self._view.item(shown, now, viewer, broadcaster),
            viewer=self._view.viewer(
                viewer,
                market,
                self._market_view.effective_status(market, now),
                latest,
                now,
                has_place=(
                    latest is not None and self._audience.has_place_for(latest.id, viewer.id)
                ),
            ),
        )

    async def _record(
        self,
        uow: UnitOfWork,
        actor: AuthenticatedUser,
        action: AuditAction,
        row: Broadcast,
        *,
        before: dict[str, str] | None,
        after: dict[str, str],
    ) -> None:
        """The audit row of what an admin did to a broadcast: `after` holds the status
        the action leaves, the market, and what the caller adds."""
        await self._audit.record(
            uow,
            actor_id=actor.id,
            action=action,
            target=AuditService.target(AuditTarget.BROADCAST, row.id),
            before=before,
            after={"status": row.status.value, "market_id": str(row.market_id), **after},
        )

    # --- Told to the open tabs ------------------------------------------------------

    def _tell_broadcaster_and_admins(self, uow: UnitOfWork, row: Broadcast, now: datetime) -> None:
        """Announce a request, an approval or a refusal: to its broadcaster on the
        topic of the market's broadcast, and to the admins on that topic and on the
        market lists, where their queue of requests is read."""
        changed = BroadcastChangedEvent(type="broadcast.changed", market_id=row.market_id, ts=now)
        of_the_market = Topic(TopicKind.BROADCAST, row.market_id)
        uow.announce(LiveEvent((of_the_market,), Audience.users({row.broadcaster_id}), changed))
        uow.announce(
            LiveEvent((of_the_market, Topic(TopicKind.MARKETS)), Audience.admins(), changed)
        )

    def _to_the_market(self, market: Market, now: datetime) -> LiveEvent:
        """`broadcast.changed` for whoever is inside `market`: a start, the moment a
        broadcast is on air, an end. On the topic of the market's broadcast and on the
        market lists, where the broadcasts that are on air are listed."""
        return self._events.about(
            market,
            (Topic(TopicKind.BROADCAST, market.id), Topic(TopicKind.MARKETS)),
            BroadcastChangedEvent(type="broadcast.changed", market_id=market.id, ts=now),
        )

    # --- The media box ----------------------------------------------------------------

    async def _take_place(self, broadcast_id: UUID, passage: _Passage) -> None:
        """Take a place in the box for a broadcast that goes live, as the last step of
        its transaction. The broadcasts whose end this operation stored on its way
        give theirs up with the same request, so the box swaps them without going
        away in between.

        `ServiceUnavailableError` when the box does not answer or has no place: the
        transaction is rolled back and nothing is written.
        """
        hand_over = [ended for ended in passage.ended if ended not in passage.handed_over]
        hand_over = hand_over[-STOP_IDS:]
        try:
            placed = await self._media.start(broadcast_id, stop=hand_over)
        except MediaUnavailableError as error:
            logger.warning("The media box gave broadcast %s no place: %s", broadcast_id, error)
            raise ServiceUnavailableError(MEDIA_UNAVAILABLE_MESSAGE) from None
        # The box answered: whatever it said, the broadcasts it was handed are stopped.
        passage.handed_over.update(hand_over)
        if not placed:
            raise ServiceUnavailableError(MEDIA_UNAVAILABLE_MESSAGE)
        passage.placed = broadcast_id

    async def _stop_in_box(self, broadcast_id: UUID) -> None:
        """Tell the box to end a broadcast, after the commit that ended it. Best
        effort: a box that does not answer ends the broadcast by its own timer, and
        the request this follows stands."""
        try:
            await self._media.stop(broadcast_id)
        except MediaUnavailableError as error:
            logger.warning(
                "The media box was not told to stop broadcast %s: %s", broadcast_id, error
            )


def _require_admin(actor: AuthenticatedUser) -> None:
    if not actor.is_admin:
        raise ForbiddenError


def _runs_on(latest: Broadcast | None, ended: Broadcast) -> bool:
    """Whether `latest` is the LIVE broadcast that goes on from `ended`: the row a
    start of `ended` wrote."""
    return (
        latest is not None
        and latest.status is BroadcastStatus.LIVE
        and latest.resumed_from == ended.id
    )


def _ended(row: Broadcast) -> ConflictError:
    return ConflictError(
        ENDED_MESSAGE,
        details={"status": BroadcastStatus.ENDED, "end_reason": row.end_reason},
    )


def _not_started(row: Broadcast) -> ConflictError:
    return ConflictError(NOT_STARTED_MESSAGE, details={"status": row.status})


def _instant(value: datetime | None) -> datetime:
    """An instant a LIVE row always holds (`ck_broadcasts_live_has_started`)."""
    if value is None:
        raise ValueError("a broadcast that started has the instants of its start")
    return value
