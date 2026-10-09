"""The declarative base every ORM model inherits from (SPEC §7, Appendix A.7).

It fixes three things for the whole schema, so no model can get them wrong:

* Constraint and index names follow one convention. Alembic can then address every
  constraint by name, and an integrity error names the rule that was broken.
* A Python `int` is always BIGINT (amounts are micro-units, golden rule 1) and a
  `datetime` is always TIMESTAMP WITH TIME ZONE.
* Enum-like columns are VARCHAR with a named CHECK constraint, built by `enum_column`.

`sha256_hex_check` backs up SPEC §2 and §3: tokens, session IDs and codes are stored as
hashes, and the database refuses anything that is not shaped like one.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Enum, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012  (SQLAlchemy reads this class attribute)
        int: BigInteger,
        datetime: DateTime(timezone=True),
        # none_as_null: Python None is SQL NULL, never the JSON literal `null`.
        dict[str, Any]: JSONB(none_as_null=True),
    }


def enum_column(enum_class: type[StrEnum], name: str) -> Enum:
    """Column type of an enum: its values as VARCHAR plus the CHECK `ck_<table>_<name>`."""
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
