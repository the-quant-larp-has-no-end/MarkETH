"""The API's route tree (SPEC §10, Appendix A.2). Everything sits under `/api`.

Who may call what is decided here once, per prefix: `/admin/*` needs role ADMIN and
`/barista/*` role BARISTA or ADMIN. The other routes guard themselves through the type
of their user parameter (`CurrentUser`, or `OptionalUser` on public routes). Whatever
guard a route ends up with is what the OpenAPI document says of it (`app/api/access.py`).

Every operation declares `ErrorResponse` as its error body. That replaces FastAPI's own
422 schema in the OpenAPI document, so the generated client knows the one error shape.
"""

from typing import Any, Final

from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_staff
from app.api.routes import (
    auth,
    barista,
    coffee_tickets,
    config,
    health,
    live,
    markets,
    me,
    trading,
    vouchers,
)
from app.api.routes.admin import code_batches as admin_code_batches
from app.api.routes.admin import grants as admin_grants
from app.api.routes.admin import markets as admin_markets
from app.api.routes.admin import risk as admin_risk
from app.schemas.common import ErrorResponse

API_PREFIX: Final[str] = "/api"

ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    "default": {
        "model": ErrorResponse,
        "description": "Any error: `code` says which, `message` is for the user.",
    }
}

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
admin_router.include_router(admin_markets.router)
admin_router.include_router(admin_code_batches.router)
admin_router.include_router(admin_risk.router)
admin_router.include_router(admin_grants.router)

api_router = APIRouter(prefix=API_PREFIX, responses=ERROR_RESPONSES)
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(markets.router)
api_router.include_router(trading.router)
api_router.include_router(live.router)
api_router.include_router(vouchers.router)
api_router.include_router(coffee_tickets.router)
api_router.include_router(barista.router, dependencies=[Depends(require_staff)])
api_router.include_router(admin_router)
