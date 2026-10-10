"""Queries on `questions`."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discussion import Question
from app.repositories.base import BaseRepository


class QuestionRepository(BaseRepository[Question]):
    """The questions of a market are read in the order of their ids, which is the
    order they were asked in (`ix_questions_market_id_id`)."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Question)
