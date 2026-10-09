"""Who may call what, written into the OpenAPI document (Appendix A.2).

The frontend is built from `openapi.json`, so the document has to say which operations
need a session and which role. Nobody writes that down per route. Each guard in
`app/api/deps.py` (`OptionalUser`, `CurrentUser`, `BaristaUser`, `AdminUser`) depends on
one of the markers below; FastAPI then lists the marker under `security` of every
operation the guard protects, and `describe_access` turns those markers into what the
contract says: one scheme, the session cookie, with the role an operation needs, and a
line in the operation's description for people. A route added later is documented by
the guard it uses and cannot say one thing while doing another.

The OpenAPI 3.1 way to name a role is the list after the scheme, `{"session": ["ADMIN"]}`.
Alternatives are separate entries, and an empty object means "no session at all".

A marker checks nothing. Authentication is `authenticate_request`, and the rules are
the guards'.
"""

from dataclasses import dataclass
from typing import Any, Final

from fastapi.openapi.models import APIKey, APIKeyIn
from fastapi.security.base import SecurityBase

from app.core.enums import UserRole

SESSION_SCHEME: Final[str] = "session"

DOCUMENTED_SESSION_COOKIE: Final[str] = "marketh_session"
"""The cookie's name as the contract states it: the SESSION_COOKIE_NAME of
`.example.env`. Building the document reads no settings (it is exported where there
are none), so the name is repeated here, and `tests/integration/test_api_contract.py`
fails when the two differ. The running API takes the name from its settings alone."""

SESSION_SCHEME_DESCRIPTION: Final[str] = (
    "The session cookie that `POST /auth/verify` sets: HttpOnly and SameSite=Lax, so the "
    "browser sends it by itself and scripts never see it. Its name is the server's "
    "SESSION_COOKIE_NAME setting. Where an operation lists a role, the session must "
    "belong to a user with that role. A suspended account is refused wherever a session "
    "is needed."
)

type SecurityRequirement = dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class Access:
    """One answer to "who may call this": for tools (`security`) and for people (`note`)."""

    security: tuple[SecurityRequirement, ...]
    note: str


PUBLIC: Final[Access] = Access((), "Access: public. No session is needed or looked at.")
PUBLIC_WITH_VIEWER: Final[Access] = Access(
    ({}, {SESSION_SCHEME: []}),
    "Access: public. With a session the answer also carries what belongs to the viewer.",
)
SIGNED_IN: Final[Access] = Access(
    ({SESSION_SCHEME: []},), "Access: any signed-in user whose account is active."
)
STAFF: Final[Access] = Access(
    ({SESSION_SCHEME: [UserRole.BARISTA.value]}, {SESSION_SCHEME: [UserRole.ADMIN.value]}),
    "Access: role BARISTA or ADMIN.",
)
ADMIN: Final[Access] = Access(({SESSION_SCHEME: [UserRole.ADMIN.value]},), "Access: role ADMIN.")


class AccessMarker(SecurityBase):
    """What a guard depends on so that its operations are marked in the document."""

    def __init__(self, scheme_name: str, access: Access) -> None:
        self.scheme_name = scheme_name
        self.access = access
        self.model = APIKey.model_validate(
            {"type": "apiKey", "in": APIKeyIn.cookie, "name": DOCUMENTED_SESSION_COOKIE}
        )

    async def __call__(self) -> None:
        return None


MAY_HAVE_SESSION: Final[AccessMarker] = AccessMarker("access:viewer", PUBLIC_WITH_VIEWER)
"""Marks `authenticate_request`: a session is read if there is one."""

NEEDS_SESSION: Final[AccessMarker] = AccessMarker("access:signed-in", SIGNED_IN)
"""Marks `require_user`."""

NEEDS_STAFF: Final[AccessMarker] = AccessMarker("access:staff", STAFF)
"""Marks `require_staff`."""

NEEDS_ADMIN: Final[AccessMarker] = AccessMarker("access:admin", ADMIN)
"""Marks `require_admin`."""

_STRICTEST_FIRST: Final[tuple[AccessMarker, ...]] = (
    NEEDS_ADMIN,
    NEEDS_STAFF,
    NEEDS_SESSION,
    MAY_HAVE_SESSION,
)
"""A guard builds on the ones after it, so an operation carries several markers and
the first one found here decides."""


def describe_access(document: dict[str, Any]) -> None:
    """Rewrite the markers of an OpenAPI document into the session scheme and each
    operation's access, in place. Doing it a second time changes nothing."""
    schemes = document.setdefault("components", {}).setdefault("securitySchemes", {})
    if SESSION_SCHEME in schemes:
        return
    for marker in _STRICTEST_FIRST:
        schemes.pop(marker.scheme_name, None)
    schemes[SESSION_SCHEME] = {
        "type": "apiKey",
        "in": "cookie",
        "name": DOCUMENTED_SESSION_COOKIE,
        "description": SESSION_SCHEME_DESCRIPTION,
    }
    for path_item in document.get("paths", {}).values():
        for operation in path_item.values():
            access = _access_of(operation.pop("security", []))
            if access.security:
                operation["security"] = [dict(requirement) for requirement in access.security]
            written = operation.get("description")
            operation["description"] = f"{written}\n\n{access.note}" if written else access.note


def _access_of(marked: list[SecurityRequirement]) -> Access:
    names = {name for requirement in marked for name in requirement}
    for marker in _STRICTEST_FIRST:
        if marker.scheme_name in names:
            return marker.access
    return PUBLIC
