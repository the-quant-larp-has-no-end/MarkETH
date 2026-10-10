"""The API's route tree (SPEC §10, Appendix A.2). Everything sits under `/api`.

Who may call what is decided here once, by where a route module is included:

* `/health` and `/config` are public.
* `PUT /me/profile` takes whoever the portal named: it is how a person gets a profile.
* Everything under `member_router` needs a profile whose account is active. On top of
  that `/barista/*` needs role BARISTA or ADMIN and `/admin/*` role ADMIN.

A route added to a module of `member_router` is guarded without anyone remembering to
guard it, and the guard runs before every other dependency of the route. Whatever guard
a route ends up with is what the OpenAPI document says of it (`app/api/access.py`).

Every operation declares `ErrorResponse` as its error body. That replaces FastAPI's own
422 schema in the OpenAPI document, so the generated client knows the one error shape.
"""

from typing import Any, Final

from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_staff, require_user
from app.api.routes import (
    barista,
    codes,
    config,
    health,
    leaderboard,
    live,
    markets,
    me,
    profile,
    trading,
    vouchers,
)
from app.api.routes.admin import code_batches as admin_code_batches
from app.api.routes.admin import grants as admin_grants
from app.api.routes.admin import markets as admin_markets
from app.api.routes.admin import risk as admin_risk
from app.api.routes.admin import users as admin_users
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
admin_router.include_router(admin_users.router)

member_router = APIRouter(dependencies=[Depends(require_user)])
member_router.include_router(me.router)
member_router.include_router(markets.router)
member_router.include_router(trading.router)
member_router.include_router(live.router)
member_router.include_router(codes.router)
member_router.include_router(vouchers.router)
member_router.include_router(leaderboard.router)
member_router.include_router(barista.router, dependencies=[Depends(require_staff)])
member_router.include_router(admin_router)

api_router = APIRouter(prefix=API_PREFIX, responses=ERROR_RESPONSES)
api_router.include_router(health.router)
api_router.include_router(config.router)
api_router.include_router(profile.router)
api_router.include_router(member_router)
