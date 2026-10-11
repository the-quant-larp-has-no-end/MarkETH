"""The live chat beside a market's live stream (SPEC §6, §10, Appendix A.2).

A chat is open exactly while its market has a live stream that is on. Whoever is inside
the market reads it and writes in it. Nothing of a chat is kept once its live stream has
ended: a message exists exactly as long as it can be read.

A message is plain text, one line. A client shows it as text: nothing in it is markup
and no part of it is a link.

The length below is what the database takes. The length a person may use is a setting,
told in `GET /config` and applied by the service, which cleans a text before it
measures it.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.core.text import CHAT_MESSAGE_STORED_MAX_LENGTH
from app.schemas.common import RequestModel, ResponseModel

ChatText = Annotated[str, Field(min_length=1, max_length=CHAT_MESSAGE_STORED_MAX_LENGTH)]
"""A chat message as a request holds it: one line once it is cleaned."""


class PostChatMessageRequest(RequestModel):
    """Body of `POST /markets/{id}/chat`."""

    text: ChatText
    """One line of at most `chat_message_max_length` characters (`GET /config`),
    counted once the text is cleaned: every run of whitespace is one space."""


class ChatMessageItem(ResponseModel):
    """One message of a chat."""

    id: UUID
    username: str
    """The author's username, as it reads when the request is answered."""

    text: str
    from_vis: bool
    """The author was an admin when the message was stored. The server says so; a
    client shows it and decides nothing from a name."""


class ChatResponse(ResponseModel):
    """`GET /markets/{id}/chat`: the chat of a market as it stands."""

    open: bool
    """The market has a live stream that is on: messages can be read and written."""

    broadcast_id: UUID | None
    """The live stream the chat belongs to; null exactly when `open` is false. A
    message that is pushed names it, so a client drops one of another live stream."""

    can_remove: bool
    """The caller is an admin."""

    items: list[ChatMessageItem]
    """The latest messages, earliest first, at most `chat_backlog_size` of them
    (`GET /config`). Empty while the chat is closed."""


class RemovedResponse(ResponseModel):
    """What an admin removed, and when."""

    id: UUID
    removed_at: datetime
