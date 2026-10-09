"""What a route may ask for: services, the caller's identity, the client's address.

Each alias below is an `Annotated[..., Depends(...)]`; a route declares a parameter of
that type and receives the object. Services come from the application's `Container`
(`app.state.container`), which `create_app` or its lifespan puts there.

Guards (Appendix A.2):

    OptionalUser   the signed-in user, or None: public routes that show more to a user
    CurrentUser    a signed-in, ACTIVE user, else 401 / 403
    AdminUser      role ADMIN, else 403
    BaristaUser    role BARISTA or ADMIN, else 403

All four rest on `authenticate_request`. Tests replace that one function with
`app.dependency_overrides` to act as any user (the `as_user` fixture).
"""

from typing import Annotated, Final

from fastapi import Depends, Request

from app.container import Container
from app.core.config import Settings
from app.core.enums import UserStatus
from app.core.errors import ForbiddenError, UnauthenticatedError
from app.core.identity import AuthenticatedUser
from app.services.account import AccountService
from app.services.auth import AuthService
from app.services.coffee_tickets import CoffeeTicketService
from app.services.grants import GrantService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.live_quotes import LiveQuoteService
from app.services.market_lifecycle import MarketLifecycleService
from app.services.markets import MarketService
from app.services.public_config import PublicConfigService
from app.services.quotes import QuoteService
from app.services.risk import RiskService
from app.services.trading import TradingService
from app.services.voucher_codes import VoucherCodeService

SUSPENDED_MESSAGE: Final[str] = "This account is suspended."


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def _settings(container: ContainerDep) -> Settings:
    return container.settings


def _health(container: ContainerDep) -> HealthService:
    return container.health


def _public_config(container: ContainerDep) -> PublicConfigService:
    return container.public_config


def _auth(container: ContainerDep) -> AuthService:
    return container.auth


def _account(container: ContainerDep) -> AccountService:
    return container.account


def _markets(container: ContainerDep) -> MarketService:
    return container.markets


def _market_lifecycle(container: ContainerDep) -> MarketLifecycleService:
    return container.market_lifecycle


def _quotes(container: ContainerDep) -> QuoteService:
    return container.quotes


def _trading(container: ContainerDep) -> TradingService:
    return container.trading


def _history(container: ContainerDep) -> HistoryService:
    return container.history


def _live_quotes(container: ContainerDep) -> LiveQuoteService:
    return container.live_quotes


def _voucher_codes(container: ContainerDep) -> VoucherCodeService:
    return container.voucher_codes


def _coffee_tickets(container: ContainerDep) -> CoffeeTicketService:
    return container.coffee_tickets


def _risk(container: ContainerDep) -> RiskService:
    return container.risk


def _grants(container: ContainerDep) -> GrantService:
    return container.grants


SettingsDep = Annotated[Settings, Depends(_settings)]
HealthServiceDep = Annotated[HealthService, Depends(_health)]
PublicConfigServiceDep = Annotated[PublicConfigService, Depends(_public_config)]
AuthServiceDep = Annotated[AuthService, Depends(_auth)]
AccountServiceDep = Annotated[AccountService, Depends(_account)]
MarketServiceDep = Annotated[MarketService, Depends(_markets)]
MarketLifecycleServiceDep = Annotated[MarketLifecycleService, Depends(_market_lifecycle)]
QuoteServiceDep = Annotated[QuoteService, Depends(_quotes)]
TradingServiceDep = Annotated[TradingService, Depends(_trading)]
HistoryServiceDep = Annotated[HistoryService, Depends(_history)]
LiveQuoteServiceDep = Annotated[LiveQuoteService, Depends(_live_quotes)]
VoucherCodeServiceDep = Annotated[VoucherCodeService, Depends(_voucher_codes)]
CoffeeTicketServiceDep = Annotated[CoffeeTicketService, Depends(_coffee_tickets)]
RiskServiceDep = Annotated[RiskService, Depends(_risk)]
GrantServiceDep = Annotated[GrantService, Depends(_grants)]


def client_ip(request: Request) -> str:
    """The caller's address, for rate limits: what nginx saw, else the socket's peer.

    nginx sets `X-Real-IP` to the address it accepted the connection from; behind
    another proxy only `X-Forwarded-For` may be there, whose first entry is the client.
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    return request.client.host if request.client is not None else "unknown"


ClientIp = Annotated[str, Depends(client_ip)]


def session_token(request: Request, settings: SettingsDep) -> str | None:
    """The token in the session cookie, or None when the cookie is absent or empty."""
    return request.cookies.get(settings.auth.session_cookie_name) or None


SessionToken = Annotated[str | None, Depends(session_token)]


async def authenticate_request(
    token: SessionToken, container: ContainerDep
) -> AuthenticatedUser | None:
    """Who is calling, from the session cookie; None without a valid session."""
    if token is None:
        return None
    return await container.sessions.authenticate(token)


OptionalUser = Annotated[AuthenticatedUser | None, Depends(authenticate_request)]


def require_user(user: OptionalUser) -> AuthenticatedUser:
    if user is None:
        raise UnauthenticatedError
    if user.status is not UserStatus.ACTIVE:
        raise ForbiddenError(SUSPENDED_MESSAGE)
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]


def require_admin(user: CurrentUser) -> AuthenticatedUser:
    if not user.is_admin:
        raise ForbiddenError
    return user


def require_staff(user: CurrentUser) -> AuthenticatedUser:
    if not user.is_staff:
        raise ForbiddenError
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]
BaristaUser = Annotated[AuthenticatedUser, Depends(require_staff)]
