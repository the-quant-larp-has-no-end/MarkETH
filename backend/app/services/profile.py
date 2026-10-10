"""A person's profile: the username and the degree they state (SPEC §3, Appendix A.3).

The portal says who somebody is; the profile is what that person adds. Saving it for
the first time is what makes them a user: the `users` row, their ledger account and the
signup grant are written together or not at all. Saving it again changes the username
and the degree, as often as they like.
"""

from typing import Final

from app.core import degrees
from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import UserStatus
from app.core.errors import (
    SUSPENDED_MESSAGE,
    ForbiddenError,
    UsernameTakenError,
    ValidationFailedError,
)
from app.core.identity import AuthenticatedUser, PortalIdentity
from app.models.user import User
from app.schemas.profile import ProfileSavedResponse, SaveProfileRequest
from app.services import UowFactory
from app.services.account import AccountService
from app.services.grants import GrantService

RESERVED_USERNAME_MESSAGE: Final[str] = "This username is reserved. Pick another one."
"""Message of the `ValidationFailedError` that refuses a name of `RESERVED_USERNAMES`."""

UNKNOWN_PROGRAMME_MESSAGE: Final[str] = "Pick a programme of the degree level you chose."
"""Message of the `ValidationFailedError` that refuses a degree the catalogue does not
list as a pair."""

USERNAME_FIELD: Final[str] = "username"
PROGRAMME_FIELD: Final[str] = "degree_programme"
"""`details.field` of the two fields a save can be refused for."""


class ProfileService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        grants: GrantService,
        account: AccountService,
    ) -> None:
        identity = settings.identity
        self._uow_factory = uow_factory
        self._username_min_length = identity.username_min_length
        self._username_max_length = identity.username_max_length
        self._reserved_usernames = frozenset(identity.reserved_usernames)
        self._clock = clock
        self._grants = grants
        self._account = account

    async def save(
        self, identity: PortalIdentity, request: SaveProfileRequest
    ) -> ProfileSavedResponse:
        """Create the caller's profile, or change it (`PUT /me/profile`).

        Before any transaction, in this order, each a `ValidationFailedError` (422)
        with `details={"field": ...}` naming the field and a message that says what
        is asked for:

        * `len(request.username)` outside `USERNAME_MIN_LENGTH` to
          `USERNAME_MAX_LENGTH`: `username`. (Its characters passed the request model:
          lower-case letters, digits and underscore.)
        * `request.username` is one of `RESERVED_USERNAMES`: `username`, with
          `RESERVED_USERNAME_MESSAGE`. Nobody gets such a name, whatever their role
          and whether they are creating a profile or changing one.
        * `degrees.is_programme(request.degree_level, request.degree_programme)` is
          false: `degree_programme`, with `UNKNOWN_PROGRAMME_MESSAGE`.

        One transaction:

        1. `uow.advisory_lock(f"profile:{identity.email}")`. Two saves of one person
           (a double tap, two tabs) run one after the other.
        2. `uow.advisory_lock(f"username:{request.username}")`: the lock is on the
           name itself, which has one spelling. Two people who want one name run one
           after the other too.
        3. `holder = users.get_by_username(request.username)`, an exact match. A
           holder whose `email` is not `identity.email` raises `UsernameTakenError`
           (409, `details.field = "username"`). Saving the name one already holds is
           no clash.
        4. `user = users.get_by_email(identity.email, for_update=True)`.
        5. No row, so this save creates the profile: `users.add(User(email=
           identity.email, name=identity.name, username, degree_level,
           degree_programme, status=ACTIVE, created_at=now))`, then
           `account = accounts.create_user_account(user.id)` and
           `signup_granted = GrantService.grant_signup(uow, user, account)`.
        6. A row, so this save edits it. A status other than ACTIVE raises
           `ForbiddenError(SUSPENDED_MESSAGE)` (403). Otherwise set `username`,
           `degree_level` and `degree_programme`, and `name` when `identity.name` is
           not empty. `signup_granted = False`; nothing is booked.

        After the commit the answer is built the way `GET /me` builds it:
        `AccountService.me(AuthenticatedUser(id=user.id, email=user.email,
        name=user.name, username=user.username, role=identity.role,
        status=user.status))`, returned as a `ProfileSavedResponse` with
        `signup_granted`. The role is the identity's: it is in no column. That read
        is a transaction of its own. If Postgres is lost between the two, the save
        stands and the request is answered 503; sending it again edits the profile
        and says `signup_granted: false`.

        Both locks last until the transaction ends, so the second of two racers reads
        what the first one committed: one person ends with one row, one account and
        one signup grant, and of two people one gets the name and the other the 409.
        Every transaction takes them in this order and takes no other advisory lock,
        so they cannot wait on each other in a cycle. `uq_users_email` and
        `uq_users_username` remain as the database's own word on both rules; if
        either ever fires here it is a bug, and it surfaces as a 500.

        A failure anywhere in the transaction leaves nothing behind: no user without
        an account, no account without its grant.
        """
        username = request.username
        if not self._username_min_length <= len(username) <= self._username_max_length:
            raise ValidationFailedError(
                f"A username has {self._username_min_length} to "
                f"{self._username_max_length} characters.",
                details={"field": USERNAME_FIELD},
            )
        if username in self._reserved_usernames:
            raise ValidationFailedError(
                RESERVED_USERNAME_MESSAGE, details={"field": USERNAME_FIELD}
            )
        if not degrees.is_programme(request.degree_level, request.degree_programme):
            raise ValidationFailedError(
                UNKNOWN_PROGRAMME_MESSAGE, details={"field": PROGRAMME_FIELD}
            )

        async with self._uow_factory() as uow:
            await uow.advisory_lock(f"profile:{identity.email}")
            await uow.advisory_lock(f"username:{username}")
            holder = await uow.users.get_by_username(username)
            if holder is not None and holder.email != identity.email:
                raise UsernameTakenError
            user = await uow.users.get_by_email(identity.email, for_update=True)
            if user is None:
                user = await uow.users.add(
                    User(
                        email=identity.email,
                        name=identity.name,
                        username=username,
                        degree_level=request.degree_level,
                        degree_programme=request.degree_programme,
                        status=UserStatus.ACTIVE,
                        created_at=self._clock.now(),
                    )
                )
                account = await uow.accounts.create_user_account(user.id)
                signup_granted = await self._grants.grant_signup(uow, user, account)
            else:
                if user.status is not UserStatus.ACTIVE:
                    raise ForbiddenError(SUSPENDED_MESSAGE)
                user.username = username
                user.degree_level = request.degree_level
                user.degree_programme = request.degree_programme
                if identity.name:
                    user.name = identity.name
                signup_granted = False

        me = await self._account.me(
            AuthenticatedUser(
                id=user.id,
                email=user.email,
                name=user.name,
                username=user.username,
                role=identity.role,
                status=user.status,
            )
        )
        return ProfileSavedResponse(**me.model_dump(), signup_granted=signup_granted)
