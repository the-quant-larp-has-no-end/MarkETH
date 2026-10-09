"""`GET /config` (Appendix A.2, A.11): public tunables for the UI."""

from fastapi import APIRouter

from app.api.deps import PublicConfigServiceDep
from app.schemas.config import PublicConfigResponse

router = APIRouter(tags=["config"])


@router.get("/config", summary="Public tunables the UI needs")
async def get_config(config: PublicConfigServiceDep) -> PublicConfigResponse:
    return config.get()
