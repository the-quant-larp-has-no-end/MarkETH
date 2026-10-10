"""How a text people write for other people is cleaned, and how long one may be stored
(`app.core.text`)."""

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.text import (
    ANSWER_STORED_MAX_LENGTH,
    CHAT_BACKLOG_MAX,
    CHAT_MESSAGE_STORED_MAX_LENGTH,
    QUESTION_STORED_MAX_LENGTH,
    clean_line,
    clean_text,
)
from app.models import ChatMessage, Question
from tests.support.schema import check_constraint


def test_the_stored_lengths_are_the_ones_the_tables_check() -> None:
    assert (QUESTION_STORED_MAX_LENGTH, ANSWER_STORED_MAX_LENGTH) == (500, 2000)
    assert (CHAT_MESSAGE_STORED_MAX_LENGTH, CHAT_BACKLOG_MAX) == (500, 200)

    question = str(check_constraint(Question.__tablename__, "ck_questions_text_length").sqltext)
    answer = str(check_constraint(Question.__tablename__, "ck_questions_answer_length").sqltext)
    message = str(
        check_constraint(ChatMessage.__tablename__, "ck_chat_messages_text_length").sqltext
    )
    assert question == "char_length(text) BETWEEN 1 AND 500"
    assert answer == "answer_text IS NULL OR char_length(answer_text) BETWEEN 1 AND 2000"
    assert message == "char_length(text) BETWEEN 1 AND 500"


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("Which menu counts?", "Which menu counts?"),
        ("  Which   menu\tcounts? ", "Which menu counts?"),
        ("one\nline\r\nafter\u2028another", "one line after another"),
        ("no\x00 NUL, no \x1b escape", "no NUL, no escape"),
        ("zero\u200bwidth and \u202eoverride", "zerowidth and override"),
        ("a\u00a0b\u3000c", "a b c"),
        ("Älplermagronen für 12 CHF? 🧀", "Älplermagronen für 12 CHF? 🧀"),
        ("\u200b\x00\t\n", ""),
        ("", ""),
    ],
    ids=[
        "as-it-is",
        "runs-of-whitespace",
        "line-breaks",
        "control-characters",
        "format-characters",
        "other-spaces",
        "letters-of-any-script",
        "nothing-a-line-keeps",
        "empty",
    ],
)
def test_clean_line_makes_one_line(raw: str, cleaned: str) -> None:
    assert clean_line(raw) == cleaned


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("one line", "one line"),
        ("first\nsecond", "first\nsecond"),
        ("first\r\nsecond\rthird", "first\nsecond\nthird"),
        ("  first  \n\tsecond\t", "first\nsecond"),
        ("\n\n first \n\n", "first"),
        ("first\n\n\n\nsecond\n \n\u200b\nthird", "first\n\nsecond\n\nthird"),
        ("first\n\nsecond", "first\n\nsecond"),
        ("a\x00b\nc\u202ed", "ab\ncd"),
        ("\n \n\t\n", ""),
        ("", ""),
    ],
    ids=[
        "one-line",
        "two-lines",
        "every-line-break",
        "each-line-cleaned",
        "empty-lines-at-the-ends",
        "runs-of-empty-lines",
        "one-empty-line-stays",
        "control-and-format-characters",
        "nothing-a-text-keeps",
        "empty",
    ],
)
def test_clean_text_keeps_lines(raw: str, cleaned: str) -> None:
    assert clean_text(raw) == cleaned


@given(st.text())
def test_a_cleaned_line_is_one_line_without_a_character_that_hides_or_reorders(raw: str) -> None:
    cleaned = clean_line(raw)

    assert clean_line(cleaned) == cleaned
    assert cleaned == cleaned.strip()
    assert "  " not in cleaned
    assert len(cleaned.splitlines()) <= 1
    assert all(unicodedata.category(character) not in {"Cc", "Cf"} for character in cleaned)
    assert "\x00" not in cleaned
    assert len(cleaned) <= len(raw)


@given(st.text())
def test_a_cleaned_text_is_cleaned_lines_without_empty_ends_or_two_empty_lines_in_a_row(
    raw: str,
) -> None:
    cleaned = clean_text(raw)
    lines = cleaned.split("\n") if cleaned else []

    assert clean_text(cleaned) == cleaned
    assert all(clean_line(line) == line for line in lines)
    assert not lines or (lines[0] and lines[-1])
    assert "\n\n\n" not in cleaned
    assert "\x00" not in cleaned
    assert len(cleaned) <= len(raw)
