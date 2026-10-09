"""The SMTP mailer and the sign-in mail, delivered to a real Mailpit (SPEC §3, A.3)."""

from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.core.config import MailSettings, Settings
from app.integrations.mail.mailer import MailDeliveryError, SmtpMailer
from app.integrations.mail.templates import magic_link_mail
from tests.support.containers import LOOPBACK

from .conftest import Mailpit

pytestmark = pytest.mark.integration


def settings_for(base_settings: Settings, host: str, port: int) -> MailSettings:
    return base_settings.mail.model_copy(update={"host": host, "port": port})


async def delivered_to(mailpit: Mailpit, recipient: str) -> list[dict[str, Any]]:
    """Every message Mailpit holds for `recipient`, in full, through its HTTP API."""
    async with httpx.AsyncClient(base_url=mailpit.api_url) as api:
        listing = (await api.get("/api/v1/messages")).raise_for_status().json()
        ids = [
            summary["ID"]
            for summary in listing["messages"]
            if recipient in [to["Address"] for to in summary["To"]]
        ]
        return [(await api.get(f"/api/v1/message/{i}")).raise_for_status().json() for i in ids]


async def test_sign_in_mail_arrives_with_link_button_and_validity(
    mailpit: Mailpit, base_settings: Settings
) -> None:
    mailer = SmtpMailer(settings_for(base_settings, mailpit.host, mailpit.smtp_port))
    recipient = f"{uuid4().hex}@student.ethz.ch"
    link = "http://localhost:8080/login/confirm#token=4f3c-Zx_Q&x=1"

    await mailer.send(magic_link_mail(recipient, link, ttl_minutes=15))

    (message,) = await delivered_to(mailpit, recipient)
    assert message["Subject"] == "Your MarkETH sign-in link"
    assert message["From"] == {"Name": "MarkETH", "Address": "no-reply@vis.ethz.ch"}
    assert message["MessageID"].endswith("@vis.ethz.ch")
    assert message["Date"]
    for part in (message["Text"], message["HTML"]):
        assert "MarkETH" in part
        assert "works once and expires in 15 minutes" in part
        assert "you can ignore this email" in part
    assert link in message["Text"]
    assert 'href="http://localhost:8080/login/confirm#token=4f3c-Zx_Q&amp;x=1"' in message["HTML"]
    assert "background: #215CAF" in message["HTML"]
    assert message["HTML"].count("<a ") == 1, "one button, and the link once more as text"


async def test_unreachable_smtp_server_raises_mail_delivery_error(
    base_settings: Settings, closed_port: int
) -> None:
    mailer = SmtpMailer(settings_for(base_settings, LOOPBACK, closed_port))

    with pytest.raises(MailDeliveryError):
        await mailer.send(magic_link_mail("alice@ethz.ch", "http://localhost/x", ttl_minutes=15))


async def test_recipient_the_message_cannot_carry_raises_mail_delivery_error(
    mailpit: Mailpit, base_settings: Settings
) -> None:
    mailer = SmtpMailer(settings_for(base_settings, mailpit.host, mailpit.smtp_port))

    with pytest.raises(MailDeliveryError):
        await mailer.send(magic_link_mail("alice@ethz.ch\nBcc: eve@example.org", "x", 15))
