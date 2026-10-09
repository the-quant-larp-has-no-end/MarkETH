"""Every ORM model of SPEC §7, one module per aggregate.

Importing this package registers all tables on `Base.metadata`; Alembic relies on that.
Models are tables, constraints and indexes only. They have no behaviour and no
relationship attributes: repositories query what a service needs, nothing lazy-loads.
"""

from app.models.coffee_ticket import CoffeeTicket
from app.models.ledger import Account, JournalEntry, Posting
from app.models.market import Market, Resolution
from app.models.ops import Alert, AuditLogEntry, OutboxEvent
from app.models.trading import IdempotencyKey, Position, Trade
from app.models.user import LoginToken, User, UserSession
from app.models.voucher import VoucherCode, VoucherCodeBatch, VoucherGrant

__all__ = [
    "Account",
    "Alert",
    "AuditLogEntry",
    "CoffeeTicket",
    "IdempotencyKey",
    "JournalEntry",
    "LoginToken",
    "Market",
    "OutboxEvent",
    "Position",
    "Posting",
    "Resolution",
    "Trade",
    "User",
    "UserSession",
    "VoucherCode",
    "VoucherCodeBatch",
    "VoucherGrant",
]
