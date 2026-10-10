"""A unit-of-work factory a test can count, and one that runs dry (backend/CLAUDE.md,
"Transactions").

A write is one unit of work: its change and its answer are one transaction, and
nothing is read once that has committed. Otherwise a request whose change stands could
still be answered 503, because the read after the commit found no connection, and the
client would send a change again that was made the first time.

    units = CountedUnitsOfWork(container.uow_factory)
    service = MarketService(units, container.settings, container.clock, ...)
    await service.propose(alice, request)
    assert units.opened == 1

    units = CountedUnitsOfWork(container.uow_factory, at_most=1)

hands out one unit of work and then stands for a pool with no connection left: the
next call raises what such a pool raises after `POSTGRES_POOL_TIMEOUT_SECONDS`, which
the API answers with 503 (`app.db.database.is_unavailable`).

Give the same factory to every collaborator that takes one, so that a unit of work
opened by any of them is counted.
"""

from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory


class CountedUnitsOfWork:
    """Stands where a `UowFactory` stands. `opened` is how many units of work it handed
    out, and `refused` how many it was asked for beyond `at_most`."""

    def __init__(self, factory: UowFactory, *, at_most: int | None = None) -> None:
        self._factory = factory
        self._at_most = at_most
        self.opened = 0
        self.refused = 0

    def __call__(self) -> UnitOfWork:
        if self._at_most is not None and self.opened >= self._at_most:
            self.refused += 1
            raise PoolTimeoutError("the stand-in for the pool has no connection left")
        self.opened += 1
        return self._factory()
