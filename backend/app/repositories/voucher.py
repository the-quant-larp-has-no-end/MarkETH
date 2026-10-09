"""Queries on `voucher_grants`, `voucher_code_batches` and `voucher_codes` (SPEC §2, A.6)."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import VoucherCodeStatus
from app.models.voucher import VoucherCode, VoucherCodeBatch, VoucherGrant
from app.repositories.base import MATCHED_BY_DATABASE, BaseRepository, sum_micro


@dataclass(frozen=True, slots=True)
class CodeBatchCounts:
    """A batch and how many of its codes are in each state."""

    batch: VoucherCodeBatch
    active: int
    redeemed: int
    revoked: int


class VoucherGrantRepository(BaseRepository[VoucherGrant]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VoucherGrant)

    async def insert_if_absent(
        self, user_id: UUID, period: str, now: datetime
    ) -> VoucherGrant | None:
        """Record a grant for (user, period); None if that grant already exists.

        Only the caller that gets a row back may book the credit. A concurrent attempt
        waits in the INSERT for this transaction and then inserts nothing (SPEC §2).
        """
        statement = (
            insert(VoucherGrant)
            .values(user_id=user_id, period=period, created_at=now)
            .on_conflict_do_nothing(index_elements=[VoucherGrant.user_id, VoucherGrant.period])
            .returning(VoucherGrant)
        )
        return await self._session.scalar(statement)

    async def list_periods(self, user_id: UUID) -> Sequence[str]:
        """Periods already granted to a user: `SIGNUP` and `YYYY-MM` values, ascending."""
        statement = (
            select(VoucherGrant.period)
            .where(VoucherGrant.user_id == user_id)
            .order_by(VoucherGrant.period)
        )
        return (await self._session.scalars(statement)).all()

    async def set_entry(self, grant_id: UUID, entry_id: UUID) -> None:
        """Link a grant to the journal entry that booked it."""
        await self._session.execute(
            update(VoucherGrant).where(VoucherGrant.id == grant_id).values(entry_id=entry_id)
        )


class VoucherCodeBatchRepository(BaseRepository[VoucherCodeBatch]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VoucherCodeBatch)

    async def get(self, batch_id: UUID, *, for_update: bool = False) -> VoucherCodeBatch | None:
        return await self._get(batch_id, for_update=for_update)

    async def list_with_counts(self) -> Sequence[CodeBatchCounts]:
        """Every batch with its code counts, newest first."""

        def count(status: VoucherCodeStatus) -> ColumnElement[int]:
            return func.count(VoucherCode.id).filter(VoucherCode.status == status)

        statement = (
            select(
                VoucherCodeBatch,
                count(VoucherCodeStatus.ACTIVE),
                count(VoucherCodeStatus.REDEEMED),
                count(VoucherCodeStatus.REVOKED),
            )
            .outerjoin(VoucherCode, VoucherCode.batch_id == VoucherCodeBatch.id)
            .group_by(VoucherCodeBatch.id)
            .order_by(VoucherCodeBatch.created_at.desc(), VoucherCodeBatch.id.desc())
        )
        return [
            CodeBatchCounts(batch=batch, active=active, redeemed=redeemed, revoked=revoked)
            for batch, active, redeemed, revoked in await self._session.execute(statement)
        ]


class VoucherCodeRepository(BaseRepository[VoucherCode]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, VoucherCode)

    async def get_by_hash(self, code_hash: str, *, for_update: bool = False) -> VoucherCode | None:
        """Lock the code before redeeming it, so two parallel redemptions succeed once."""
        statement = select(VoucherCode).where(VoucherCode.code_hash == code_hash)
        return await self._session.scalar(self._locked(statement, for_update))

    async def user_has_redeemed_in_batch(self, user_id: UUID, batch_id: UUID) -> bool:
        statement = select(
            exists().where(
                VoucherCode.batch_id == batch_id,
                VoucherCode.status == VoucherCodeStatus.REDEEMED,
                VoucherCode.redeemed_by == user_id,
            )
        )
        return (await self._session.execute(statement)).scalar_one()

    async def revoke(self, batch_id: UUID, code_hashes: Collection[str] | None) -> int:
        """Revoke the still-active codes of a batch and return how many that was.

        `code_hashes` narrows it to those codes; None means the whole batch. A code that
        is redeemed, also by a redemption committing while this waits, stays redeemed.
        """
        statement = (
            update(VoucherCode)
            .where(
                VoucherCode.batch_id == batch_id,
                VoucherCode.status == VoucherCodeStatus.ACTIVE,
            )
            .values(status=VoucherCodeStatus.REVOKED)
            .returning(VoucherCode.id)
            .execution_options(**MATCHED_BY_DATABASE)
        )
        if code_hashes is not None:
            statement = statement.where(VoucherCode.code_hash.in_(code_hashes))
        return len((await self._session.scalars(statement)).all())

    async def issued_value_since(self, since: datetime) -> int:
        """Value of the non-revoked codes of batches created at or after `since`.

        This is what counts against `MAX_CODE_ISSUANCE_PER_MONTH` (A.6).
        """
        statement = (
            select(sum_micro(VoucherCodeBatch.value_micro))
            .select_from(VoucherCode)
            .join(VoucherCodeBatch, VoucherCodeBatch.id == VoucherCode.batch_id)
            .where(
                VoucherCodeBatch.created_at >= since,
                VoucherCode.status != VoucherCodeStatus.REVOKED,
            )
        )
        return (await self._session.execute(statement)).scalar_one()

    async def unredeemed_value(self, now: datetime) -> int:
        """Value of the codes that could still be redeemed at `now`: active and unexpired."""
        statement = (
            select(sum_micro(VoucherCodeBatch.value_micro))
            .select_from(VoucherCode)
            .join(VoucherCodeBatch, VoucherCodeBatch.id == VoucherCode.batch_id)
            .where(
                VoucherCode.status == VoucherCodeStatus.ACTIVE,
                VoucherCodeBatch.expires_at > now,
            )
        )
        return (await self._session.execute(statement)).scalar_one()
