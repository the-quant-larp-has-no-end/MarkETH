"""`GET /health`: is each data store answering, and the media box (SPEC §9, Appendix
A.2, A.10).

Compose uses this as the API's healthcheck. Only Postgres decides the verdict: with
Redis, ScyllaDB or the media box down the API still trades, so it stays healthy and
reports the outage. Without the media box no live stream starts or runs, and the
operations of a live stream say so themselves (503): this answer is no gate for them.
"""

import asyncio
from typing import Final

from app.core.errors import ServiceUnavailableError
from app.db.database import Database
from app.integrations.media.client import MediaClient
from app.integrations.redis.client import RedisClient
from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.schemas.health import DependencyStatus, HealthResponse, HealthStatus

SCYLLA_PROBE: Final[str] = "SELECT now() FROM system.local"
"""Answered by any node, with or without the keyspace the relay creates."""


class HealthService:
    def __init__(
        self, database: Database, redis: RedisClient, scylla: ScyllaClient, media: MediaClient
    ) -> None:
        self._database = database
        self._redis = redis
        self._scylla = scylla
        self._media = media

    async def check(self) -> HealthResponse:
        """Probe the three stores and the media box at once and report each.

        Postgres up: `status` is `ok` when Redis, ScyllaDB and the media box answer
        too, else `degraded`. Postgres down: raises `ServiceUnavailableError` (503)
        whose details carry the four statuses. The media box never decides between the
        two: it is `up` or `down` in either answer. Each probe is bounded by its
        client's own timeout, so the slowest answer takes about one ScyllaDB request
        timeout or one `BROADCAST_MEDIA_TIMEOUT_MS`, whichever is longer, while the
        pool has a connection free. While every connection of the pool is in use, the
        probe of Postgres waits for one like any request,
        `POSTGRES_POOL_TIMEOUT_SECONDS` at most, and then reports Postgres down.
        """
        postgres, redis, scylla, media = await asyncio.gather(
            self._database.ping(), self._redis.ping(), self._scylla_answers(), self._media.ping()
        )
        statuses = {
            "postgres": _status(postgres),
            "redis": _status(redis),
            "scylla": _status(scylla),
            "media": _status(media),
        }
        if not postgres:
            raise ServiceUnavailableError(
                "The database is unreachable.",
                details={name: status.value for name, status in statuses.items()},
            )
        overall = HealthStatus.OK if redis and scylla and media else HealthStatus.DEGRADED
        return HealthResponse(status=overall, **statuses)

    async def _scylla_answers(self) -> bool:
        try:
            await self._scylla.execute(SCYLLA_PROBE)
        except ScyllaUnavailableError:
            return False
        return True


def _status(is_up: bool) -> DependencyStatus:
    return DependencyStatus.UP if is_up else DependencyStatus.DOWN
