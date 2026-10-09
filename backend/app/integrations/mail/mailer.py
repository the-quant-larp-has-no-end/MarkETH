"""Outgoing mail (SPEC §3, A.3).

MarkETH sends one kind of mail, the magic sign-in link: to Mailpit during development,
through a real SMTP server for the demo. `Mailer` is a protocol so tests can capture
mail instead of sending it.
"""

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import format_datetime, localtime, make_msgid, parseaddr
from typing import Final, Protocol

import aiosmtplib

from app.core.config import MailSettings

SMTP_TIMEOUT_SECONDS: Final[float] = 10.0
"""Limit for each step of the SMTP conversation. The sign-in request waits for the send."""


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    """One message to one recipient, as plain text and as HTML."""

    to: str
    subject: str
    text: str
    html: str


class MailDeliveryError(Exception):
    """The message was not handed to the SMTP server."""


class Mailer(Protocol):
    async def send(self, mail: OutgoingMail) -> None:
        """Hand one message over for delivery, or raise `MailDeliveryError`."""
        ...


class SmtpMailer:
    """Sends mail through the SMTP server configured in `.env`.

    When the server is unreachable, too slow or refuses the message: `send` raises
    `MailDeliveryError` and nothing else. Nothing is queued or retried. The caller logs
    the failure and still answers 202 (SPEC A.3); the user asks for a new link.

    STARTTLS is used only when `SMTP_STARTTLS` is true, and then it is required. The
    client logs in only when `SMTP_USERNAME` is set.
    """

    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings
        # Given explicitly: without a domain, make_msgid() does a blocking DNS lookup.
        self._message_id_domain = parseaddr(settings.sender)[1].rpartition("@")[2] or settings.host

    async def send(self, mail: OutgoingMail) -> None:
        try:
            await aiosmtplib.send(
                self._compose(mail),
                hostname=self._settings.host,
                port=self._settings.port,
                username=self._settings.username or None,
                password=self._settings.password or None,
                start_tls=self._settings.starttls,
                timeout=SMTP_TIMEOUT_SECONDS,
            )
        # ValueError: an address or header the message cannot carry.
        except (aiosmtplib.SMTPException, OSError, ValueError) as exc:
            raise MailDeliveryError(f"mail was not delivered: {exc!r}") from exc

    def _compose(self, mail: OutgoingMail) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._settings.sender
        message["To"] = mail.to
        message["Subject"] = mail.subject
        message["Date"] = format_datetime(localtime())
        message["Message-ID"] = make_msgid(domain=self._message_id_domain)
        message.set_content(mail.text)
        message.add_alternative(mail.html, subtype="html")
        return message
