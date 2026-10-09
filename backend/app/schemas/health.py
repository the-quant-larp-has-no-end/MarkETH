"""`GET /health` (Appendix A.2): the three data stores, reported separately."""

from enum import StrEnum

from app.schemas.common import ResponseModel


class DependencyStatus(StrEnum):
    UP = "up"
    DOWN = "down"


class HealthStatus(StrEnum):
    OK = "ok"
    """Postgres, Redis and ScyllaDB all answer."""

    DEGRADED = "degraded"
    """Postgres answers; Redis or ScyllaDB does not. Trading works (SPEC §9)."""


class HealthResponse(ResponseModel):
    """Answered with 200 whenever Postgres is up.

    With Postgres down the endpoint answers 503 `SERVICE_UNAVAILABLE` instead, and
    `details` carries the three `postgres`, `redis` and `scylla` values.
    """

    status: HealthStatus
    postgres: DependencyStatus
    redis: DependencyStatus
    scylla: DependencyStatus
