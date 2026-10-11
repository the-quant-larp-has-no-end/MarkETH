"""The broadcasts the box holds, and the four things that can be asked of it (SPEC §9).

The box decides nothing about people: whoever reaches its socket from outside is the API.
It holds at most BROADCAST_MAX_LIVE broadcasts and empties itself when the API never
calls (the two timers).

Every broadcast begins in a box that nothing was done to. When the last broadcast it
holds is stopped, the process ends and Compose starts the container again. When a start
hands another broadcast over (`stop`), the process stays; if the box is empty at that
moment, every other process of the container is killed before the place is given away.
"""

import asyncio
import time
from collections.abc import Callable, Iterable
from typing import Final

from mediabox.broadcast import Answer, Broadcast, Clock, Log, Spawn, spawn_ffmpeg
from mediabox.settings import Settings

FULL: Final[Answer] = {"ok": False, "error": "full"}
UNKNOWN: Final[Answer] = {"ok": False, "error": "unknown"}
OK: Final[Answer] = {"ok": True}

# How often the two timers of every broadcast are looked at.
TIMER_SECONDS: Final = 1.0


class Box:
    def __init__(
        self,
        settings: Settings,
        *,
        log: Log,
        clock: Clock = time.monotonic,
        spawn: Spawn = spawn_ffmpeg,
        clear_out: Callable[[], None] | None = None,
    ) -> None:
        """`clear_out` kills every process of the container but this one; None: nothing to do."""
        self._settings = settings
        self._log = log
        self._clock = clock
        self._spawn = spawn
        self._clear_out = clear_out
        self._broadcasts: dict[str, Broadcast] = {}
        # True from the moment the last broadcast is being stopped: nothing is answered any more.
        self.ending = False
        # Set when that stop is done: the process ends.
        self.finished = asyncio.Event()

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def log(self) -> Log:
        return self._log

    def status(self) -> Answer:
        return {
            "ok": True,
            "capacity": self._settings.max_live,
            "live": [broadcast.describe() for broadcast in self._broadcasts.values()],
        }

    async def start(self, broadcast_id: str, stop: Iterable[str]) -> Answer:
        """Takes a place for a broadcast. The ids of `stop` give theirs up first."""
        handed_over = [
            self._broadcasts.pop(other)
            for other in dict.fromkeys(stop)
            if other != broadcast_id and other in self._broadcasts
        ]
        if handed_over:
            for broadcast in handed_over:
                self._log("stop", id=broadcast.id, reason="handed over")
            await asyncio.gather(*(broadcast.close() for broadcast in handed_over))
            if not self._broadcasts and self._clear_out is not None:
                self._clear_out()
                self._log("cleared_out")
        if broadcast_id in self._broadcasts:
            return OK
        if len(self._broadcasts) >= self._settings.max_live:
            return FULL
        self._broadcasts[broadcast_id] = Broadcast(
            broadcast_id, self._settings, clock=self._clock, spawn=self._spawn, log=self._log
        )
        self._log("start", id=broadcast_id)
        return OK

    def chunk(self, broadcast_id: str, part: str, seq: int, data: bytes) -> Answer:
        broadcast = self._broadcasts.get(broadcast_id)
        if broadcast is None:
            return UNKNOWN
        return broadcast.take(part, seq, data)

    async def stop(self, broadcast_id: str, *, reason: str = "asked") -> Answer:
        """Ends a broadcast and deletes its files. Fine for an id the box does not hold."""
        broadcast = self._broadcasts.pop(broadcast_id, None)
        if broadcast is None:
            return OK
        self._log("stop", id=broadcast_id, reason=reason)
        last = not self._broadcasts
        if last:
            self.ending = True
        await broadcast.close()
        if last:
            self.finished.set()
        return OK

    async def check_timers(self) -> None:
        """Stops what the API should have stopped: a silent broadcast, and one past its time limit.

        The same look ends an ffmpeg that is stuck, which the API cannot see.
        """
        now = self._clock()
        age_limit = (self._settings.max_minutes + 1) * 60
        for broadcast in list(self._broadcasts.values()):
            if now - broadcast.last_taken_at >= self._settings.stall_seconds:
                await self.stop(broadcast.id, reason="stalled")
            elif now - broadcast.started_at >= age_limit:
                await self.stop(broadcast.id, reason="time limit")
            else:
                await broadcast.check_encoder()

    async def run_timers(self) -> None:
        while not self.finished.is_set():
            await asyncio.sleep(TIMER_SECONDS)
            await self.check_timers()

    async def shut_down(self) -> None:
        """SIGTERM: every ffmpeg is killed and every file deleted before the process goes."""
        self.ending = True
        broadcasts = list(self._broadcasts.values())
        self._broadcasts.clear()
        for broadcast in broadcasts:
            self._log("stop", id=broadcast.id, reason="shut down")
        await asyncio.gather(*(broadcast.close() for broadcast in broadcasts))
        self.finished.set()
