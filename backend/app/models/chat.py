"""The live chat beside a market's live stream: `chat_messages`.

A row exists exactly while its message can be read: the chat of a broadcast is deleted
when the broadcast ends, and a removed message is deleted on the spot. A message is
never edited, so the app role cannot UPDATE this table (`app.models.privileges`).

`from_vis` is true when the author was an admin at the moment the message was stored.
It is kept with the row, so a role that changes later rewrites nothing that was said.
"""

from datetime import datetime
from uuid import UUID, uuid7

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.text import CHAT_MESSAGE_STORED_MAX_LENGTH
from app.db.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            f"char_length(text) BETWEEN 1 AND {CHAT_MESSAGE_STORED_MAX_LENGTH}",
            name="text_length",
        ),
        # The latest messages of a broadcast.
        Index(None, "broadcast_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    market_id: Mapped[UUID] = mapped_column(ForeignKey("markets.id"))
    broadcast_id: Mapped[UUID] = mapped_column(ForeignKey("broadcasts.id"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    from_vis: Mapped[bool]
    created_at: Mapped[datetime]
