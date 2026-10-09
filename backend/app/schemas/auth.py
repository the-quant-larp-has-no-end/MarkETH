"""Magic-link sign-in (SPEC §3, §10, Appendix A.3)."""

from typing import Final

from pydantic import Field

from app.schemas.common import RequestModel, ResponseModel
from app.schemas.me import MeResponse

EMAIL_MAX_LENGTH: Final[int] = 320
TOKEN_MAX_LENGTH: Final[int] = 256


class LoginLinkRequest(RequestModel):
    """`POST /auth/request-link`. The address is normalised and checked by the service."""

    email: str = Field(min_length=3, max_length=EMAIL_MAX_LENGTH)


class LoginLinkResponse(ResponseModel):
    """Always the same answer for an allowlisted address, whether an account exists or not."""

    link_ttl_minutes: int
    """How long the link in the mail stays valid."""


class VerifyLoginRequest(RequestModel):
    """`POST /auth/verify`: the token from the link's fragment (A.3)."""

    token: str = Field(min_length=1, max_length=TOKEN_MAX_LENGTH)


class VerifyLoginResponse(MeResponse):
    """The signed-in user. The session itself travels in the cookie, never in the body."""

    signup_granted: bool
    """True when this sign-in created the account and booked its signup grant."""
