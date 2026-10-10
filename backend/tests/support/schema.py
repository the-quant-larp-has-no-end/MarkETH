"""What the schema would be if a model were edited, without editing one.

A test reaches for a CHECK constraint of the models and gives it another expression for
as long as the test runs, with pytest's `monkeypatch`, which puts the original back:

    check = check_constraint("postings", "ck_postings_amount_non_zero")
    monkeypatch.setattr(check, "sqltext", literal_column("amount_micro > 0"))

Only the DDL SQLAlchemy compiles from the models changes; the database, the model
classes and every query stay as they are.
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint, Column, ColumnElement, MetaData, Table

from app.db.base import Base, enum_column


def check_constraint(table: str, name: str) -> CheckConstraint:
    """The CHECK constraint `name` that the models declare on `table`."""
    return _check(Base.metadata.tables[table], name)


def enum_check_expression(table: str, column: str, members: type[StrEnum]) -> ColumnElement[bool]:
    """The expression of `ck_<table>_<column>` if the column were an enum of `members`.

    Built the way a model builds it, with `enum_column`, on a table of its own.
    """
    declared = Table(
        table,
        MetaData(naming_convention=Base.metadata.naming_convention),
        Column(column, enum_column(members, column)),
    )
    expression: ColumnElement[bool] = _check(declared, f"ck_{table}_{column}").sqltext
    return expression


def with_one_more_member(members: type[StrEnum], name: str) -> type[StrEnum]:
    """An enum with the members of `members` and one more, whose name and value is `name`."""
    names_and_values = [*((member.name, member.value) for member in members), (name, name)]
    # mypy reads the functional form of an enum only when its name is a literal.
    extended: type[StrEnum] = StrEnum(members.__name__, names_and_values)  # type: ignore[misc]
    return extended


def _check(table: Table, name: str) -> CheckConstraint:
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == name:
            return constraint
    raise LookupError(f"table {table.name} has no CHECK constraint {name}")
