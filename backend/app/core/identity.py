"""Who is making a request (SPEC §3, Appendix A.2, A.3).

MarkETH has no sign-in of its own. The portal in front of it signs a person in and names
them in two request headers; `IdentityService` turns those into a `Caller`, and the
guards of `app/api/deps.py` decide what that caller may reach. These are plain values,
never attached to a database session.

How the two headers are read is fixed here, once, as two pure functions: `portal_address`
says whether an id is one MarkETH knows, `display_name` makes of the name header what a
profile stores. What a username looks like is fixed here too, because the database, the
request model and the settings all have to agree on it.
"""

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote
from uuid import UUID

from app.core.enums import UserRole, UserStatus

USERNAME_CHARACTERS: Final[str] = "a-z0-9_"
"""What a username is made of, as a character class: lower-case letters, digits and
underscore. A username has one spelling, so being unique needs no folding of case."""

USERNAME_STORED_MAX_LENGTH: Final[int] = 32
"""The longest username the database takes. `USERNAME_MAX_LENGTH` in `.env` is the
length a person may choose and cannot be set above this."""

USERNAME_PATTERN: Final[str] = f"^[{USERNAME_CHARACTERS}]{{1,{USERNAME_STORED_MAX_LENGTH}}}$"
"""A username, as a regular expression: the CHECK of `users.username` and the pattern
of the profile request are both this."""

DISPLAY_NAME_MAX_LENGTH: Final[int] = 200
"""The longest display name kept, which is also the width of `users.name`."""

_ID_LOCAL_PART: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
"""What stands before the `@` of an id. No `+` tag, no quoted local part, nothing a
mail system would read in two ways."""

_REMOVED_FROM_A_NAME: Final[frozenset[str]] = frozenset({"Cc", "Cf"})
"""Unicode categories a display name does not keep: control characters (a NUL, an
escape) and format characters (a zero-width space, a direction override)."""


def portal_address(raw: str, allowed_domains: Collection[str]) -> str | None:
    """The address an id of the portal stands for, or None when the id is not accepted.

    An id counts only if it is ASCII and, in lower case, exactly

        [a-z0-9][a-z0-9._-]{0,63}@<one of allowed_domains>

    and the result is that lower-case text: nothing is trimmed, no Unicode form is
    folded and no domain is rewritten onto another. An id with padding, with a
    character outside ASCII or with a second address in it is somebody this rule does
    not know.

    `allowed_domains` are whole domains in lower case, and a domain is allowed only if
    it is one of them exactly: `mail.ethz.ch` is not `ethz.ch`. One domain passed as a
    bare string is refused with `TypeError`, because looking a domain up in a string
    would find every piece of it.

    The same rule reads the entries of `ADMIN_EMAILS`, so the list of bootstrap
    admins names exactly the ids the portal can send.
    """
    if isinstance(allowed_domains, str):
        raise TypeError("allowed_domains is a collection of domains, not one domain")
    # ASCII first: `str.lower` also folds characters from outside it onto ASCII letters
    # (the Kelvin sign becomes "k").
    if not raw.isascii():
        return None
    local, _, domain = raw.lower().partition("@")
    if domain not in allowed_domains or _ID_LOCAL_PART.fullmatch(local) is None:
        return None
    return f"{local}@{domain}"


def display_name(raw: str) -> str:
    """The name a profile stores, made of the name header as it arrived.

    The portal percent-encodes the name (`Zo%C3%AB`), so it is decoded first. Then, in
    this order: control and format characters go, except the ones that are whitespace;
    every run of whitespace, a tab or a line break included, becomes one space and
    none stays at either end; and the result is cut to `DISPLAY_NAME_MAX_LENGTH`
    characters. An empty header gives an empty name, and so does one that holds
    nothing a name keeps.

    What comes out holds no NUL, which Postgres refuses to store, no line break and no
    character that reorders or hides the text around it.
    """
    decoded = unquote(raw)
    kept = "".join(
        character
        for character in decoded
        if character.isspace() or unicodedata.category(character) not in _REMOVED_FROM_A_NAME
    )
    return " ".join(kept.split())[:DISPLAY_NAME_MAX_LENGTH].rstrip()


@dataclass(frozen=True, slots=True)
class PortalIdentity:
    """Who the portal says is calling, whether or not that person has a profile."""

    email: str
    """The portal's id in lower case: an address at one of `ALLOWED_EMAIL_DOMAINS`
    (`portal_address`). One id is one account."""

    name: str
    """The display name the portal sent, as `display_name` reads it. Empty when it sent
    none."""

    role: UserRole
    """What the person may do: ADMIN for an address of `ADMIN_EMAILS`, otherwise the
    role of their `users` row, and USER while they have none."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """A caller with a profile: the `users` row, with the role the caller has in
    effect. That is the row's own role, unless the address is one of
    `ADMIN_EMAILS`, which is ADMIN whatever the row says."""

    id: UUID
    email: str
    name: str
    username: str
    role: UserRole
    status: UserStatus

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    @property
    def is_admin(self) -> bool:
        """An ACTIVE admin. A suspended account keeps its role but none of its rights."""
        return self.is_active and self.role is UserRole.ADMIN

    @property
    def is_staff(self) -> bool:
        """ACTIVE baristas and admins may redeem vouchers."""
        return self.is_active and self.role in (UserRole.ADMIN, UserRole.BARISTA)


@dataclass(frozen=True, slots=True)
class Caller:
    """What a request that names somebody resolves to."""

    identity: PortalIdentity
    user: AuthenticatedUser | None
    """None until `PUT /me/profile` has created the person's `users` row."""
