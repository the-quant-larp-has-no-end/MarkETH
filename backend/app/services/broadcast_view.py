"""What a broadcast is at an instant, worked out from its row (SPEC §6).

A row says REQUESTED, APPROVED, REFUSED, LIVE or ENDED. One thing the row does not say
by itself: a LIVE broadcast is over from the earlier of two instants on, whoever looks
and whether or not anything was written since.

    started_at + BROADCAST_MAX_MINUTES          it ran its time           TIME_LIMIT
    last_chunk_at + BROADCAST_STALL_SECONDS     its studio sent nothing   STALLED

No job stores that end. `BroadcastView` reports it wherever a status is reported, and
the first request that meets such a row stores it (`BroadcastService`).

A broadcast is **open** while its effective status is REQUESTED, APPROVED or LIVE, and
**on air** while it is LIVE and has something to play (`on_air_at` is set). A market is
**streamable** while its effective status is OPEN, HALTED or CLOSED: a live stream is
asked for, approved and started there, and one that runs goes on whatever becomes of
its market.

`BroadcastView` reads rows and settings and computes; it does no I/O.
"""

from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import BroadcastEndReason, BroadcastStatus, MarketStatus
from app.core.identity import AuthenticatedUser
from app.models.broadcast import Broadcast
from app.models.market import Market
from app.models.user import User
from app.repositories.broadcast import OPEN_STATUSES
from app.repositories.unit_of_work import UnitOfWork
from app.schemas.broadcasts import BroadcastItem, BroadcastPerson, BroadcastViewer

STREAMABLE_STATUSES: Final[frozenset[MarketStatus]] = frozenset(
    {MarketStatus.OPEN, MarketStatus.HALTED, MarketStatus.CLOSED}
)
"""The effective statuses of a market that can have a live stream: open, paused, or
waiting for its result."""

RESUMABLE_END_REASONS: Final[frozenset[BroadcastEndReason]] = frozenset(
    {BroadcastEndReason.STALLED, BroadcastEndReason.TIME_LIMIT}
)
"""The ends after which a broadcaster goes live again on the approval they have: the
broadcast ended by itself. After an end by a person the proposer asks again."""

PLAYLIST_PATH: Final[str] = "/api/broadcasts/{broadcast_id}/hls/index.m3u8"
"""Where the proxy serves the playlist of a broadcast, to a caller the API admitted."""


