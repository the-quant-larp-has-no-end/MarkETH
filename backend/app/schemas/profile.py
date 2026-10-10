"""Setting up and changing one's profile (SPEC §3, §10, Appendix A.3)."""

from typing import Annotated, Final

from pydantic import Field, StrictBool

from app.core.degrees import PROGRAMME_MAX_LENGTH
from app.core.enums import DegreeLevel
from app.core.identity import USERNAME_PATTERN, USERNAME_STORED_MAX_LENGTH
from app.schemas.common import RequestModel, in_its_own_words
from app.schemas.me import MeResponse

USERNAME_CHARACTERS_MESSAGE: Final[str] = "Use lower-case letters, digits and underscores only."
"""What the username field says for a character that is none of those: a capital
letter, a hyphen, a dot, a space, an umlaut."""

Username = Annotated[
    str,
    Field(min_length=1, max_length=USERNAME_STORED_MAX_LENGTH, pattern=USERNAME_PATTERN),
    in_its_own_words(string_pattern_mismatch=USERNAME_CHARACTERS_MESSAGE),
]
"""A username as a request states it: the characters and the length the database
takes. The length a person may choose is the service's to check."""


class SaveProfileRequest(RequestModel):
    """`PUT /me/profile`: the username and the degree a person states, and with the
    first save their acceptance of the usage policy.

    This model checks the shape. The service checks what depends on settings, on the
    catalogue and on whether a profile exists: the username's length against
    `username_min_length` and `username_max_length`, that the username is not one
    the server reserves, the degree as a pair that `degrees` of `GET /config` lists,
    and `policy_accepted` where the save creates the profile. Each failure is
    `VALIDATION_ERROR` with the field in `details.field`.
    """

    username: Username
    """Lower-case letters, digits and underscore: a capital letter is not accepted. A
    name somebody else holds is answered with `USERNAME_TAKEN`."""

    degree_level: DegreeLevel
    degree_programme: str = Field(min_length=1, max_length=PROGRAMME_MAX_LENGTH)
    """A label of `degree_level`, exactly as `GET /config` lists it."""

    policy_accepted: StrictBool = False
    """Whether the person accepts the usage policy. The save that creates a profile
    takes `true` and nothing else. A save that changes a profile does not look at
    it and may leave it out."""


class ProfileSavedResponse(MeResponse):
    """The profile as it is after the save, with everything `GET /me` returns."""

    signup_granted: bool
    """True when this save created the profile and booked its signup grant."""
