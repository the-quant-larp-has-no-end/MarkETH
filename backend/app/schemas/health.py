"""`GET /health` (Appendix A.2): the three data stores and the media box, reported
separately."""

from enum import StrEnum

from app.schemas.common import ResponseModel


class DependencyStatus(StrEnum):
    UP = "up"
    DOWN = "down"


class HealthStatus(StrEnum):
    OK = "ok"
    """Postgres, Redis, ScyllaDB and the media box all answer."""

    DEGRADED = "degraded"
    """Postgres answers; Redis, ScyllaDB or the media box does not. Trading works
    (SPEC §9)."""


class HealthResponse(ResponseModel):
    """Answered with 200 whenever Postgres is up.

    With Postgres down the endpoint answers 503 `SERVICE_UNAVAILABLE` instead, and
    `details` carries the four `postgres`, `redis`, `scylla` and `media` values.
    """

    status: HealthStatus
    postgres: DependencyStatus
    redis: DependencyStatus
    scylla: DependencyStatus
    media: DependencyStatus
    """The media box, which turns what a live stream uploads into what its viewers
    play. While it is down no live stream starts or runs; everything else works."""
