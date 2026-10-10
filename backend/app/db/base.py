"""The declarative base every ORM model inherits from (SPEC §7, Appendix A.7).

It fixes three things for the whole schema, so no model can get them wrong:

* Constraint and index names follow one convention. Alembic can then address every
  constraint by name, and an integrity error names the rule that was broken.
* A Python `int` is always BIGINT (amounts are micro-units, golden rule 1) and a
  `datetime` is always TIMESTAMP WITH TIME ZONE that refuses a value without a timezone
  (`AwareDateTime`).
* Enum-like columns are VARCHAR with a named CHECK constraint, built by `enum_column`.
  Where an enum has a member no row may hold, the column is built from the members a
  row keeps (`StoredMembers`), and the other one cannot reach a statement.

`sha256_hex_check` backs up SPEC §2: a code is looked up by its hash, and the database
refuses anything in that column that is not shaped like one.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Dialect, Enum, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator, TypeEngine

NAMING_CONVENTION: Final[dict[str, str]] = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

ENUM_LENGTH: Final[int] = 32
"""Width of every enum column. Fixed, so adding a longer member never alters a column."""

SHA256_HEX_LENGTH: Final[int] = 64


class AwareDateTime(TypeDecorator[datetime]):
    """TIMESTAMP WITH TIME ZONE that only takes an instant, never a wall-clock reading.

    The driver would read a datetime without a timezone as local time of the process,
    so the same code would store one instant in a container (UTC) and another on a
    developer's machine. Such a value is refused here, for every column and for every
    comparison against one; it surfaces as `sqlalchemy.exc.StatementError`.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError(f"a timestamp needs a timezone, got the naive {value!r}")
        return value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012  (SQLAlchemy reads this class attribute)
        int: BigInteger,
        datetime: AwareDateTime(),
        # none_as_null: Python None is SQL NULL, never the JSON literal `null`.
        dict[str, Any]: JSONB(none_as_null=True),
    }


class StoredMembers[MemberT: StrEnum](TypeDecorator[MemberT]):
    """Column type of an enum of which a row keeps only some members.

    The column is a VARCHAR whose CHECK lists `members` and nothing else, and a row
    read from it carries a member of `enum_class`. Any other member is refused when it
    is bound to a statement, as a value to write and as a value to compare with alike:
    `sqlalchemy.exc.StatementError`, before the database is asked. The enum can then
    name a value the application only ever works out, and no statement can mistake it
    for one a row may hold.
    """

    impl = Enum
    cache_ok = True

    def __init__(self, enum_class: type[MemberT], members: tuple[MemberT, ...], name: str) -> None:
        self.enum_class = enum_class
        self.members = members
        super().__init__(
            *(member.value for member in members),
            name=name,
            native_enum=False,
            create_constraint=True,
            length=ENUM_LENGTH,
            validate_strings=True,
        )

    def process_bind_param(self, value: MemberT | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value not in self.members:
            raise ValueError(f"{value!r} is not a value a row of this column keeps")
        return self.enum_class(value).value

    def process_result_value(self, value: str | None, dialect: Dialect) -> MemberT | None:
        return None if value is None else self.enum_class(value)


def enum_column[MemberT: StrEnum](
    enum_class: type[MemberT], name: str, *, members: tuple[MemberT, ...] | None = None
) -> TypeEngine[Any]:
    """Column type of an enum: its values as VARCHAR plus the CHECK `ck_<table>_<name>`.

    `members` names the ones a row keeps where that is not all of them, in the order
    the CHECK lists them (`StoredMembers`).
    """
    if members is not None:
        return StoredMembers(enum_class, members, name)
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_LENGTH,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


def sha256_hex_check(column: str) -> CheckConstraint:
    """CHECK `ck_<table>_<column>_sha256_hex`: the column holds a SHA-256 hex digest."""
    return CheckConstraint(
        f"{column} ~ '^[0-9a-f]{{{SHA256_HEX_LENGTH}}}$'", name=f"{column}_sha256_hex"
    )
