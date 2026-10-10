"""The audit log: who did what to what (SPEC §5, §7, §10, Appendix A.5).

Every admin action writes one `audit_log` row in the same transaction as the change it
describes, so the record exists exactly if the change does. One read is audited too:
an admin who opens the codes of a batch gets them from the transaction that records
it. The circuit breaker writes its own rows with no actor.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.clock import Clock
from app.models.ops import AuditLogEntry
from app.repositories.unit_of_work import UnitOfWork


class AuditAction(StrEnum):
    """`audit_log.action`: one value per auditable operation."""

    MARKET_APPROVE = "market.approve"
    MARKET_REJECT = "market.reject"
    MARKET_HALT = "market.halt"
    MARKET_RESUME = "market.resume"
    MARKET_CLOSE = "market.close"
    MARKET_RESOLVE = "market.resolve"
    MARKET_VOID = "market.void"
    CODE_BATCH_CREATE = "code_batch.create"
    CODE_BATCH_REVOKE = "code_batch.revoke"
    CODE_BATCH_READ_CODES = "code_batch.read_codes"
    ALERT_ACK = "alert.ack"
    MONTHLY_GRANT_RUN = "grants.monthly_run"
    USER_SUSPEND = "user.suspend"
    USER_REINSTATE = "user.reinstate"
    USER_SET_ROLE = "user.set_role"
    USER_ADJUST_BALANCE = "user.adjust_balance"
    QUESTION_ANSWER = "question.answer"
    QUESTION_REMOVE = "question.remove"
    CHAT_MESSAGE_REMOVE = "chat_message.remove"
    CHAT_MESSAGE_REMOVE_OF_USER = "chat_message.remove_of_user"
    BROADCAST_APPROVE = "broadcast.approve"
    BROADCAST_REFUSE = "broadcast.refuse"
    BROADCAST_START = "broadcast.start"
    BROADCAST_END = "broadcast.end"


class AuditTarget(StrEnum):
    """The kind of thing an action was done to; see `AuditService.target`."""

    MARKET = "market"
    CODE_BATCH = "code_batch"
    ALERT = "alert"
    GRANTS = "grants"
    USER = "user"
    QUESTION = "question"
    CHAT_MESSAGE = "chat_message"
    BROADCAST = "broadcast"


class AuditService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @staticmethod
    def target(kind: AuditTarget, ident: object) -> str:
        """`audit_log.target` of one row, e.g. `market:0199c1d4-…` or `alert:17`."""
        return f"{kind}:{ident}"

    async def record(
        self,
        uow: UnitOfWork,
        *,
        actor_id: UUID | None,
        action: str,
        target: str,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
    ) -> AuditLogEntry:
        """Write one audit row inside the caller's transaction.

        `actor_id` is None when the system acted by itself (the circuit breaker).
        `action` is an `AuditAction` and `target` comes from `AuditService.target`.
        `before` and `after` describe the state the action changed, or carry its
        parameters (a reason, a count); either may be None. Both are stored as JSONB,
        so they hold JSON values only: UUIDs and instants as strings, amounts as
        integer micro-units under `*_micro` keys, never a float and never a secret.
        """
        return await uow.audit.add(
            AuditLogEntry(
                actor_id=actor_id,
                action=str(action),
                target=target,
                before=None if before is None else dict(before),
                after=None if after is None else dict(after),
                created_at=self._clock.now(),
            )
        )
