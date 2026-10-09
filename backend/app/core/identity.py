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
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def is_staff(self) -> bool:
        """Baristas and admins may redeem coffee tickets."""
        return self.role in (UserRole.ADMIN, UserRole.BARISTA)
