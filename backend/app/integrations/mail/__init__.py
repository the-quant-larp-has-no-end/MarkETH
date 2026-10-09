"""Mail adapters: the `Mailer` protocol, its SMTP implementation and the templates (SPEC §3)."""

from app.integrations.mail.mailer import MailDeliveryError, Mailer, OutgoingMail, SmtpMailer
from app.integrations.mail.templates import magic_link_mail

__all__ = ["MailDeliveryError", "Mailer", "OutgoingMail", "SmtpMailer", "magic_link_mail"]
