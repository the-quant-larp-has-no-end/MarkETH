"""`PUT /me/profile`: how a person the portal signed in gets a profile, and changes it
(SPEC §3, §10, Appendix A.3).

It is the one route a caller without a profile may use, so it is not part of the member
routes (`app/api/router.py`).
"""

from fastapi import APIRouter

from app.api.deps import Identity, ProfileServiceDep
from app.schemas.profile import ProfileSavedResponse, SaveProfileRequest

router = APIRouter(prefix="/me", tags=["me"])


@router.put("/profile", summary="Create the caller's profile, or change it")
async def save_profile(
    body: SaveProfileRequest, identity: Identity, profiles: ProfileServiceDep
) -> ProfileSavedResponse:
    return await profiles.save(identity, body)
