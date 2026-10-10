"""The SQL the application sends to Postgres while a block of a test runs.

Which rows a request locks is part of what it promises (SPEC §8, backend/CLAUDE.md,
"Transactions"): a trade locks the market row and then the trader's account, a quote
locks nothing, a redemption locks its code and never its batch. A lock shows in the
statement that takes it, so a test can read it there:

    with recorded_statements(container) as statements:
        await container.quotes.quote(viewer, market.id, query)
    assert not any(takes_a_row_lock(statement) for statement in statements)

Only what goes through the engine of `container` is recorded, in the order it is sent,
each statement on one line.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import event

from app.container import Container

ROW_LOCKS: Final[tuple[str, ...]] = (
    "FOR UPDATE",
    "FOR NO KEY UPDATE",
    "FOR SHARE",
    "FOR KEY SHARE",
)
"""The four locking clauses of a SELECT in Postgres, strongest first."""


@contextmanager
def recorded_statements(container: Container) -> Iterator[list[str]]:
    """The statements sent inside the block. The list fills while the block runs."""
    statements: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.split()))

    engine = container.database.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def takes_a_row_lock(statement: str) -> bool:
    """Whether `statement` is a SELECT that locks the rows it reads."""
    return any(clause in statement for clause in ROW_LOCKS)


def writes(statement: str) -> bool:
    """Whether `statement` changes a row: an INSERT, an UPDATE or a DELETE."""
    return statement.startswith(("INSERT", "UPDATE", "DELETE"))
