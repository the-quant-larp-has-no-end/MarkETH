"""The schema fingerprint changes with everything a database is made of, and the revisions
lead to the one of the models (SPEC §7, A.10).

A database is changed by the revisions of `migrations/versions/` alone, so a change to a
model has to come with one. `alembic check` does not see every such change: it does not
compare CHECK constraints, and an enum column is a VARCHAR with one. The fingerprint is
what does: the last revision carries the one of the models, or these tests fail.

A model is "edited" here by giving one of its CHECK constraints another expression for
the length of a test (`tests/support/schema.py`); nothing is written to a model.
"""

import ast
import inspect
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
from sqlalchemy import Column, Index, MetaData, Text, literal_column, make_url
from sqlalchemy.schema import CreateTable

from app.core.enums import JournalKind
from app.db.base import Base
from app.models.fingerprint import (
    DIALECT,
    HAND_WRITTEN_STATEMENTS,
    fingerprint_of,
    schema_fingerprint,
)
from app.models.privileges import APP_ROLE_PRIVILEGES, TABLE_PRIVILEGES
from app.models.triggers import CREATE_AUDIENCE_FUNCTION, CREATE_AUDIENCE_TRIGGER
from tests.support.schema import check_constraint, enum_check_expression, with_one_more_member

BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

FIRST_DEPLOYED_FINGERPRINT: Final[str] = (
    "c2e6fb70beaae04aa33addb36ed64ab0197aad751823402851f14312ea2ea7a0"
)
"""What the database behind the portal carried on 10 Oct 2026, built by revision 0001."""

PRINT_FINGERPRINT: Final[str] = (
    "from app.models.fingerprint import schema_fingerprint; print(schema_fingerprint())"
)


def revisions() -> list[Script]:
    """The revisions of `migrations/versions/`, the baseline first."""
    scripts = ScriptDirectory.from_config(Config(BACKEND_ROOT / "alembic.ini"))
    return list(reversed(list(scripts.walk_revisions())))


