"""Business logic: one class per area, each owning its transactions (backend/CLAUDE.md).

How every service here is built:

* Dependencies arrive through the constructor, wired once in `app/container.py`.
* A method that changes state opens exactly one `UnitOfWork` per business operation
  and locks the market row first, then the user's account, then anything else.
* Credits move only through `LedgerService.post`; time comes only from the `Clock`.
* The acting user is an `AuthenticatedUser`. Results are schema objects (`app.schemas`)
  or ORM rows. Failures are `AppError` subclasses; nothing here knows HTTP.
* A method that takes a `uow` runs inside its caller's transaction and never commits.
* What happens after the commit (cache deletes, live events, mail) is best effort and
  must not fail the request.
"""

from collections.abc import Callable

from app.repositories.unit_of_work import UnitOfWork

type UowFactory = Callable[[], UnitOfWork]
"""Returns a new `UnitOfWork` per call: one session, one transaction."""
