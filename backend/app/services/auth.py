"""Passwordless sign-in by magic link (SPEC §3, Appendix A.3).

Requesting a link stores the hash of a random token and mails the token. Confirming it
consumes the token and, in the same transaction, creates the user if needed, books the
signup grant once and opens a session. Both steps are rate limited in Redis and fail
closed: without Redis nobody signs in (SPEC §9).
"""

from dataclasses import dataclass
from datetime import datetime

from app.core.clock import Clock
from app.core.config import Settings
from app.integrations.mail.mailer import Mailer
from app.integrations.redis.rate_limiter import RateLimit, RateLimiter
from app.schemas.auth import LoginLinkResponse, VerifyLoginResponse
from app.services import UowFactory
from app.services.email_policy import EmailPolicy
from app.services.grants import GrantService
from app.services.sessions import SessionService


@dataclass(frozen=True, slots=True)
class SignIn:
    """A successful verification: what the route puts in the cookie and in the body."""

    session_token: str
    session_expires_at: datetime
    response: VerifyLoginResponse


class AuthService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        email_policy: EmailPolicy,
        sessions: SessionService,
        grants: GrantService,
        rate_limiter: RateLimiter,
        mailer: Mailer,
    ) -> None:
        auth = settings.auth
        self._uow_factory = uow_factory
        self._auth = auth
        self._monthly_grant_micro = settings.vouchers.monthly_grant_micro
        self._public_base_url = settings.app.public_base_url
        self._clock = clock
        self._email_policy = email_policy
        self._sessions = sessions
        self._grants = grants
        self._rate_limiter = rate_limiter
        self._mailer = mailer
        self._link_limit_per_ip = RateLimit(
            "login-link-ip", auth.link_limit_per_ip, auth.link_limit_per_ip_window_seconds
        )
        self._link_limit_per_email = RateLimit(
            "login-link-email",
            auth.link_limit_per_email,
            auth.link_limit_per_email_window_seconds,
        )
        self._verify_limit_per_ip = RateLimit(
            "login-verify-ip", auth.verify_limit_per_ip, auth.verify_limit_per_ip_window_seconds
        )

    async def request_link(self, email: str, client_ip: str) -> LoginLinkResponse:
        """Mail a magic link to an allowlisted address (`POST /auth/request-link`).

        1. `EmailPolicy.identify(email)`: a malformed or foreign address raises
           `ValidationFailedError` (422), before any limit is counted.
        2. Rate limits, fail closed: hit `login-link-ip` with subject `client_ip`, then
           `login-link-email` with subject the canonical address. A rejected hit raises
           `RateLimitedError(retry_after_seconds=...)` (429). `RedisUnavailableError`
           becomes `ServiceUnavailableError` (503).
        3. `token = generate_token(LOGIN_TOKEN_BYTES)`. In one transaction insert
           `LoginToken(email=<normalised address>, token_hash=sha256_hex(token),
           created_at=now, expires_at=now + LOGIN_TOKEN_TTL_MINUTES)`. No user row is
           created or read: users exist only after verification.
        4. After the commit send `magic_link_mail(to=<normalised address>,
           link=f"{PUBLIC_BASE_URL}/login/confirm#token={token}", ttl_minutes)`. A
           `MailDeliveryError` is logged (without the token) and swallowed.
        5. Return `LoginLinkResponse(link_ttl_minutes=LOGIN_TOKEN_TTL_MINUTES)`.

        The answer is the same whether or not an account exists (no account probing).
        The token appears in the mail only: never in a log, never in the response.
        """
        raise NotImplementedError

    async def verify(self, token: str, client_ip: str) -> SignIn:
        """Consume a magic-link token and sign its owner in (`POST /auth/verify`).

        1. Rate limit, fail closed: hit `login-verify-ip` with subject `client_ip`
           (429 or 503 as in `request_link`).
        2. One transaction:
           a. `login_tokens.get_by_hash(sha256_hex(token), for_update=True)`. No row,
              `consumed_at` set, or `now >= expires_at`: `LoginLinkInvalidError` (400).
           b. `EmailPolicy.identify(row.email)`; if the address is no longer allowed,
              `LoginLinkInvalidError`. `role = EmailPolicy.role_for(canonical)`.
           c. `users.insert_if_absent(canonical_email, email, role, ACTIVE, now)`
              returns the locked user, new or existing. On an existing row set `email`
              and `role` to the values of this sign-in. A SUSPENDED user raises
              `ForbiddenError` (403, "This account is suspended.").
           d. Lock the user's account, creating it first if the user is new
              (`accounts.create_user_account`).
           e. `signup_granted = GrantService.grant_signup(uow, user, account)`.
           f. Set the token's `consumed_at = now`.
           g. `SessionService.create(uow, user)`.
        3. Return `SignIn` with the session token, its expiry and a
           `VerifyLoginResponse`: the user's profile, the balance after the grant,
           `monthly_grant_cr`, `next_monthly_grant_on=GrantService.next_grant_date()`
           and `signup_granted`.

        Any failure in step 2 rolls the whole transaction back, the token included: a
        link is single use only once a sign-in succeeded. Two confirmations of one
        link race on the token's row lock; the loser sees it consumed.
        """
        raise NotImplementedError

    async def logout(self, session_token: str | None) -> None:
        """End the session of the cookie, if there is one (`POST /auth/logout`).

        `SessionService.revoke(session_token)` when a token was sent; nothing
        otherwise. Never raises for an unknown or missing token.
        """
        raise NotImplementedError
