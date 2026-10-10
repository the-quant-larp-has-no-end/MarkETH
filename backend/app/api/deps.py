"""What a route may ask for: services and who is calling.

Each alias below is an `Annotated[..., Depends(...)]`; a route declares a parameter of
that type and receives the object. Services come from the application's `Container`
(`app.state.container`), which `create_app` or its lifespan puts there.

Who is calling (SPEC §3, Appendix A.2, A.3). The API has no sign-in of its own: the
portal in front of it names the caller in two request headers, and
`authenticate_request` is the one place where they are read. Four guards rest on it:

    Identity      whoever the portal named, with or without a profile, else 401 / 403
    CurrentUser   a caller with a profile whose account is ACTIVE, else 401 / 403
    AdminUser     role ADMIN, else 403
    BaristaUser   role BARISTA or ADMIN, else 403

A named caller without a profile gets 403 `PROFILE_REQUIRED` from `CurrentUser`; only
`PUT /me/profile`, guarded by `Identity`, lets such a caller through. Tests replace
`authenticate_request` with `app.dependency_overrides` to act as anybody (the `as_user`
fixture).

Each guard also depends on a marker of `app/api/access.py`. The marker does nothing at
request time; it is how the OpenAPI document learns which guard protects an operation.
"""

from typing import Annotated

from fastapi import Depends, Request, Security

from app.api import access
from app.container import Container
from app.core.config import Settings
from app.core.errors import (
    SUSPENDED_MESSAGE,
    ForbiddenError,
    ProfileRequiredError,
    UnauthenticatedError,
)
from app.core.identity import AuthenticatedUser, Caller, PortalIdentity, display_name
from app.services.account import AccountService
from app.services.admin_users import AdminUserService
from app.services.codes import CodeService
from app.services.grants import GrantService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.leaderboard import LeaderboardService
from app.services.live_quotes import LiveQuoteService
from app.services.market_lifecycle import MarketLifecycleService
from app.services.markets import MarketService
from app.services.notices import NoticeService
from app.services.profile import ProfileService
from app.services.public_config import PublicConfigService
from app.services.quotes import QuoteService
from app.services.risk import RiskService
from app.services.trading import TradingService
from app.services.vouchers import VoucherService


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


def _profile(container: ContainerDep) -> ProfileService:
    return container.profile


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


def _codes(container: ContainerDep) -> CodeService:
    return container.codes


def _vouchers(container: ContainerDep) -> VoucherService:
    return container.vouchers


def _leaderboard(container: ContainerDep) -> LeaderboardService:
    return container.leaderboard


def _risk(container: ContainerDep) -> RiskService:
    return container.risk


def _notices(container: ContainerDep) -> NoticeService:
    return container.notices


def _grants(container: ContainerDep) -> GrantService:
    return container.grants


def _admin_users(container: ContainerDep) -> AdminUserService:
    return container.admin_users


SettingsDep = Annotated[Settings, Depends(_settings)]
HealthServiceDep = Annotated[HealthService, Depends(_health)]
PublicConfigServiceDep = Annotated[PublicConfigService, Depends(_public_config)]
ProfileServiceDep = Annotated[ProfileService, Depends(_profile)]
AccountServiceDep = Annotated[AccountService, Depends(_account)]
MarketServiceDep = Annotated[MarketService, Depends(_markets)]
MarketLifecycleServiceDep = Annotated[MarketLifecycleService, Depends(_market_lifecycle)]
QuoteServiceDep = Annotated[QuoteService, Depends(_quotes)]
TradingServiceDep = Annotated[TradingService, Depends(_trading)]
HistoryServiceDep = Annotated[HistoryService, Depends(_history)]
LiveQuoteServiceDep = Annotated[LiveQuoteService, Depends(_live_quotes)]
CodeServiceDep = Annotated[CodeService, Depends(_codes)]
VoucherServiceDep = Annotated[VoucherService, Depends(_vouchers)]
LeaderboardServiceDep = Annotated[LeaderboardService, Depends(_leaderboard)]
RiskServiceDep = Annotated[RiskService, Depends(_risk)]
NoticeServiceDep = Annotated[NoticeService, Depends(_notices)]
GrantServiceDep = Annotated[GrantService, Depends(_grants)]
AdminUserServiceDep = Annotated[AdminUserService, Depends(_admin_users)]


def _sent_once(request: Request, header: str) -> str | None:
    """The value of a header the request carries exactly once, else None.

    A header that arrives twice says two things, and nothing here may pick one: the
    first would be whatever a client put in front of the portal's own line.
    """
    values = request.headers.getlist(header)
    return values[0] if len(values) == 1 else None


async def authenticate_request(
    request: Request, settings: SettingsDep, container: ContainerDep
) -> Caller | None:
    """Who is calling, from the two headers of the portal; None when it names nobody.

    Nobody is named when the id header is absent, blank, or there more than once. The
    identity service is not asked then, and the guards answer 401. Otherwise it gets
    the id exactly as it arrived, and answers one that is not an allowed address with
    403.

    The name it gets is `display_name` of the name header: percent-decoded, which is
    how the portal sends anything outside ASCII, without control and format
    characters, with its whitespace collapsed, and cut to the width a profile keeps.
    A name header that is absent, or there more than once, is no name: "".
    """
    names = settings.identity
    user_id = _sent_once(request, names.header_user_id)
    if user_id is None or not user_id.strip():
        return None
    user_name = _sent_once(request, names.header_user_name)
    return await container.identity.authenticate(user_id, display_name(user_name or ""))


OptionalCaller = Annotated[Caller | None, Depends(authenticate_request)]


def require_identity(
    caller: OptionalCaller, _documented: Annotated[None, Security(access.NEEDS_IDENTITY)]
) -> PortalIdentity:
    """Whoever the portal named. A profile is not needed, but a suspended one is refused."""
    if caller is None:
        raise UnauthenticatedError
    if caller.user is not None and not caller.user.is_active:
        raise ForbiddenError(SUSPENDED_MESSAGE)
    return caller.identity


Identity = Annotated[PortalIdentity, Depends(require_identity)]


def require_user(
    caller: OptionalCaller, _documented: Annotated[None, Security(access.NEEDS_PROFILE)]
) -> AuthenticatedUser:
    if caller is None:
        raise UnauthenticatedError
    if caller.user is None:
        raise ProfileRequiredError(name=caller.identity.name, email=caller.identity.email)
    if not caller.user.is_active:
        raise ForbiddenError(SUSPENDED_MESSAGE)
    return caller.user


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]


def require_admin(
    user: CurrentUser, _documented: Annotated[None, Security(access.NEEDS_ADMIN)]
) -> AuthenticatedUser:
    if not user.is_admin:
        raise ForbiddenError
    return user


def require_staff(
    user: CurrentUser, _documented: Annotated[None, Security(access.NEEDS_STAFF)]
) -> AuthenticatedUser:
    if not user.is_staff:
        raise ForbiddenError
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(require_admin)]
BaristaUser = Annotated[AuthenticatedUser, Depends(require_staff)]
