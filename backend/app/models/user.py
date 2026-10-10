"""People with a profile: `users` (SPEC §3, §7, A.3).

The portal signs a person in; a row exists once that person has saved a profile.
`email` is the portal's id in lower case and unique, which is what gives one id one
wallet and one signup grant. The role is not a column: it is derived from `.env` on
every request.

A username is lower case and unique. It has one spelling, so `neo` is one name and the
database needs no rule about case to keep it that way.
"""

from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.degrees import PROGRAMME_MAX_LENGTH
from app.core.enums import DegreeLevel, UserStatus
from app.core.identity import DISPLAY_NAME_MAX_LENGTH, USERNAME_PATTERN
from app.db.base import Base, enum_column

EMAIL_MAX_LENGTH: Final[int] = 320
"""Longest address RFC 5321 allows: 64 (local part) + 1 + 255 (domain)."""


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(f"username ~ '{USERNAME_PATTERN}'", name="username_format"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    # What `app.core.identity.display_name` made of the portal's name header.
    name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH))
    # TEXT, bounded by the CHECK above.
    username: Mapped[str] = mapped_column(Text, unique=True)
    degree_level: Mapped[DegreeLevel] = mapped_column(enum_column(DegreeLevel, "degree_level"))
    degree_programme: Mapped[str] = mapped_column(String(PROGRAMME_MAX_LENGTH))
    status: Mapped[UserStatus] = mapped_column(enum_column(UserStatus, "status"))
    created_at: Mapped[datetime]
