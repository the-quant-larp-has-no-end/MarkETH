"""What an admin does in the live chat of a market: remove a message, audit-logged
(SPEC §6, §10, Appendix A.2)."""

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import AdminUser, ChatServiceDep
from app.schemas.chat import RemovedResponse

router = APIRouter(prefix="/markets", tags=["admin-chat"])


@router.post(
    "/{market_id}/chat/{message_id}/remove",
    summary="Remove a message from the live chat of a market",
)
async def remove_chat_message(
    market_id: UUID,
    message_id: UUID,
    admin: AdminUser,
    chat: ChatServiceDep,
) -> RemovedResponse:
    return await chat.remove(admin, market_id, message_id)
