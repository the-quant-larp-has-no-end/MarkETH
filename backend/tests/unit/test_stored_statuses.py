"""A market is reported in eight statuses and stored in seven (SPEC §6, Appendix A.4).

`MarketStatus.EXPIRED` is worked out from the clock (`MarketView.effective_status`) and
never kept. These tests hold the two apart where they could run into each other: the
column of `markets.status` is built from `STORED_MARKET_STATUSES`, so EXPIRED is in no
CHECK, and the column's type refuses it when a statement is bound, as a value to write
and as a value to compare with.

Nothing here needs a database. What Postgres itself answers is in
`tests/integration/test_schema.py`.
"""

from enum import StrEnum
from typing import Any, Final

import pytest
from sqlalchemy import Column, Dialect, MetaData, Table, insert, make_url, select, update
from sqlalchemy.exc import StatementError
from sqlalchemy.schema import CreateTable

from app.core.enums import STORED_MARKET_STATUSES, MarketStatus
from app.db.base import Base, StoredMembers, enum_column
from app.models import Market
from app.models.fingerprint import DIALECT, schema_fingerprint
from tests.support.schema import check_constraint

POSTGRES: Final[Dialect] = make_url(DIALECT).get_dialect()()
"""The dialect every connection of this code uses, and the fingerprint is compiled for."""

MARKETS: Final[Table] = Base.metadata.tables["markets"]
STATUS: Final[Any] = MARKETS.c.status


def ddl_of(table: Table) -> str:
    return str(CreateTable(table).compile(dialect=POSTGRES))


def bound(statement: Any) -> list[Any]:
    """What the statement would send to Postgres: every value of every parameter,
    through the processor of the parameter's type, as the driver gets it. The members
    of an IN list are bound one by one, as SQLAlchemy does when it expands the list."""
    compiled = statement.compile(dialect=POSTGRES)
    sent: list[Any] = []
    for parameter in compiled.bind_names:
        process = parameter.type.bind_processor(POSTGRES)
        values = parameter.value if parameter.expanding else [parameter.value]
        sent.extend(value if process is None else process(value) for value in values)
    return sent


def test_a_row_keeps_every_status_but_expired() -> None:
    """In the order of the enum, which is the order the CHECK lists them in."""
    assert list(STORED_MARKET_STATUSES) == [
        status for status in MarketStatus if status is not MarketStatus.EXPIRED
    ]
    assert set(MarketStatus) - set(STORED_MARKET_STATUSES) == {MarketStatus.EXPIRED}
    assert len(set(STORED_MARKET_STATUSES)) == len(STORED_MARKET_STATUSES) == 7


def test_the_check_of_the_status_column_lists_the_seven_stored_statuses_and_nothing_else() -> None:
    assert check_constraint("markets", "ck_markets_status") is not None
    assert (
        "CONSTRAINT ck_markets_status CHECK (status IN "
        "('PROPOSED', 'REJECTED', 'OPEN', 'HALTED', 'CLOSED', 'RESOLVED', 'VOID'))"
    ) in ddl_of(MARKETS)
    assert "EXPIRED" not in ddl_of(MARKETS)
    assert "status VARCHAR(32) NOT NULL" in ddl_of(MARKETS)


def test_the_status_column_is_built_from_the_stored_statuses() -> None:
    assert isinstance(STATUS.type, StoredMembers)
    assert STATUS.type.members == STORED_MARKET_STATUSES
    assert STATUS.type.enum_class is MarketStatus


def test_a_reported_status_changes_no_table_and_a_stored_one_would() -> None:
    """What keeps EXPIRED out of the database is the tuple, not the enum: a column of
    the same enum that keeps all of its members would list EXPIRED in its CHECK, and
    the fingerprint of such a schema is another one."""

    def markets_with(status_type: Any) -> MetaData:
        models = MetaData(naming_convention=Base.metadata.naming_convention)
        Table("markets", models, Column("status", status_type, nullable=False))
        return models

    as_built = markets_with(enum_column(MarketStatus, "status", members=STORED_MARKET_STATUSES))
    all_stored = markets_with(enum_column(MarketStatus, "status"))

    assert "'EXPIRED'" not in ddl_of(as_built.tables["markets"])
    assert "'VOID', 'EXPIRED')" in ddl_of(all_stored.tables["markets"])
    assert schema_fingerprint(as_built) != schema_fingerprint(all_stored)


@pytest.mark.parametrize("status", STORED_MARKET_STATUSES, ids=str)
def test_a_stored_status_is_bound_as_its_value_and_read_back_as_the_member(
    status: MarketStatus,
) -> None:
    (written,) = bound(update(Market).values(status=status))
    read = STATUS.type.result_processor(POSTGRES, None)

    assert written == status.value
    assert type(written) is str
    assert read is not None
    assert read(status.value) is status


def test_expired_cannot_be_written() -> None:
    """Neither into a row that is inserted nor into one that is updated: the statement
    fails when it is bound, before anything is sent."""
    for statement in (
        insert(Market).values(status=MarketStatus.EXPIRED),
        update(Market).values(status=MarketStatus.EXPIRED),
        update(Market).values(status="EXPIRED"),
    ):
        with pytest.raises((StatementError, ValueError), match="EXPIRED"):
            bound(statement)


def test_expired_cannot_be_asked_for() -> None:
    """A WHERE that compares the column with EXPIRED would find nothing and say
    nothing. It fails like a write: a list of expired proposals is read as PROPOSED and
    judged by the clock (`MarketView.stored_statuses`)."""
    for statement in (
        select(Market.id).where(Market.status == MarketStatus.EXPIRED),
        select(Market.id).where(Market.status.in_([MarketStatus.OPEN, MarketStatus.EXPIRED])),
        select(Market.id).where(Market.status.not_in([MarketStatus.EXPIRED])),
    ):
        with pytest.raises((StatementError, ValueError), match="EXPIRED"):
            bound(statement)

    kept = bound(select(Market.id).where(Market.status.in_(STORED_MARKET_STATUSES)))
    assert kept == [status.value for status in STORED_MARKET_STATUSES]


def test_a_value_that_is_no_status_at_all_is_refused_like_expired() -> None:
    with pytest.raises((StatementError, ValueError), match="BANNED"):
        bound(update(Market).values(status="BANNED"))


def test_a_column_of_some_members_refuses_the_others_and_none_of_its_own() -> None:
    """`StoredMembers` for any enum: the rule is the type's, not the market's."""

    class Light(StrEnum):
        RED = "RED"
        GREEN = "GREEN"
        BROKEN = "BROKEN"

    kept = (Light.RED, Light.GREEN)
    lights = Table(
        "lights",
        MetaData(naming_convention=Base.metadata.naming_convention),
        Column("colour", enum_column(Light, "colour", members=kept)),
    )

    assert "CONSTRAINT ck_lights_colour CHECK (colour IN ('RED', 'GREEN'))" in ddl_of(lights)
    assert bound(insert(lights).values(colour=Light.GREEN)) == ["GREEN"]
    assert bound(insert(lights).values(colour=None)) == [None]
    with pytest.raises((StatementError, ValueError), match="BROKEN"):
        bound(insert(lights).values(colour=Light.BROKEN))
