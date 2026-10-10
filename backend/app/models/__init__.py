"""Every ORM model of SPEC §7, one module per aggregate.

Importing this package registers all tables on `Base.metadata`; Alembic relies on that.
Models are tables, constraints and indexes only. They have no behaviour and no
relationship attributes: repositories query what a service needs, nothing lazy-loads.

Three modules beside the models complete the schema. `triggers` and `privileges` hold
the SQL a table definition cannot state; the baseline revision runs it once the tables
exist. `fingerprint` reduces all of it to one hash, which says whether a database was
built from this code.
"""

from app.models.code import Code, CodeBatch
from app.models.grant import Grant
from app.models.ledger import Account, JournalEntry, Posting
from app.models.market import Market, Resolution
from app.models.ops import Alert, AuditLogEntry, OutboxEvent
from app.models.trading import IdempotencyKey, Position, Trade
from app.models.user import User
from app.models.voucher import Voucher

__all__ = [
    "Account",
    "Alert",
    "AuditLogEntry",
    "Code",
    "CodeBatch",
    "Grant",
    "IdempotencyKey",
    "JournalEntry",
    "Market",
    "OutboxEvent",
    "Position",
    "Posting",
    "Resolution",
    "Trade",
    "User",
    "Voucher",
]
