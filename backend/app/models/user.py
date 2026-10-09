"""Users and how they sign in: `users`, `login_tokens`, `sessions` (SPEC §3, §7, A.3).

A user row exists only after a magic link was confirmed. `canonical_email` is the
identity: both mail aliases of one person map to it, so its unique index is what stops
a second signup grant.
"""

from datetime import datetime
from typing import Final
from uuid import UUID, uuid7

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import UserRole, UserStatus
from app.db.base import SHA256_HEX_LENGTH, Base, enum_column, sha256_hex_check

EMAIL_MAX_LENGTH: Final[int] = 320
"""Longest address RFC 5321 allows: 64 (local part) + 1 + 255 (domain)."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    canonical_email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole, "role"))
    status: Mapped[UserStatus] = mapped_column(enum_column(UserStatus, "status"))
    created_at: Mapped[datetime]


class LoginToken(Base):
    """A magic link. Only the hash of its token is stored; `consumed_at` makes it single use."""

    __tablename__ = "login_tokens"
    __table_args__ = (sha256_hex_check("token_hash"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    token_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]


class UserSession(Base):
    """A signed-in browser. `id` is the SHA-256 of the cookie's token, never the token."""

    __tablename__ = "sessions"
    __table_args__ = (sha256_hex_check("id"),)

    id: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
