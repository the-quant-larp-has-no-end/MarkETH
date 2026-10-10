"""Setting up and changing one's profile (SPEC §3, §10, Appendix A.3)."""

from pydantic import Field

from app.core.degrees import PROGRAMME_MAX_LENGTH
from app.core.enums import DegreeLevel
from app.core.identity import USERNAME_PATTERN, USERNAME_STORED_MAX_LENGTH
from app.schemas.common import RequestModel
from app.schemas.me import MeResponse


class SaveProfileRequest(RequestModel):
    """`PUT /me/profile`: the username and the degree a person states.

    This model checks the shape. The service checks what depends on settings and on
    the catalogue: the username's length against `username_min_length` and
    `username_max_length`, that the username is not one the server reserves, and the
    degree as a pair that `degrees` of `GET /config` lists. Each failure is
    `VALIDATION_ERROR` with the field in `details.field`.
    """

    username: str = Field(
        min_length=1, max_length=USERNAME_STORED_MAX_LENGTH, pattern=USERNAME_PATTERN
    )
    """Lower-case letters, digits and underscore: a capital letter is not accepted. A
    name somebody else holds is answered with `USERNAME_TAKEN`."""

    degree_level: DegreeLevel
    degree_programme: str = Field(min_length=1, max_length=PROGRAMME_MAX_LENGTH)
    """A label of `degree_level`, exactly as `GET /config` lists it."""


class ProfileSavedResponse(MeResponse):
    """The profile as it is after the save, with everything `GET /me` returns."""

    signup_granted: bool
    """True when this save created the profile and booked its signup grant."""
