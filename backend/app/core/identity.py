"""Who is making a request. Produced by `SessionService`, consumed by routes and services."""

from dataclasses import dataclass
from uuid import UUID

from app.core.enums import UserRole, UserStatus


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """A signed-in user. A plain value, never attached to a database session."""

    id: UUID
    email: str
    canonical_email: str
    role: UserRole
    status: UserStatus

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    @property
    def is_admin(self) -> bool:
        """An ACTIVE admin. A suspended account keeps its role but none of its rights,
        also on the public routes, where a suspended user still arrives as the viewer."""
        return self.is_active and self.role is UserRole.ADMIN

    @property
    def is_staff(self) -> bool:
        """ACTIVE baristas and admins may redeem coffee tickets."""
        return self.is_active and self.role in (UserRole.ADMIN, UserRole.BARISTA)