class BroadcastView:
    """The rules that read a broadcast and write nothing. No I/O."""

    def __init__(self, settings: Settings) -> None:
        self._max_duration = timedelta(minutes=settings.broadcast.max_minutes)
        self._stall = timedelta(seconds=settings.broadcast.stall_seconds)

    def over(self, row: Broadcast, now: datetime) -> tuple[BroadcastEndReason, datetime] | None:
        """Why and since when a row stored LIVE is over by itself at `now`: no piece
        for `BROADCAST_STALL_SECONDS`, or `BROADCAST_MAX_MINUTES` on. None when it is
        not over, and for a row that is not stored LIVE.

        The earlier of the two instants is the end, with its reason. Where both fall
        on one instant the time limit is the reason.
        """
        if row.status is not BroadcastStatus.LIVE:
            return None
        if row.started_at is None or row.last_chunk_at is None:
            raise ValueError(f"broadcast {row.id} is LIVE without the instants of its start")
        time_limit_at = row.started_at + self._max_duration
        stalled_at = row.last_chunk_at + self._stall
        reason, instant = (
            (BroadcastEndReason.TIME_LIMIT, time_limit_at)
            if time_limit_at <= stalled_at
            else (BroadcastEndReason.STALLED, stalled_at)
        )
        return (reason, instant) if now >= instant else None

    def effective_status(self, row: Broadcast, now: datetime) -> BroadcastStatus:
        """The status of `row` at `now`: what the row says, except that a LIVE row
        that is over is ENDED."""
        return BroadcastStatus.ENDED if self.over(row, now) is not None else row.status

    def is_open(self, row: Broadcast | None, now: datetime) -> bool:
        """Whether `row` is asked for, approved or running at `now`. No row is not."""
        return row is not None and self.effective_status(row, now) in OPEN_STATUSES

    def is_on_air(self, row: Broadcast, now: datetime) -> bool:
        """Whether `row` is LIVE at `now` and has something to play."""
        return self.effective_status(row, now) is BroadcastStatus.LIVE and row.on_air_at is not None

    def streamable(self, market_status: MarketStatus) -> bool:
        """Whether a market whose effective status is `market_status`
        (`MarketView.effective_status`) can have a live stream."""
        return market_status in STREAMABLE_STATUSES

    def resumable(self, row: Broadcast, latest: Broadcast | None, now: datetime) -> bool:
        """Whether the broadcaster of `row` goes live again without asking: `row` is
        the market's latest broadcast, it ended by itself, and an admin other than its
        broadcaster had decided on it. `latest` is the latest broadcast of the market
        of `row`.

        An admin's own broadcast is not among them: an admin starts another one.
        """
        if latest is None or latest.id != row.id:
            return False
        if self.effective_status(row, now) is not BroadcastStatus.ENDED:
            return False
        return self._end_of(row, now)[0] in RESUMABLE_END_REASONS and (
            row.decided_by is not None and row.decided_by != row.broadcaster_id
        )

    def playlist_url(self, row: Broadcast) -> str:
        """Where the viewers of `row` play it: `/api/broadcasts/{id}/hls/index.m3u8`."""
        return PLAYLIST_PATH.format(broadcast_id=row.id)

    def shown_to(self, viewer: AuthenticatedUser, row: Broadcast | None, now: datetime) -> bool:
        """Whether `viewer`, who is inside the market, is told about its latest
        broadcast `row`: an admin and its broadcaster always, everybody else while it
        is LIVE."""
        if row is None:
            return False
        return (
            viewer.is_admin
            or row.broadcaster_id == viewer.id
            or self.effective_status(row, now) is BroadcastStatus.LIVE
        )

    def item(
        self,
        row: Broadcast,
        now: datetime,
        viewer: AuthenticatedUser,
        broadcaster: User | None,
    ) -> BroadcastItem:
        """`row` as `viewer` is told about it at `now`.

        `refusal_reason` goes to its broadcaster and to admins, `requested_by` to
        admins: `broadcaster` is the `users` row of the broadcaster, which only an
        admin's answer needs.
        """
        status = self.effective_status(row, now)
        end_reason, ended_at = self._end_of(row, now)
        on_air = self.is_on_air(row, now)
        own = row.broadcaster_id == viewer.id
        requested_by = None
        if viewer.is_admin:
            if broadcaster is None or broadcaster.id != row.broadcaster_id:
                raise ValueError(f"an admin's answer needs the broadcaster of broadcast {row.id}")
            requested_by = BroadcastPerson(username=broadcaster.username, email=broadcaster.email)
        return BroadcastItem(
            id=row.id,
            market_id=row.market_id,
            status=status,
            on_air=on_air,
            playlist_url=self.playlist_url(row) if on_air else None,
            requested_at=row.requested_at,
            started_at=row.started_at,
            ended_at=ended_at,
            end_reason=end_reason,
            refusal_reason=row.refusal_reason if viewer.is_admin or own else None,
            requested_by=requested_by,
        )

    def viewer(
        self,
        viewer: AuthenticatedUser,
        market: Market,
        market_status: MarketStatus,
        latest: Broadcast | None,
        now: datetime,
        *,
        has_place: bool,
    ) -> BroadcastViewer:
        """What `viewer`, who is inside `market`, may do about its live stream at
        `now`. Each flag is what the operation would say about this caller and this
        market; how many broadcasts run at once and how often a person asks in a day
        is the operation's alone to say.

        `market_status` is the effective status of `market` and `latest` its latest
        broadcast. `has_place` says whether a place to watch is free for the viewer or
        held by them, asked without taking one.
        """
        streamable = self.streamable(market_status)
        status = None if latest is None else self.effective_status(latest, now)
        is_open = status in OPEN_STATUSES
        own = latest is not None and latest.broadcaster_id == viewer.id
        on_air = latest is not None and self.is_on_air(latest, now)
        return BroadcastViewer(
            is_broadcaster=own,
            can_request=(
                market.created_by == viewer.id
                and not viewer.is_admin
                and streamable
                and not is_open
            ),
            can_start=(
                latest is not None
                and own
                and streamable
                and (status is BroadcastStatus.APPROVED or self.resumable(latest, latest, now))
            ),
            can_start_as_admin=viewer.is_admin and streamable and not is_open,
            can_approve=viewer.is_admin and status is BroadcastStatus.REQUESTED and streamable,
            can_refuse=(
                viewer.is_admin and status in (BroadcastStatus.REQUESTED, BroadcastStatus.APPROVED)
            ),
            can_end=status is BroadcastStatus.LIVE and (own or viewer.is_admin),
            can_watch=on_air and has_place,
            full=on_air and not has_place,
        )

    def _end_of(
        self, row: Broadcast, now: datetime
    ) -> tuple[BroadcastEndReason | None, datetime | None]:
        """Why and when `row` ended, as of `now`: what the row stores, or what the
        clock says of a LIVE row that is over. Two times None for a row that has not
        ended."""
        over = self.over(row, now)
        return (row.end_reason, row.ended_at) if over is None else over


class BroadcastLookup:
    """Which broadcast of a market is LIVE: what the live chat asks."""

    def __init__(self, view: BroadcastView, clock: Clock) -> None:
        self._view = view
        self._clock = clock

    async def live_broadcast_id(self, uow: UnitOfWork, market_id: UUID) -> UUID | None:
        """The id of the market's broadcast whose effective status is LIVE, or None.
        It runs in the caller's transaction, locks nothing and writes nothing: a
        broadcast that is over by the clock is not LIVE here, whether or not its end
        is stored yet."""
        row = await uow.broadcasts.live_for_market(market_id)
        if row is None or self._view.over(row, self._clock.now()) is not None:
            return None
        return row.id
