"""`AuthenticatedUser`: what a role is worth (SPEC §3, Appendix A.2).

Public routes hand whoever is signed in to the service as the viewer, a suspended user
included. There `is_admin` decides what only admins may read, so it must not be true
for an account that is suspended.
"""

from uuid import uuid7

import pytest

from app.core.enums import UserRole, UserStatus
from app.core.identity import AuthenticatedUser


def someone(role: UserRole, status: UserStatus) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uuid7(),
        email="someone@student.ethz.ch",
        canonical_email="someone@ethz.ch",
        role=role,
        status=status,
    )


@pytest.mark.parametrize("role", list(UserRole))
def test_an_active_account_has_the_rights_of_its_role(role: UserRole) -> None:
    active = someone(role, UserStatus.ACTIVE)

    assert active.is_active
    assert active.is_admin == (role is UserRole.ADMIN)
    assert active.is_staff == (role in (UserRole.ADMIN, UserRole.BARISTA))


@pytest.mark.parametrize("role", list(UserRole))
def test_a_suspended_account_keeps_its_role_and_none_of_its_rights(role: UserRole) -> None:
    suspended = someone(role, UserStatus.SUSPENDED)

    assert suspended.role is role
    assert not suspended.is_active
    assert not suspended.is_admin
    assert not suspended.is_staff
