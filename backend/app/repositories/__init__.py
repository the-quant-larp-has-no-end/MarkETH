"""Data access: one repository class per aggregate and the `UnitOfWork` that binds them.

Repositories are queries only: none of them commits or raises `AppError`. The
`UnitOfWork` commits once, when the block of the service that opened it ends; services
own the transaction and the business meaning (backend/CLAUDE.md, "Layers").
"""

from app.repositories.unit_of_work import UnitOfWork

__all__ = ["UnitOfWork"]
