"""Queries on `outbox`, `audit_log` and `alerts` (SPEC §5, §9)."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OutboxTopic
from app.models.ops import Alert, AuditLogEntry, OutboxEvent
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository


class OutboxRepository:
    """Not a `BaseRepository`: its `add` builds the row, so callers cannot set `published_at`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, topic: OutboxTopic, payload: dict[str, Any], now: datetime) -> OutboxEvent:
        """Queue an event in the caller's transaction: it exists exactly if that commits."""
        event = OutboxEvent(topic=topic, payload=payload, created_at=now)
        self._session.add(event)
        await self._session.flush()
        return event

    async def claim_unpublished(self, limit: int) -> Sequence[OutboxEvent]:
        """Lock up to `limit` unpublished events, oldest first, until the transaction ends.

        SKIP LOCKED passes over rows another relay holds, so relays never block each other
        and never publish the same batch at once.
        """
        statement = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None))
            .order_by(OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return (await self._session.scalars(statement)).all()

    async def mark_published(self, event_ids: Collection[int], now: datetime) -> None:
        await self._session.execute(
            update(OutboxEvent).where(OutboxEvent.id.in_(event_ids)).values(published_at=now)
        )


class AuditLogRepository(BaseRepository[AuditLogEntry]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLogEntry)


class AlertRepository(BaseRepository[Alert]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Alert)

    async def get(self, alert_id: int) -> Alert | None:
        return await self._get(alert_id, for_update=False)

    async def list_unacknowledged(self) -> Sequence[Alert]:
        """Alerts nobody acknowledged yet, newest first."""
        statement = select(Alert).where(Alert.acked_at.is_(None)).order_by(Alert.id.desc())
        return (await self._session.scalars(statement)).all()

    async def acknowledge(self, alert_id: int, user_id: UUID, now: datetime) -> bool:
        """Acknowledge an alert. False if it does not exist or was acknowledged before."""
        statement = (
            update(Alert)
            .where(Alert.id == alert_id, Alert.acked_at.is_(None))
            .values(acked_by=user_id, acked_at=now)
            .returning(Alert.id)
            .execution_options(**MATCHED_BY_DATABASE)
        )
        return (await self._session.scalars(statement)).first() is not None
