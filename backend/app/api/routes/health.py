"""`GET /health` (Appendix A.2): the compose healthcheck."""

from fastapi import APIRouter

from app.api.deps import HealthServiceDep
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", summary="Report Postgres, Redis, ScyllaDB and the media box separately")
async def get_health(health: HealthServiceDep) -> HealthResponse:
    return await health.check()
