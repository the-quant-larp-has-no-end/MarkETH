"""The places of a broadcast's viewers, counted in this process (SPEC §6).

At most `BROADCAST_MAX_VIEWERS` people watch one broadcast. The proxy asks the API for
every viewer before it serves a playlist or a segment, and remembers a yes for
`BROADCAST_ACCESS_CACHE_SECONDS`; each yes gives its viewer a place, or keeps the one
they hold. A place is given back when no yes followed for `seat_seconds`: as long as
the proxy remembers the last one, and as long as the segments of a playlist play. A
viewer who stops watching therefore frees their place by doing nothing.

The places are a dictionary of this process. The stack runs one API process, so the
count is exact; a process that starts again counts afresh, and every viewer who is
still watching has a place again with their next request. Nobody is told the count.
"""

from datetime import datetime, timedelta
from uuid import UUID

from app.core.clock import Clock
from app.core.config import Settings


class BroadcastAudience:
    """Who holds one of the `BROADCAST_MAX_VIEWERS` places of a broadcast. The stack
    runs one API process, so the count is exact. Nobody is told it."""

    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._max_viewers = settings.broadcast.max_viewers
        self._seat = timedelta(seconds=settings.broadcast.seat_seconds)
        self._clock = clock
        self._admitted: dict[UUID, dict[UUID, datetime]] = {}
        """Per broadcast: who holds a place, and when they were last admitted."""

    def admit(self, broadcast_id: UUID, user_id: UUID) -> bool:
        """Give `user_id` a place, or keep the one they hold. False when every place is
        held by somebody else."""
        now = self._clock.now()
        places = self._held(broadcast_id, now)
        if user_id not in places and len(places) >= self._max_viewers:
            return False
        self._admitted.setdefault(broadcast_id, places)[user_id] = now
        return True

    def holds(self, broadcast_id: UUID, user_id: UUID) -> bool:
        """Whether `user_id` holds a place. It takes none."""
        return user_id in self._held(broadcast_id, self._clock.now())

    def has_place_for(self, broadcast_id: UUID, user_id: UUID) -> bool:
        """Whether `admit` would say yes to `user_id` right now. It takes no place."""
        places = self._held(broadcast_id, self._clock.now())
        return user_id in places or len(places) < self._max_viewers

    def count(self, broadcast_id: UUID) -> int:
        """How many places of the broadcast are held."""
        return len(self._held(broadcast_id, self._clock.now()))

    def forget(self, broadcast_id: UUID) -> None:
        """Give every place of the broadcast back: it has ended."""
        self._admitted.pop(broadcast_id, None)

    def _held(self, broadcast_id: UUID, now: datetime) -> dict[UUID, datetime]:
        """The places of a broadcast that are held at `now`, after the ones nobody
        asked for in `seat_seconds` were given back. A broadcast nobody watches keeps
        no entry."""
        places = self._admitted.get(broadcast_id)
        if places is None:
            return {}
        for user_id in [user for user, last in places.items() if now - last >= self._seat]:
            del places[user_id]
        if not places:
            del self._admitted[broadcast_id]
        return places
