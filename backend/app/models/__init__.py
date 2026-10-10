"""Every ORM model of SPEC §7, one module per aggregate.

Importing this package registers all tables on `Base.metadata`; Alembic relies on that.
Models are tables, constraints and indexes only. They have no behaviour and no
relationship attributes: repositories query what a service needs, nothing lazy-loads.

Three modules beside the models complete the schema. `triggers` and `privileges` hold
the SQL a table definition cannot state. `fingerprint` reduces all of it to one hash,
which says whether a database has the schema of this code. A change to any of it comes
with a revision of `migrations/versions/`, and `revisions` holds what one needs.
"""

from app.models.broadcast import Broadcast
from app.models.chat import ChatMessage
from app.models.code import Code, CodeBatch
from app.models.discussion import Question
from app.models.grant import Grant
from app.models.ledger import Account, JournalEntry, Posting
from app.models.market import Market, Resolution
from app.models.notice import Notice
from app.models.ops import Alert, AuditLogEntry, OutboxEvent
from app.models.streak import Streak
from app.models.trading import IdempotencyKey, Position, Trade
from app.models.user import User
from app.models.voucher import Voucher

__all__ = [
    "Account",
    "Alert",
    "AuditLogEntry",
    "Broadcast",
    "ChatMessage",
    "Code",
    "CodeBatch",
    "Grant",
    "IdempotencyKey",
    "JournalEntry",
    "Market",
    "Notice",
    "OutboxEvent",
    "Position",
    "Posting",
    "Question",
    "Resolution",
    "Streak",
    "Trade",
    "User",
    "Voucher",
]
