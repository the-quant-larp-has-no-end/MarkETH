"""Whom the portal's id stands for, and with which role (SPEC §3, Appendix A.3).

The portal names the caller by an address in a request header. Nobody typed that value
and nobody proved it with a link: it is an identifier another system asserts. So it is
read strictly, by `app.core.identity.portal_address`. An id counts only if, in ASCII and
in lower case, it is exactly

    [a-z0-9][a-z0-9._-]{0,63}@<one of ALLOWED_EMAIL_DOMAINS>

Nothing is trimmed, no Unicode form is folded and no domain is rewritten: an id with
padding, with a character outside ASCII or with a second address in it is somebody this
policy does not know, and is refused rather than read as the nearest known address.

An accepted id is the account's address, in lower case. One id is one account: where two
domains are allowed, `x@student.ethz.ch` and `x@ethz.ch` are two people with two wallets.
`users.email`, which is unique, holds the id.

`EmailPolicy` is pure: settings in, values out, no I/O.
"""

from collections.abc import Iterable

from app.core.config import Settings
from app.core.enums import UserRole
from app.core.errors import ForbiddenError
from app.core.identity import portal_address


class EmailPolicy:
    """Built once, from the identity settings.

    Building it raises `ValueError` for a role list that holds an entry `identify`
    would refuse: such an entry gives its role to nobody, and a process must not start
    on it. `IdentitySettings` refuses the entry when it loads `.env`; here the same
    rule holds for settings that were put together in code.
    """

    def __init__(self, settings: Settings) -> None:
        identity = settings.identity
        self._allowed_domains = tuple(identity.allowed_email_domains)
        self._admins = self._role_list("ADMIN_EMAILS", identity.admin_emails)
        self._baristas = self._role_list("BARISTA_EMAILS", identity.barista_emails)

    def identify(self, raw: str) -> str:
        """The account's address for the id the portal sent: that id in lower case.

        Raises `ForbiddenError` (403) with `details = {"allowed_domains": [...]}` for
        anything that is not an allowed address. A malformed id and a foreign domain
        get the same answer; the allowlist is no secret (A.3).
        """
        email = portal_address(raw, self._allowed_domains)
        if email is None:
            raise ForbiddenError(
                f"MarkETH is only open to accounts at {' or '.join(self._allowed_domains)}.",
                details={"allowed_domains": list(self._allowed_domains)},
            )
        return email

    def role_for(self, email: str) -> UserRole:
        """The role of an address `identify` returned: ADMIN if it is in `ADMIN_EMAILS`,
        else BARISTA if it is in `BARISTA_EMAILS`, else USER.

        Asked on every request and stored nowhere, so the lists of `.env` are the roles.
        """
        if email in self._admins:
            return UserRole.ADMIN
        if email in self._baristas:
            return UserRole.BARISTA
        return UserRole.USER

    def _role_list(self, variable: str, entries: Iterable[str]) -> frozenset[str]:
        """The addresses of a role list, each entry read the way an id is."""
        addresses: set[str] = set()
        for entry in entries:
            email = portal_address(entry, self._allowed_domains)
            if email is None:
                raise ValueError(
                    f"{variable}: {entry!r} is not an address at "
                    f"{' or '.join(self._allowed_domains)}, so nobody would get the role"
                )
            addresses.add(email)
        return frozenset(addresses)
