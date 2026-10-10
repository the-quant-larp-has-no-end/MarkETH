"""Queries on `vouchers` (SPEC §2, §5, A.6)."""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import VoucherStatus
from app.models.voucher import Voucher
from app.repositories.base import BaseRepository, sum_micro


class VoucherRepository(BaseRepository[Voucher]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Voucher)

    async def get_owned(
        self, voucher_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> Voucher | None:
        """The voucher with this id if `user_id` holds it, else None.

        The row is selected by its id and its owner together, so a voucher of somebody
        else is neither read nor locked, and looks exactly like one that does not
        exist. Lock it before replacing its code.
        """
        statement = select(Voucher).where(Voucher.id == voucher_id, Voucher.user_id == user_id)
        return await self._session.scalar(self._locked(statement, for_update))

    async def get_by_hash(self, code_hash: str, *, for_update: bool = False) -> Voucher | None:
        """Lock the voucher before redeeming it, so a second scan is rejected."""
        statement = select(Voucher).where(Voucher.code_hash == code_hash)
        return await self._session.scalar(self._locked(statement, for_update))

    async def list_for_user(self, user_id: UUID) -> Sequence[Voucher]:
        """A user's vouchers in every status, newest first."""
        statement = select(Voucher).where(Voucher.user_id == user_id).order_by(Voucher.id.desc())
        return (await self._session.scalars(statement)).all()

    async def list_expired_active(
        self, user_id: UUID, now: datetime, *, for_update: bool = False
    ) -> Sequence[Voucher]:
        """A user's vouchers that are still ACTIVE although they expired: refunds owed."""
        statement = (
            select(Voucher)
            .where(
                Voucher.user_id == user_id,
                Voucher.status == VoucherStatus.ACTIVE,
                Voucher.expires_at <= now,
            )
            .order_by(Voucher.id)
        )
        return (await self._session.scalars(self._locked(statement, for_update))).all()

    async def product_names(self, voucher_ids: Collection[UUID]) -> dict[UUID, str]:
        """What each of these vouchers is for, by voucher id, in one query.

        For the ledger, whose lines about a voucher name its product. An id that is no
        voucher's is simply missing from the result.
        """
        statement = select(Voucher.id, Voucher.product_name).where(Voucher.id.in_(voucher_ids))
        rows = await self._session.execute(statement)
        return dict(rows.all())

    async def pending_value(self) -> int:
        """Value of all ACTIVE vouchers: what VIS owes on them (SPEC §5)."""
        statement = select(sum_micro(Voucher.value_micro)).where(
            Voucher.status == VoucherStatus.ACTIVE
        )
        return (await self._session.execute(statement)).scalar_one()
