"""Operational records: `outbox`, `audit_log`, `alerts` (SPEC §5, §7, §9).

* The outbox is written in the same transaction as a trade; the relay drains it into
  ScyllaDB. A row with `published_at IS NULL` is still owed.
* The audit log records every admin action with the state before and after.
* An alert is what the circuit breaker and the risk page raise for admins.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AlertSeverity, OutboxTopic
from app.db.base import Base, enum_column


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # The relay only ever asks for unpublished rows in id order; keep that index tiny.
        Index("ix_outbox_unpublished", "id", postgresql_where=text("published_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    topic: Mapped[OutboxTopic] = mapped_column(enum_column(OutboxTopic, "topic"))
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    published_at: Mapped[datetime | None]


class AuditLogEntry(Base):
    """`actor_id` is NULL when the system itself acted, e.g. the circuit breaker."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime]


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("(acked_by IS NULL) = (acked_at IS NULL)", name="acked_by_iff_acked_at"),
    )

    id: Mapped[int] = mapped_column(Identity(always=True), primary_key=True)
    severity: Mapped[AlertSeverity] = mapped_column(enum_column(AlertSeverity, "severity"))
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]]
    created_at: Mapped[datetime]
    acked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    acked_at: Mapped[datetime | None]
