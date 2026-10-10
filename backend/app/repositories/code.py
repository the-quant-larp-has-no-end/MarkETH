"""Queries on `code_batches` and `codes` (SPEC §2, A.6)."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import CodeStatus
from app.models.code import Code, CodeBatch
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository, sum_micro


@dataclass(frozen=True, slots=True)
class CodeBatchCounts:
    """A batch and how many of its codes are in each state."""

    batch: CodeBatch
    active: int
    redeemed: int
    revoked: int


class CodeBatchRepository(BaseRepository[CodeBatch]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, CodeBatch)

    async def get(self, batch_id: UUID, *, for_update: bool = False) -> CodeBatch | None:
        return await self._get(batch_id, for_update=for_update)

    async def list_with_counts(self) -> Sequence[CodeBatchCounts]:
        """Every batch with its code counts, newest first."""
        statement = _batches_with_counts().order_by(
            CodeBatch.created_at.desc(), CodeBatch.id.desc()
        )
        return [
            CodeBatchCounts(batch=batch, active=active, redeemed=redeemed, revoked=revoked)
            for batch, active, redeemed, revoked in await self._session.execute(statement)
        ]

    async def get_with_counts(self, batch_id: UUID) -> CodeBatchCounts | None:
        """One batch with its code counts as they are now, or None if there is no such batch."""
        statement = _batches_with_counts().where(CodeBatch.id == batch_id)
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        batch, active, redeemed, revoked = row
        return CodeBatchCounts(batch=batch, active=active, redeemed=redeemed, revoked=revoked)


def _batches_with_counts() -> Select[CodeBatch, int, int, int]:
    """Batches with how many of their codes are active, redeemed and revoked."""

    def count(status: CodeStatus) -> ColumnElement[int]:
        return func.count(Code.id).filter(Code.status == status)

    return (
        select(
            CodeBatch,
            count(CodeStatus.ACTIVE),
            count(CodeStatus.REDEEMED),
            count(CodeStatus.REVOKED),
        )
        .outerjoin(Code, Code.batch_id == CodeBatch.id)
        .group_by(CodeBatch.id)
    )


class CodeRepository(BaseRepository[Code]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Code)

    async def get_by_hash(self, code_hash: str, *, for_update: bool = False) -> Code | None:
        """Lock the code before redeeming it, so two parallel redemptions succeed once."""
        statement = select(Code).where(Code.code_hash == code_hash)
        return await self._session.scalar(self._locked(statement, for_update))

    async def list_active_codes(self, batch_id: UUID) -> Sequence[str]:
        """The codes of a batch that are still ACTIVE, as stored, in the order they
        were created. Every ACTIVE row holds its code and no other row does
        (`ck_codes_code_iff_active`). Whether the batch has expired is the caller's
        to ask."""
        statement = (
            select(Code.code)
            .where(Code.batch_id == batch_id, Code.status == CodeStatus.ACTIVE)
            .order_by(Code.id)
        )
        return [code for code in await self._session.scalars(statement) if code is not None]

    async def user_has_redeemed_in_batch(self, user_id: UUID, batch_id: UUID) -> bool:
        statement = select(
            exists().where(
                Code.batch_id == batch_id,
                Code.status == CodeStatus.REDEEMED,
                Code.redeemed_by == user_id,
            )
        )
        return (await self._session.execute(statement)).scalar_one()

    async def revoke(self, batch_id: UUID, code_hashes: Collection[str] | None) -> int:
        """Revoke the still-active codes of a batch and return how many that was.

        `code_hashes` narrows it to those codes; None means the whole batch. A code that
        is redeemed, also by a redemption committing while this waits, stays redeemed.
        The same statement clears the code of each row it revokes: only an ACTIVE row
        keeps one (`ck_codes_code_iff_active`).
        """
        statement = (
            update(Code)
            .where(
                Code.batch_id == batch_id,
                Code.status == CodeStatus.ACTIVE,
            )
            .values(status=CodeStatus.REVOKED, code=None)
            .returning(Code.id)
            .execution_options(**MATCHED_BY_DATABASE)
        )
        if code_hashes is not None:
            statement = statement.where(Code.code_hash.in_(code_hashes))
        return len((await self._session.scalars(statement)).all())

    async def issued_value_since(self, since: datetime) -> int:
        """Value of the non-revoked codes of batches created at or after `since`.

        This is what counts against `MAX_CODE_ISSUANCE_PER_MONTH` (A.6).
        """
        statement = (
            select(sum_micro(CodeBatch.value_micro))
            .select_from(Code)
            .join(CodeBatch, CodeBatch.id == Code.batch_id)
            .where(
                CodeBatch.created_at >= since,
                Code.status != CodeStatus.REVOKED,
            )
        )
        return (await self._session.execute(statement)).scalar_one()

    async def unredeemed_value(self, now: datetime) -> int:
        """Value of the codes that could still be redeemed at `now`: active and unexpired."""
        statement = (
            select(sum_micro(CodeBatch.value_micro))
            .select_from(Code)
            .join(CodeBatch, CodeBatch.id == Code.batch_id)
            .where(
                Code.status == CodeStatus.ACTIVE,
                CodeBatch.expires_at > now,
            )
        )
        return (await self._session.execute(statement)).scalar_one()
