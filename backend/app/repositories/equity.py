"""Stream the immutable events that change a user's equity, in one database snapshot."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, String, Uuid, case, cast, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Outcome, TradeSide
from app.models.ledger import Account, JournalEntry, Posting
from app.models.market import Resolution
from app.models.trading import Position, Trade


@dataclass(frozen=True, slots=True)
class EquityEvent:
    at: datetime
    balance_delta_micro: int
    market_id: UUID | None
    outcome: Outcome | None
    shares_delta_micro: int
    p_yes_micro: int | None
    settled: bool


class EquityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def events(self, user_id: UUID, until: datetime) -> AsyncIterator[EquityEvent]:
        """Include other traders' price movements and settlements, even zero payouts.

        The cursor fetches in batches rather than materializing a lifetime of trades.
        A market's version orders trades sharing a timestamp. The service combines
        all events at one timestamp, so a buy or settlement cannot be counted twice
        while its ledger leg and position leg are being replayed.
        """
        market_ids = select(Position.market_id).where(Position.user_id == user_id).distinct()
        zero = literal(0, BigInteger)
        no_market = literal(None, Uuid)
        no_outcome = literal(None, String)
        no_price = literal(None, BigInteger)
        postings = (
            select(
                JournalEntry.created_at.label("at"),
                Posting.amount_micro.label("balance_delta"),
                no_market.label("market_id"),
                no_outcome.label("outcome"),
                zero.label("shares_delta"),
                no_price.label("price"),
                literal(False).label("settled"),
                literal(1).label("kind"),
                Posting.id.label("sequence"),
            )
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .join(Account, Account.id == Posting.account_id)
            .where(Account.owner_id == user_id, JournalEntry.created_at <= until)
        )
        own_shares = case(
            (Trade.user_id != user_id, zero),
            (Trade.side == TradeSide.BUY, Trade.shares_micro),
            else_=-Trade.shares_micro,
        )
        trades = select(
            Trade.created_at,
            zero,
            Trade.market_id,
            cast(Trade.outcome, String),
            own_shares,
            Trade.p_yes_after_micro,
            literal(False),
            literal(0),
            Trade.market_version,
        ).where(Trade.market_id.in_(market_ids), Trade.created_at <= until)
        settlements = select(
            Resolution.created_at,
            zero,
            Resolution.market_id,
            no_outcome,
            zero,
            no_price,
            literal(True),
            literal(2),
            zero,
        ).where(Resolution.market_id.in_(market_ids), Resolution.created_at <= until)
        events = union_all(postings, trades, settlements).subquery()
        statement = select(events).order_by(
            events.c.at, events.c.kind, events.c.market_id, events.c.sequence
        )
        result = await self._session.stream(statement.execution_options(yield_per=1000))
        try:
            async for row in result:
                yield EquityEvent(
                    at=row.at,
                    balance_delta_micro=row.balance_delta,
                    market_id=row.market_id,
                    outcome=Outcome(row.outcome) if row.outcome is not None else None,
                    shares_delta_micro=row.shares_delta,
                    p_yes_micro=row.price,
                    settled=row.settled,
                )
        finally:
            await result.close()
