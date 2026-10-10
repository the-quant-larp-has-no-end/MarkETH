"""User management (SPEC §3, §10, Appendix A.2, A.3, A.8).

An admin lists the users, suspends and reinstates an account, sets a role and credits or
debits a balance. Suspending, reinstating and setting a role answer with the user as they
are afterwards; an adjustment answers with what was booked.
"""

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import Field

from app.core.enums import DegreeLevel, UserRole, UserStatus
from app.schemas.common import (
    CreditAmount,
    CursorPage,
    NonZeroCreditAmount,
    PageQuery,
    RequestModel,
    ResponseModel,
    SignedCreditAmount,
)
from app.schemas.markets import ShortText

SEARCH_TEXT_MAX_LENGTH: Final[int] = 100
"""The longest text `q` takes. Long enough for a name or an address typed in full."""


class AdminUserListQuery(PageQuery):
    """Query string of `GET /admin/users`: whom to list. Every filter is optional, and
    all that are given must hold."""

    q: str | None = Field(default=None, min_length=1, max_length=SEARCH_TEXT_MAX_LENGTH)
    """Lists the users whose username, name or address contains this text, whatever the
    case of its letters. Leave it out to list everybody: an empty one is rejected."""

    role: UserRole | None = None
    """The role a user has in effect, as `AdminUserItem.role` reports it: `ADMIN` lists
    the bootstrap admins too, and no other role lists one of them."""

    status: UserStatus | None = None


class AdminUserItem(ResponseModel):
    """A user as the admin panel shows them."""

    id: UUID
    email: str
    """The address the portal identifies the user by."""

    name: str
    """The display name the portal sent when the profile was last saved. May be empty."""

    username: str
    degree_level: DegreeLevel
    degree_programme: str
    role: UserRole
    """The role the user has in effect: ADMIN for a bootstrap admin, otherwise the role
    that was set for the account."""

    status: UserStatus
    bootstrap_admin: bool
    """True for an address the server's configuration names as an admin. Nobody can
    change the role of such an account or suspend it."""

    balance_cr: CreditAmount
    created_at: datetime


class AdminUserListResponse(CursorPage[AdminUserItem]):
    """`GET /admin/users`, newest profile first."""


class SuspendUserRequest(RequestModel):
    """`POST /admin/users/{id}/suspend`. The reason goes to the audit log."""

    reason: ShortText


class ReinstateUserRequest(RequestModel):
    """`POST /admin/users/{id}/reinstate`. The reason goes to the audit log."""

    reason: ShortText


class SetUserRoleRequest(RequestModel):
    """`POST /admin/users/{id}/role`."""

    role: UserRole
    """The role the account has from now on. Setting the one it already has changes
    nothing."""


class AdjustBalanceRequest(RequestModel):
    """`POST /admin/users/{id}/adjustments`: credit or debit a user's balance."""

    amount_cr: NonZeroCreditAmount
    """Above zero to add to the balance, below zero to take from it. A debit of more
    than the account holds is refused with `INSUFFICIENT_BALANCE`."""

    reason: ShortText
    """Why. It goes to the audit log; the user's ledger shows the amount, not this."""


class BalanceAdjustmentResponse(ResponseModel):
    """A booked adjustment. Sending the same Idempotency-Key again returns it unchanged."""

    user_id: UUID
    amount_cr: SignedCreditAmount
    """What was booked, with its sign."""

    balance_cr: CreditAmount
    """The user's balance right after the adjustment."""

    created_at: datetime
