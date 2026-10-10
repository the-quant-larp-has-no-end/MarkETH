"""Text people write for other people to read: questions, answers and chat messages.

Two things are fixed here, once. The lengths the database and the request models take:
a CHECK of a table and the `maxLength` of a request field are the same number, so the
two cannot disagree. The length a person may use is a setting below that number and is
applied by the service. And how such a text is cleaned before it is stored, as two pure
functions: what they return holds no NUL, which Postgres refuses to store, and no
character that hides or reorders the text around it.

This module imports nothing from `app`.
"""

import unicodedata
from typing import Final

QUESTION_STORED_MAX_LENGTH: Final[int] = 500
"""The longest question the database takes: the CHECK of `questions.text`.
`DISCUSSION_QUESTION_MAX_LENGTH` cannot be set above it."""

ANSWER_STORED_MAX_LENGTH: Final[int] = 2000
"""The longest answer the database takes: the CHECK of `questions.answer_text`.
`DISCUSSION_ANSWER_MAX_LENGTH` cannot be set above it."""

CHAT_MESSAGE_STORED_MAX_LENGTH: Final[int] = 500
"""The longest chat message the database takes: the CHECK of `chat_messages.text`.
`CHAT_MESSAGE_MAX_LENGTH` cannot be set above it."""

CHAT_BACKLOG_MAX: Final[int] = 200
"""The most messages one read of a chat returns: the upper bound of `CHAT_BACKLOG_SIZE`."""

_REMOVED: Final[frozenset[str]] = frozenset({"Cc", "Cf"})
"""Unicode categories a text does not keep: control characters (a NUL, an escape) and
format characters (a zero-width space, a direction override)."""


def clean_line(raw: str) -> str:
    """`raw` as one line.

    Control and format characters go, except the ones that are whitespace; every run
    of whitespace, a tab or a line break included, becomes one space, and none stays
    at either end. Cleaning a cleaned line changes nothing.
    """
    kept = "".join(
        character
        for character in raw
        if character.isspace() or unicodedata.category(character) not in _REMOVED
    )
    return " ".join(kept.split())


def clean_text(raw: str) -> str:
    """`raw` as lines.

    The text is split at its line boundaries and each line is cleaned with
    `clean_line`. Empty lines at either end go, and two or more empty lines in a row
    become one. The lines are joined with a line feed. Cleaning a cleaned text changes
    nothing.
    """
    lines: list[str] = []
    for line in (clean_line(line) for line in raw.splitlines()):
        if line or (lines and lines[-1]):
            lines.append(line)
    if lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
