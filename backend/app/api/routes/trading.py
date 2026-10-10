"""Quotes and trades (SPEC §8, §10, Appendix A.4)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from app.api.deps import CurrentUser, QuoteServiceDep, TradingServiceDep
from app.schemas.trading import QuoteQuery, QuoteResponse, TradeRequest, TradeResponse

router = APIRouter(prefix="/markets", tags=["trading"])

IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description=(
            "A UUID the client makes up per trade and repeats on every retry of it. "
            "The same key returns the same trade; it never executes twice."
        ),
    ),
]


@router.get(
    "/{market_id}/quote",
    summary="Price a trade for the caller, or answer the error the trade would get",
)
async def get_quote(
    market_id: UUID,
    query: Annotated[QuoteQuery, Query()],
    viewer: CurrentUser,
    quotes: QuoteServiceDep,
) -> QuoteResponse:
    return await quotes.quote(viewer, market_id, query)


@router.post(
    "/{market_id}/trades",
    status_code=status.HTTP_201_CREATED,
    summary="Buy or sell shares, idempotently",
)
async def create_trade(
    market_id: UUID,
    body: TradeRequest,
    idempotency_key: IdempotencyKey,
    user: CurrentUser,
    trading: TradingServiceDep,
) -> TradeResponse:
    return await trading.trade(user, market_id, idempotency_key, body)
