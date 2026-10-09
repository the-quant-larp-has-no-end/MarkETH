"""Who may sign in, and as whom (SPEC §3, Appendix A.3).

An address is first normalised, then checked against the allowlist, then mapped to its
canonical identity: `x@student.ethz.ch` and `x@ethz.ch` are one person, `x@ethz.ch`.
`users.canonical_email` is unique, which is what stops a second signup grant.

`EmailPolicy` is pure: settings in, values out, no I/O.
"""

from dataclasses import dataclass

from app.core.config import Settings
from app.core.enums import UserRole


@dataclass(frozen=True, slots=True)
class EmailIdentity:
    email: str
    """The normalised address as the user typed it. Mail is sent here."""

    canonical_email: str
    """`local@CANONICAL_EMAIL_DOMAIN`: the identity, unique per person."""


class EmailPolicy:
    def __init__(self, settings: Settings) -> None:
        self._auth = settings.auth

    def identify(self, raw: str) -> EmailIdentity:
        """Normalise an address as typed and return its identity (SPEC §3).

        1. Normalise: strip surrounding whitespace, apply Unicode NFKC, lowercase.
        2. Split at "@". Exactly one "@" with a non-empty part on each side, at most
           320 characters in total.
        3. The local part must match `[a-z0-9][a-z0-9._-]{0,63}`. This rejects `+` tags
           and quoted local parts, which the spec forbids, and anything with spaces or
           non-ASCII characters.
        4. The domain must equal one of `ALLOWED_EMAIL_DOMAINS` exactly; a subdomain
           of an allowed domain is not allowed.
        5. `email` is `local@domain`, `canonical_email` is
           `local@CANONICAL_EMAIL_DOMAIN`.

        Raises `ValidationFailedError` (422) when a step fails, with the message
        "Use your ETH email address (…)" naming the allowed domains and
        `details = {"allowed_domains": [...]}`. The allowlist is public (A.3), so a
        malformed address and a foreign domain get the same answer.
        """
        raise NotImplementedError

    def role_for(self, canonical_email: str) -> UserRole:
        """The role of an identity, re-derived at every sign-in (A.3).

        ADMIN if `canonical_email` is in `ADMIN_EMAILS`, else BARISTA if it is in
        `BARISTA_EMAILS`, else USER. Both lists may name an address by any allowed
        domain; each entry is canonicalised with the rules of `identify` before it is
        compared. An entry that `identify` would reject matches nobody and is skipped.
        """
        raise NotImplementedError
