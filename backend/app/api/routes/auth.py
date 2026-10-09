"""`/auth/*`: magic-link sign-in and the session cookie (SPEC §3, Appendix A.3).

The session token travels in a cookie only: HttpOnly, SameSite=Lax, Path=/, Secure when
`SESSION_COOKIE_SECURE` says so, and living exactly as long as the session.
"""

from fastapi import APIRouter, Response, status

from app.api.deps import AuthServiceDep, ClientIp, SessionToken, SettingsDep
from app.schemas.auth import (
    LoginLinkRequest,
    LoginLinkResponse,
    VerifyLoginRequest,
    VerifyLoginResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/request-link",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Mail a sign-in link to an ETH address",
)
async def request_login_link(
    body: LoginLinkRequest, auth: AuthServiceDep, ip: ClientIp
) -> LoginLinkResponse:
    return await auth.request_link(body.email, ip)


@router.post("/verify", summary="Confirm a sign-in link and open a session")
async def verify_login(
    body: VerifyLoginRequest,
    response: Response,
    auth: AuthServiceDep,
    settings: SettingsDep,
    ip: ClientIp,
) -> VerifyLoginResponse:
    sign_in = await auth.verify(body.token, ip)
    response.set_cookie(
        key=settings.auth.session_cookie_name,
        value=sign_in.session_token,
        max_age=int(settings.auth.session_ttl.total_seconds()),
        path="/",
        secure=settings.auth.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return sign_in.response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session and clear its cookie",
)
async def logout(
    response: Response, token: SessionToken, auth: AuthServiceDep, settings: SettingsDep
) -> None:
    await auth.logout(token)
    response.delete_cookie(
        key=settings.auth.session_cookie_name,
        path="/",
        secure=settings.auth.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
