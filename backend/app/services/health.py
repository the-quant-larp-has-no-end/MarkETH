"""`GET /health`: is each data store answering (SPEC §9, Appendix A.2, A.10).

Compose uses this as the API's healthcheck. Only Postgres decides the verdict: with
Redis or ScyllaDB down the API still trades, so it stays healthy and reports the outage.
"""

import asyncio
from typing import Final

from app.core.errors import ServiceUnavailableError
from app.db.database import Database
from app.integrations.redis.client import RedisClient
from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.schemas.health import DependencyStatus, HealthResponse, HealthStatus

SCYLLA_PROBE: Final[str] = "SELECT now() FROM system.local"
"""Answered by any node, with or without the keyspace the relay creates."""


class HealthService:
    def __init__(self, database: Database, redis: RedisClient, scylla: ScyllaClient) -> None:
        self._database = database
        self._redis = redis
        self._scylla = scylla

    async def check(self) -> HealthResponse:
        """Probe the three stores at once and report each.

        Postgres up: `status` is `ok` when Redis and ScyllaDB answer too, else
        `degraded`. Postgres down: raises `ServiceUnavailableError` (503) whose details
        carry the three statuses. Each probe is bounded by its client's own timeout, so
        the slowest answer takes about one ScyllaDB request timeout.
        """
        postgres, redis, scylla = await asyncio.gather(
            self._database.ping(), self._redis.ping(), self._scylla_answers()
        )
        statuses = {
            "postgres": _status(postgres),
            "redis": _status(redis),
            "scylla": _status(scylla),
        }
        if not postgres:
            raise ServiceUnavailableError(
                "The database is unreachable.",
                details={name: status.value for name, status in statuses.items()},
            )
        overall = HealthStatus.OK if redis and scylla else HealthStatus.DEGRADED
        return HealthResponse(status=overall, **statuses)

    async def _scylla_answers(self) -> bool:
        try:
            await self._scylla.execute(SCYLLA_PROBE)
        except ScyllaUnavailableError:
            return False
        return True


def _status(is_up: bool) -> DependencyStatus:
    return DependencyStatus.UP if is_up else DependencyStatus.DOWN