def statements_of(function: Callable[[], None]) -> list[str]:
    """The statements of a revision's `upgrade` or `downgrade`, each as its source."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    assert isinstance(definition, ast.FunctionDef)
    return [ast.unparse(statement) for statement in definition.body]


def test_the_revisions_are_one_line_numbered_from_the_baseline() -> None:
    """A database takes them in one order, which their numbers and file names show."""
    line = revisions()

    assert [revision.revision for revision in line] == [
        f"{number:04d}" for number in range(1, len(line) + 1)
    ]
    assert [revision.down_revision for revision in line] == [
        None,
        *(revision.revision for revision in line[:-1]),
    ]
    for revision in line:
        assert Path(revision.path).name.startswith(f"{revision.revision}_")


def test_the_last_revision_leads_to_the_models_of_this_code() -> None:
    """Where this fails, a model, a trigger or a privilege changed and no revision came
    with it: `make revision NAME=...` writes the file, with the fingerprint of the
    models as they are now. Without it `scripts.check_schema` refuses every database
    there is, the deployed one first."""
    assert schema_fingerprint() == revisions()[-1].module.FINGERPRINT


def test_every_revision_leaves_its_fingerprint_and_takes_the_one_before_back() -> None:
    """`upgrade` ends by storing the fingerprint of the revision, `downgrade` by storing
    the one of the revision before, so the comment of `alembic_version` always names the
    schema the database has."""
    baseline, *later = revisions()
    assert statements_of(baseline.module.upgrade)[-1] == "store_fingerprint(FINGERPRINT)"

    before = baseline
    for revision in later:
        assert re.fullmatch(r"[0-9a-f]{64}", revision.module.FINGERPRINT), revision.revision
        assert revision.module.PREVIOUS_FINGERPRINT == before.module.FINGERPRINT
        assert statements_of(revision.module.upgrade)[-1] == "store_fingerprint(FINGERPRINT)"
        assert (
            statements_of(revision.module.downgrade)[-1]
            == "store_fingerprint(PREVIOUS_FINGERPRINT)"
        )
        before = revision


def test_the_baseline_is_the_schema_as_it_was_first_deployed() -> None:
    """The baseline is a record: the statements of the first deployed database, as text.
    Its fingerprint is the one that database carries, and the statements still hash to
    it, so no later edit to a model, and no edit to the file, has reached them.

    SQLAlchemy ends a line of a CREATE TABLE with a blank after the comma; the file is
    kept without those blanks, and they are put back for the hash."""
    baseline = revisions()[0].module
    tables = [
        statement.replace(",\n", ", \n")
        for name in sorted(baseline.TABLES)
        for statement in baseline.TABLES[name]
    ]
    rest = [
        *baseline.TRIGGERS,
        baseline.CREATE_APP_ROLE,
        baseline.GRANT_CONNECT,
        *baseline.APP_ROLE_PRIVILEGES,
    ]

    assert baseline.FINGERPRINT == FIRST_DEPLOYED_FINGERPRINT
    assert fingerprint_of([*tables, *rest]) == FIRST_DEPLOYED_FINGERPRINT


def test_a_new_revision_is_written_with_the_fingerprints_and_the_next_number(
    tmp_path: Path,
) -> None:
    """`make revision NAME=...`: the file follows the last revision and carries the
    fingerprint of the models as they are, and the one of the revision before for the
    way back. Unchanged, it is a revision that alters nothing and says so."""
    shutil.copytree(
        BACKEND_ROOT / "migrations", tmp_path / "migrations", ignore=lambda *_: ["__pycache__"]
    )
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(tmp_path / "migrations"))
    last = revisions()[-1]
    number = f"{int(last.revision) + 1:04d}"

    written = command.revision(config, message="add a nickname", rev_id=number)

    assert isinstance(written, Script)
    assert Path(written.path).name == f"{number}_add_a_nickname.py"
    assert written.down_revision == last.revision
    assert schema_fingerprint() == written.module.FINGERPRINT
    assert written.module.PREVIOUS_FINGERPRINT == last.module.FINGERPRINT
    assert statements_of(written.module.upgrade) == ["store_fingerprint(FINGERPRINT)"]
    assert statements_of(written.module.downgrade) == ["store_fingerprint(PREVIOUS_FINGERPRINT)"]
    makefile = (BACKEND_ROOT.parent / "Makefile").read_text(encoding="utf-8")
    assert "uv run alembic revision --rev-id" in makefile


def test_the_fingerprint_is_a_sha256_hex_digest() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", schema_fingerprint())


@pytest.mark.parametrize("hash_seed", ["0", "1", "4242"])
def test_another_process_computes_the_same_fingerprint(hash_seed: str) -> None:
    """A revision is written with it in one process and every later start computes it in
    another, where sets are walked in another order."""
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


# --- Audiences, streaks, questions, broadcasts and chat messages ----------------------

RULES_OF_FIVE_TABLES: Final[list[tuple[str, str]]] = [
    ("markets", "ck_markets_audience_programme_not_blank"),
    ("journal_entries", "ck_journal_entries_kind"),
    ("streaks", "ck_streaks_current_days_positive"),
    ("streaks", "ck_streaks_longest_not_below_current"),
    ("questions", "ck_questions_text_length"),
    ("questions", "ck_questions_answer_length"),
    ("questions", "ck_questions_answered_whole"),
    ("questions", "ck_questions_removed_whole"),
    ("broadcasts", "ck_broadcasts_status"),
    ("broadcasts", "ck_broadcasts_end_reason"),
    ("broadcasts", "ck_broadcasts_decided_together"),
    ("broadcasts", "ck_broadcasts_requested_is_undecided"),
    ("broadcasts", "ck_broadcasts_refusal_has_reason"),
    ("broadcasts", "ck_broadcasts_live_has_started"),
    ("broadcasts", "ck_broadcasts_ended_is_complete"),
    ("broadcasts", "ck_broadcasts_resumed_has_started"),
    ("chat_messages", "ck_chat_messages_text_length"),
]

INDEXES_OF_FIVE_TABLES: Final[list[str]] = [
    "ix_markets_audience_programme_status_close_at",
    "ix_trades_user_id_created_at",
    "ix_questions_market_id_id",
    "uq_broadcasts_market_open",
    "ix_broadcasts_open",
    "ix_broadcasts_market_id_requested_at",
    "ix_broadcasts_broadcaster_id_requested_at",
    "ix_chat_messages_broadcast_id_id",
]

PRIVILEGES_TAKEN_BACK: Final[tuple[str, ...]] = (
    "REVOKE DELETE ON questions FROM %I",
    "REVOKE UPDATE ON chat_messages FROM %I",
    "REVOKE DELETE ON broadcasts FROM %I",
)


@pytest.mark.parametrize(("table", "name"), RULES_OF_FIVE_TABLES)
def test_each_rule_of_an_audience_a_streak_a_question_a_broadcast_and_a_chat_message_counts(
    monkeypatch: pytest.MonkeyPatch, table: str, name: str
) -> None:
    unchanged = schema_fingerprint()

    monkeypatch.setattr(check_constraint(table, name), "sqltext", literal_column("true"))

    assert schema_fingerprint() != unchanged


@pytest.mark.parametrize("name", INDEXES_OF_FIVE_TABLES)
def test_each_index_of_those_tables_counts(name: str) -> None:
    models = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(models)
    unchanged = schema_fingerprint(models)

    dropped = [
        (table, index)
        for table in models.tables.values()
        for index in table.indexes
        if index.name == name
    ]
    assert len(dropped) == 1, name
    table, index = dropped[0]
    table.indexes.remove(index)

    assert schema_fingerprint(models) != unchanged


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("markets", "audience_programme"),
        ("users", "degree_changed_at"),
        ("broadcasts", "resumed_from"),
        ("chat_messages", "broadcast_id"),
        ("chat_messages", "from_vis"),
        ("streaks", "last_day"),
        ("questions", "removed_at"),
    ],
)
def test_the_tables_are_fingerprinted_with_the_columns_the_models_give_them(
    table: str, column: str
) -> None:
    """The fingerprint is taken over the CREATE TABLE statements of the models, and a
    column of a model is a line of one."""
    dialect = make_url(DIALECT).get_dialect()()
    ddl = str(CreateTable(Base.metadata.tables[table]).compile(dialect=dialect))

    assert f"\n\t{column} " in ddl


def test_the_trigger_that_fixes_an_audience_and_the_three_revokes_are_hand_written_statements() -> (
    None
):
    statements = list(HAND_WRITTEN_STATEMENTS)

    assert statements.index(CREATE_AUDIENCE_FUNCTION) + 1 == statements.index(
        CREATE_AUDIENCE_TRIGGER
    )
    # What is granted on every table comes first, then what is taken back table by table.
    granted = APP_ROLE_PRIVILEGES.index(
        f"GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA public TO %I"
    )
    for statement in PRIVILEGES_TAKEN_BACK:
        assert statement in statements
        assert APP_ROLE_PRIVILEGES.index(statement) > granted
