"""The risk page and its alerts (SPEC §5, §10, Appendix A.2)."""

from typing import Annotated, Final

from fastapi import APIRouter, Path

from app.api.deps import AdminUser, RiskServiceDep
from app.schemas.admin.risk import AlertItem, RiskResponse

router = APIRouter(tags=["admin-risk"])

BIGINT_MAX: Final[int] = 2**63 - 1
"""`alerts.id` is a BIGINT; a larger number cannot name an alert."""


@router.get("/risk", summary="Open markets, loss budgets, outstanding obligations and alerts")
async def get_risk(risk: RiskServiceDep) -> RiskResponse:
    return await risk.snapshot()


@router.post("/alerts/{alert_id}/ack", summary="Acknowledge an alert")
async def acknowledge_alert(
    alert_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)], admin: AdminUser, risk: RiskServiceDep
) -> AlertItem:
    return await risk.acknowledge_alert(admin, alert_id)
