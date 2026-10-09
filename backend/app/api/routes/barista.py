"""`/barista/*`: role BARISTA or ADMIN (SPEC §2, §10). The guard is on the whole prefix."""

from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import BaristaUser, CoffeeTicketServiceDep
from app.schemas.barista import CoffeeTicketRedeemResponse
from app.schemas.vouchers import CODE_INPUT_MAX_LENGTH

router = APIRouter(prefix="/barista", tags=["barista"])

TicketCode = Annotated[
    str,
    Path(
        min_length=1,
        max_length=CODE_INPUT_MAX_LENGTH,
        description="The ticket's code as scanned or typed; dashes and case do not matter.",
    ),
]


@router.post("/coffee-tickets/{code}/redeem", summary="Mark a scanned coffee ticket as used")
async def redeem_coffee_ticket(
    code: TicketCode, barista: BaristaUser, tickets: CoffeeTicketServiceDep
) -> CoffeeTicketRedeemResponse:
    return await tickets.redeem(barista, code)
