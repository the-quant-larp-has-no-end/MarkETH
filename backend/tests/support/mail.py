"""A mailer that keeps what it is given instead of sending it (SPEC §3, A.3)."""

import re
from typing import Final

from app.integrations.mail.mailer import OutgoingMail

_LINK: Final[re.Pattern[str]] = re.compile(r"https?://\S+")
_TOKEN_MARKER: Final[str] = "#token="


class MemoryMailer:
    """Implements `app.integrations.mail.mailer.Mailer` by recording every mail.

    Set `failure` to an exception (for example `MailDeliveryError("down")`) to make
    every `send` raise it instead, the way an unreachable SMTP server would.
    """

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []
        self.failure: Exception | None = None

    async def send(self, mail: OutgoingMail) -> None:
        if self.failure is not None:
            raise self.failure
        self.sent.append(mail)

    @property
    def last(self) -> OutgoingMail:
        """The mail sent most recently. Fails the test if none was sent."""
        assert self.sent, "no mail was sent"
        return self.sent[-1]

    @property
    def last_magic_link(self) -> str:
        """The sign-in link of the most recent mail, as written in its text part."""
        match = _LINK.search(self.last.text)
        assert match is not None, f"no link in the mail: {self.last.text!r}"
        return match.group()

    @property
    def last_token(self) -> str:
        """The token in the fragment of that link: what the page posts to `/auth/verify`."""
        link = self.last_magic_link
        assert _TOKEN_MARKER in link, f"no token in the link: {link!r}"
        return link.partition(_TOKEN_MARKER)[2]

    def clear(self) -> None:
        self.sent.clear()
