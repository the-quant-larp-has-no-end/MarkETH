"""The schema fingerprint changes with everything a database is built from (SPEC §7, A.10).

The schema has one revision and no upgrade path, so a database must be wiped when a
model changes. `alembic check` does not see every such change: it does not compare CHECK
constraints, and an enum column is a VARCHAR with one. The fingerprint is what does.

A model is "edited" here by giving one of its CHECK constraints another expression for
the length of a test (`tests/support/schema.py`); nothing is written to a model.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Column, Index, MetaData, Text, literal_column

from app.core.enums import JournalKind
from app.db.base import Base
from app.models.fingerprint import HAND_WRITTEN_STATEMENTS, schema_fingerprint
from tests.support.schema import check_constraint, enum_check_expression, with_one_more_member

BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

PRINT_FINGERPRINT: Final[str] = (
    "from app.models.fingerprint import schema_fingerprint; print(schema_fingerprint())"
)


def test_the_schema_has_exactly_one_revision() -> None:
    """What the fingerprint rests on: a changed model is answered by a rebuilt database,
    never by a second revision that alters an existing one."""
    revisions = ScriptDirectory.from_config(Config(BACKEND_ROOT / "alembic.ini")).walk_revisions()

    assert [(revision.revision, revision.down_revision) for revision in revisions] == [
        ("0001", None)
    ]


def test_the_fingerprint_is_a_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", schema_fingerprint())


@pytest.mark.parametrize("hash_seed", ["0", "1", "4242"])
def test_another_process_computes_the_same_fingerprint(hash_seed: str) -> None:
    """The baseline revision stores it in one process and every later start computes it
    in another, where sets are walked in another order."""
    printed = subprocess.run(
        [sys.executable, "-c", PRINT_FINGERPRINT],
        cwd=BACKEND_ROOT,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=True,
    )

    assert printed.stdout.strip() == schema_fingerprint()


def test_a_changed_check_constraint_changes_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unchanged = schema_fingerprint()
    check = check_constraint("postings", "ck_postings_amount_non_zero")

    monkeypatch.setattr(check, "sqltext", literal_column("amount_micro > 0"))

    assert schema_fingerprint() != unchanged


def test_an_added_enum_member_changes_the_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    unchanged = schema_fingerprint()
    check = check_constraint("journal_entries", "ck_journal_entries_kind")

    # The expression built for the members the enum has is the one the model carries.
    same_kinds = enum_check_expression("journal_entries", "kind", JournalKind)
    monkeypatch.setattr(check, "sqltext", same_kinds)
    assert schema_fingerprint() == unchanged

    more_kinds = enum_check_expression(
        "journal_entries", "kind", with_one_more_member(JournalKind, "TIP")
    )
    monkeypatch.setattr(check, "sqltext", more_kinds)
    assert schema_fingerprint() != unchanged


def test_an_added_column_and_an_added_index_change_the_fingerprint() -> None:
    models = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(models)
    users = models.tables["users"]

    # A copy lists a table's constraints in an order of its own, so it is compared with
    # itself, not with the models.
    unchanged = schema_fingerprint(models)
    users.append_column(Column("nickname", Text))
    with_column = schema_fingerprint(models)
    Index("ix_users_nickname", users.c.nickname)
    with_index = schema_fingerprint(models)

    assert len({unchanged, with_column, with_index}) == 3
    assert "nickname" not in Base.metadata.tables["users"].c


@pytest.mark.parametrize("position", range(len(HAND_WRITTEN_STATEMENTS)))
def test_every_hand_written_statement_counts(position: int) -> None:
    """The triggers and the app role's privileges are part of what a database is."""
    edited = list(HAND_WRITTEN_STATEMENTS)
    edited[position] += " -- edited"

    assert schema_fingerprint(statements=edited) != schema_fingerprint()


def test_the_order_of_the_hand_written_statements_counts() -> None:
    """A REVOKE that runs before the GRANT it narrows takes nothing away."""
    reordered = list(reversed(HAND_WRITTEN_STATEMENTS))

    assert schema_fingerprint(statements=reordered) != schema_fingerprint()


def test_two_statements_cannot_pass_for_two_others_that_read_the_same_end_to_end() -> None:
    no_tables = MetaData()

    assert schema_fingerprint(no_tables, ["ab", "c"]) != schema_fingerprint(no_tables, ["a", "bc"])
