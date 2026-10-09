"""Plumbing shared by every repository (backend/CLAUDE.md, "Layers" and "Transactions").

A repository is the queries of one aggregate on the session of the surrounding
`UnitOfWork`. It never commits and never raises `AppError`; a service decides what a
missing row or a constraint violation means.

Row locks. `for_update=True` takes Postgres' FOR NO KEY UPDATE lock, the one an UPDATE
of non-key columns takes. It serialises writers of a row exactly like FOR UPDATE, but
does not conflict with the FOR KEY SHARE lock every foreign-key check takes on the row
it references. With plain FOR UPDATE, inserting a trade (which references its user)
would wait on a sign-in holding that user's row while the sign-in waits on the trade's
account lock: a deadlock. No row locked this way is ever deleted or re-keyed, so the
weaker lock loses nothing.

Bulk updates. An UPDATE with a condition beyond the primary key runs with
`MATCHED_BY_DATABASE`: the rows Postgres reports as changed are the ones whose loaded
copies change too. The ORM's default re-evaluates the condition in Python on those
copies, which gives a different answer whenever a copy predates another transaction.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from sqlalchemy import BigInteger, ColumnElement, Select, cast, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.base import Base

MATCHED_BY_DATABASE: Final[Mapping[str, str]] = {"synchronize_session": "fetch"}
"""Execution options of a conditional bulk UPDATE; see the module docstring."""


def sum_micro(column: InstrumentedAttribute[int] | ColumnElement[int]) -> ColumnElement[int]:
    """SUM of a BIGINT column as an integer, 0 over no rows.

    Postgres widens SUM(bigint) to NUMERIC, which the driver returns as a Decimal. Cast
    back so amounts stay integers end to end (golden rule 1).
    """
    return cast(func.coalesce(func.sum(column), 0), BigInteger)


class BaseRepository[ModelT: Base]:
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def add(self, instance: ModelT) -> ModelT:
        """INSERT now, so the row has its primary key and defaults when this returns.

        Flushing on every add also fixes the write order: the models have no
        relationships the ORM could sort inserts by, so rows go out as services add them.
        """
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def add_all(self, instances: Iterable[ModelT]) -> None:
        self._session.add_all(instances)
        await self._session.flush()

    async def _get(self, ident: object, *, for_update: bool) -> ModelT | None:
        """Load by primary key. Locking re-reads the row even if this session holds it."""
        return await self._session.get(
            self._model,
            ident,
            with_for_update={"key_share": True} if for_update else None,
            populate_existing=for_update,
        )

    @staticmethod
    def _locked[T](statement: Select[T], for_update: bool) -> Select[T]:
        """Add the row lock on request.

        A locking read returns the latest committed row, so it must also overwrite a copy
        this session loaded before the lock (`populate_existing`). Otherwise a service
        would compute on values another transaction has since changed.
        """
        if not for_update:
            return statement
        return statement.with_for_update(key_share=True).execution_options(populate_existing=True)
