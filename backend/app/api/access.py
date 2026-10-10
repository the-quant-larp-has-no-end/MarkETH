"""Who may call what, written into the OpenAPI document (Appendix A.2).

The frontend is built from `openapi.json`, so the document has to say which operations
need an identity, which a profile and which a role. Nobody writes that down per route.
Each guard in `app/api/deps.py` (`Identity`, `CurrentUser`, `BaristaUser`, `AdminUser`)
depends on one of the markers below; FastAPI then lists the marker under `security` of
every operation the guard protects, and `describe_access` turns those markers into what
the contract says: one scheme, the header in which the portal names the caller, with
what an operation needs beyond it, and a line in the operation's description for people.
A route added later is documented by the guard it uses and cannot say one thing while
doing another.

The OpenAPI 3.1 way to name a requirement is the list after the scheme,
`{"portal": ["ADMIN"]}`. Alternatives are separate entries. An operation without
`security` needs no identity at all.

A marker checks nothing. Who is calling is settled by `authenticate_request`, and the
rules are the guards'.
"""

from dataclasses import dataclass
from typing import Any, Final

from fastapi.openapi.models import APIKey, APIKeyIn
from fastapi.security.base import SecurityBase

from app.core.enums import UserRole

PORTAL_SCHEME: Final[str] = "portal"

DOCUMENTED_IDENTITY_HEADER: Final[str] = "X-User-Id"
"""The header's name as the contract states it: the IDENTITY_HEADER_USER_ID of
`.example.env`. Building the document reads no settings (it is exported where there
are none), so the name is repeated here, and `tests/integration/test_api_contract.py`
fails when the two differ. The running API takes the name from its settings alone."""

PORTAL_SCHEME_DESCRIPTION: Final[str] = (
    "The request header in which the portal names the caller after ETH single sign-on. "
    "The portal adds it on the way to the API; a client of this API never sends it. "
    "Its name is the server's IDENTITY_HEADER_USER_ID setting. The list of an operation "
    "says what the caller needs beyond being named: `REGISTERED` is a saved profile, "
    "`BARISTA` and `ADMIN` are roles. A suspended account is refused wherever an "
    "identity is needed."
)

REGISTERED: Final[str] = "REGISTERED"
"""Named in `security` where a caller needs a profile: a `users` row that is ACTIVE."""

type SecurityRequirement = dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class Access:
    """One answer to "who may call this": for tools (`security`) and for people (`note`)."""

    security: tuple[SecurityRequirement, ...]
    note: str


PUBLIC: Final[Access] = Access((), "Access: public. No identity is needed or looked at.")
IDENTITY: Final[Access] = Access(
    ({PORTAL_SCHEME: []},),
    "Access: anyone the portal signed in with an allowed address. No profile is needed.",
)
MEMBER: Final[Access] = Access(
    ({PORTAL_SCHEME: [REGISTERED]},), "Access: any registered user whose account is active."
)
STAFF: Final[Access] = Access(
    ({PORTAL_SCHEME: [UserRole.BARISTA.value]}, {PORTAL_SCHEME: [UserRole.ADMIN.value]}),
    "Access: role BARISTA or ADMIN.",
)
ADMIN: Final[Access] = Access(({PORTAL_SCHEME: [UserRole.ADMIN.value]},), "Access: role ADMIN.")


class AccessMarker(SecurityBase):
    """What a guard depends on so that its operations are marked in the document."""

    def __init__(self, scheme_name: str, access: Access) -> None:
        self.scheme_name = scheme_name
        self.access = access
        self.model = APIKey.model_validate(
            {"type": "apiKey", "in": APIKeyIn.header, "name": DOCUMENTED_IDENTITY_HEADER}
        )

    async def __call__(self) -> None:
        return None


NEEDS_IDENTITY: Final[AccessMarker] = AccessMarker("access:identity", IDENTITY)
"""Marks `require_identity`."""

NEEDS_PROFILE: Final[AccessMarker] = AccessMarker("access:member", MEMBER)
"""Marks `require_user`."""

NEEDS_STAFF: Final[AccessMarker] = AccessMarker("access:staff", STAFF)
"""Marks `require_staff`."""

NEEDS_ADMIN: Final[AccessMarker] = AccessMarker("access:admin", ADMIN)
"""Marks `require_admin`."""

_STRICTEST_FIRST: Final[tuple[AccessMarker, ...]] = (
    NEEDS_ADMIN,
    NEEDS_STAFF,
    NEEDS_PROFILE,
    NEEDS_IDENTITY,
)
"""A guard may build on one that comes after it here, so an operation can carry
several markers; the first one found in this order decides."""


def describe_access(document: dict[str, Any]) -> None:
    """Rewrite the markers of an OpenAPI document into the portal scheme and each
    operation's access, in place. Doing it a second time changes nothing."""
    schemes = document.setdefault("components", {}).setdefault("securitySchemes", {})
    if PORTAL_SCHEME in schemes:
        return
    for marker in _STRICTEST_FIRST:
        schemes.pop(marker.scheme_name, None)
    schemes[PORTAL_SCHEME] = {
        "type": "apiKey",
        "in": "header",
        "name": DOCUMENTED_IDENTITY_HEADER,
        "description": PORTAL_SCHEME_DESCRIPTION,
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
