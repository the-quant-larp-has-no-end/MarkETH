"""Coffee tickets of the signed-in user (SPEC §2, §10, Appendix A.2, A.6)."""

from fastapi import APIRouter, status

from app.api.deps import CoffeeTicketServiceDep, CurrentUser
from app.schemas.coffee_tickets import CoffeeTicketIssuedResponse, CoffeeTicketListResponse

router = APIRouter(tags=["coffee-tickets"])


@router.post(
    "/coffee-tickets",
    status_code=status.HTTP_201_CREATED,
    summary="Turn one voucher into a coffee ticket",
)
async def issue_coffee_ticket(
    user: CurrentUser, tickets: CoffeeTicketServiceDep
) -> CoffeeTicketIssuedResponse:
    return await tickets.issue(user)


@router.get("/me/coffee-tickets", summary="Own coffee tickets, pending and past")
async def list_my_coffee_tickets(
    user: CurrentUser, tickets: CoffeeTicketServiceDep
) -> CoffeeTicketListResponse:
    return await tickets.list_for_user(user)
