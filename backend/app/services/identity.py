"""Who a request comes from (SPEC §3, Appendix A.2, A.3).

MarkETH has no sign-in of its own. The portal in front of it signs a person in through
ETH single sign-on and names them in two request headers; `app/api/deps.py` reads the
headers and asks this service what they amount to. Nothing is remembered between
requests: Postgres is asked every time, so a profile that was just created, a role an
admin just set and a suspension all hold from the next request on. Who is a bootstrap
admin is what `.env` said when the process started.
"""

from app.core.enums import UserRole
from app.core.identity import AuthenticatedUser, Caller, PortalIdentity
from app.services import UowFactory
from app.services.email_policy import EmailPolicy
from app.services.upkeep import UserUpkeepService


class IdentityService:
    def __init__(
        self,
        uow_factory: UowFactory,
        email_policy: EmailPolicy,
        upkeep: UserUpkeepService,
    ) -> None:
        self._uow_factory = uow_factory
        self._email_policy = email_policy
        self._upkeep = upkeep

    async def authenticate(self, user_id: str, user_name: str) -> Caller:
        """The caller the portal's two headers stand for.

        `user_id` is the id header exactly as it arrived. `user_name` is the display
        name the API layer made of the name header with
        `app.core.identity.display_name`: decoded, cleaned and cut, and "" when the
        portal sent no name or sent the header more than once. It is taken as it is;
        nothing here cleans it again. The API layer calls this only for a request
        whose id header is there once and is not blank.

        1. `email = EmailPolicy.identify(user_id)`: the id in lower case. An id that is
           not an allowed address leaves as `ForbiddenError` (403) with
           `details.allowed_domains`.
        2. One read transaction, no lock: `users.get_by_email(email)`.
        3. No row: return `Caller(PortalIdentity(email, user_name, role), user=None)`
           with `role = EmailPolicy.effective_role(email, UserRole.USER)`, which is
           ADMIN for a bootstrap admin and USER for everybody else. The person has no
           profile yet; the guards answer `PROFILE_REQUIRED` everywhere except on
           `PUT /me/profile`, which creates it.
        4. A row: `role = EmailPolicy.effective_role(email, row.role)`, the role of the
           row unless the address is a bootstrap admin. After that transaction has
           ended, `await upkeep.run_if_due(row)`. Then return
           `Caller(PortalIdentity(email, user_name, role), AuthenticatedUser(id=row.id,
           email=row.email, name=row.name, username=row.username, role=role,
           status=row.status))`. The identity carries the name the portal sends now,
           the user the name the profile kept.

        The role of a row is what an admin last set, read with this request. An address
        taken off `ADMIN_EMAILS` has the role of its row at the next start of the API.

        A SUSPENDED row is returned like any other, with that status; the guards refuse
        it. Upkeep still runs for it, because a refund is owed whatever the status.

        Nothing is caught here. A lost database connection leaves this method and
        becomes the API's 503, whether the lookup lost it or `run_if_due` raised it.
        `run_if_due` lets nothing else through.
        """
        email = self._email_policy.identify(user_id)

        async with self._uow_factory() as uow:
            row = await uow.users.get_by_email(email)
        stored_role = UserRole.USER if row is None else row.role
        role = self._email_policy.effective_role(email, stored_role)
        identity = PortalIdentity(email=email, name=user_name, role=role)
        if row is None:
            return Caller(identity=identity, user=None)

        await self._upkeep.run_if_due(row)
        return Caller(
            identity=identity,
            user=AuthenticatedUser(
                id=row.id,
                email=row.email,
                name=row.name,
                username=row.username,
                role=role,
                status=row.status,
            ),
        )
