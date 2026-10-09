"""ScyllaDB adapters: the price history behind the charts (SPEC §9).

ScyllaDB never decides anything. With it down, trades still commit, the outbox backs
up and the charts are served from Postgres.
"""

from app.integrations.scylla.client import ScyllaClient, ScyllaUnavailableError
from app.integrations.scylla.quote_history import QuoteHistoryStore, QuotePoint

__all__ = ["QuoteHistoryStore", "QuotePoint", "ScyllaClient", "ScyllaUnavailableError"]
