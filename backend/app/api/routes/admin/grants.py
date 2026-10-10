"""Booking every owed monthly grant at once (SPEC §2, Appendix A.2)."""

from fastapi import APIRouter

from app.api.deps import AdminUser, GrantServiceDep
from app.schemas.admin.grants import MonthlyGrantRunResponse

router = APIRouter(prefix="/grants", tags=["admin-grants"])


@router.post("/monthly", summary="Book every owed monthly grant for every user")
async def run_monthly_grants(admin: AdminUser, grants: GrantServiceDep) -> MonthlyGrantRunResponse:
    return await grants.run_monthly(admin)
