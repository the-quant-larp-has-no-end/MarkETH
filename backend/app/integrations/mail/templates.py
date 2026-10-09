"""The mails MarkETH sends. There is one: the magic sign-in link (SPEC §3, A.3).

Kept plain on purpose: a short text, one button, the link once more as text. Styles are
inline because mail clients drop style sheets.
"""

from html import escape
from typing import Final

from app.integrations.mail.mailer import OutgoingMail

ETH_BLUE: Final[str] = "#215CAF"

_FONT: Final[str] = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"


def magic_link_mail(to: str, link: str, ttl_minutes: int) -> OutgoingMail:
    """The sign-in mail. `link` opens the confirm page; nothing is consumed by opening it."""
    validity = "1 minute" if ttl_minutes == 1 else f"{ttl_minutes} minutes"
    once = f"The link works once and expires in {validity}."
    ignore = "If you did not request it, you can ignore this email."
    text = "\n".join(
        (
            "Sign in to MarkETH",
            "",
            "Open this link to sign in:",
            link,
            "",
            once,
            ignore,
            "",
        )
    )
    href = escape(link, quote=True)
    html = f"""\
<!doctype html>
<html lang="en">
  <body style="margin: 0; padding: 24px; background: #ffffff; color: #1f2933; \
font-family: {_FONT}; font-size: 16px; line-height: 1.5;">
    <div style="max-width: 480px; margin: 0 auto;">
      <p style="margin: 0 0 24px; font-size: 22px; font-weight: 700;">MarkETH</p>
      <p style="margin: 0 0 24px;">Tap the button to sign in.</p>
      <p style="margin: 0 0 24px;">
        <a href="{href}" style="display: inline-block; padding: 14px 28px; \
background: {ETH_BLUE}; color: #ffffff; font-weight: 600; text-decoration: none; \
border-radius: 6px;">Sign in to MarkETH</a>
      </p>
      <p style="margin: 0 0 4px; font-size: 14px; color: #52606d;">\
Or copy this link into your browser:</p>
      <p style="margin: 0 0 24px; font-size: 14px; word-break: break-all;">{href}</p>
      <p style="margin: 0; font-size: 14px; color: #52606d;">{once} {ignore}</p>
    </div>
  </body>
</html>
"""
    return OutgoingMail(to=to, subject="Your MarkETH sign-in link", text=text, html=html)
