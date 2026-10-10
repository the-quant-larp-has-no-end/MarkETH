"""What people ask about a market, and what they are answered: `questions`.

A question is waiting while `answered_at` and `removed_at` are NULL, answered once
`answered_at` is set, and removed once `removed_at` is set. No column stores that word.
An answer is written once and never changed. A removed question keeps its row: the app
role cannot DELETE from this table (`app.models.privileges`).

The lengths the CHECKs take are those of `app.core.text`; the lengths a person may use
are settings below them, applied by the service.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.text import ANSWER_STORED_MAX_LENGTH, QUESTION_STORED_MAX_LENGTH
from app.db.base import Base


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            f"char_length(text) BETWEEN 1 AND {QUESTION_STORED_MAX_LENGTH}", name="text_length"
        ),
        CheckConstraint(
            "answer_text IS NULL "
            f"OR char_length(answer_text) BETWEEN 1 AND {ANSWER_STORED_MAX_LENGTH}",
            name="answer_length",
        ),
        # An answer has its text, its author and its instant, or none of the three.
        CheckConstraint(
            "(answer_text IS NULL) = (answered_by IS NULL) "
            "AND (answer_text IS NULL) = (answered_at IS NULL)",
            name="answered_whole",
        ),
        CheckConstraint("(removed_by IS NULL) = (removed_at IS NULL)", name="removed_whole"),
        # Every list and every count of a market reads this one range.
        Index(None, "market_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"))
    asked_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime]
    answer_text: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    answered_at: Mapped[datetime | None]
    removed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    removed_at: Mapped[datetime | None]
