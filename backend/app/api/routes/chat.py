"""The live chat beside a market's live stream (SPEC §6, §10, Appendix A.2).

Whoever is inside a market reads its chat and writes in it while the market's live
stream is on. A market the caller is not inside of, and one that was never opened, is
answered 404 by every operation here, like an id no market has.
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import ChatServiceDep, CurrentUser
from app.schemas.chat import ChatMessageItem, ChatResponse, PostChatMessageRequest

router = APIRouter(prefix="/markets", tags=["chat"])


@router.get(
    "/{market_id}/chat",
    summary="The live chat of a market: whether it is open, and its latest messages",
)
async def get_chat(market_id: UUID, viewer: CurrentUser, chat: ChatServiceDep) -> ChatResponse:
    return await chat.read(viewer, market_id)


@router.post(
    "/{market_id}/chat",
    status_code=status.HTTP_201_CREATED,
    summary="Write a message in the live chat of a market",
)
async def post_chat_message(
    market_id: UUID,
    body: PostChatMessageRequest,
    user: CurrentUser,
    chat: ChatServiceDep,
) -> ChatMessageItem:
    return await chat.post(user, market_id, body)
