"""`Container`: every long-lived object of a process, built once (backend/CLAUDE.md).

This is the only place where classes are wired together. The API, the relay, scripts
and tests all start from `Container.build(settings)` and read what they need from its
attributes; nothing else constructs a service, a repository factory or an adapter.

Building connects to nothing. Postgres, Redis and ScyllaDB are each reached on first
use, so a process starts with any of them down (SPEC §9).
"""

import logging
from typing import Self

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.db.database import Database
from app.integrations.mail.mailer import Mailer, SmtpMailer
from app.integrations.redis.client import RedisClient
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.quote_bus import QuoteBus
from app.integrations.redis.rate_limiter import InProcessRateLimiter, RedisRateLimiter
from app.integrations.redis.session_cache import SessionCache
from app.integrations.scylla.client import ScyllaClient
from app.integrations.scylla.quote_history import QuoteHistoryStore
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.account import AccountService
from app.services.audit import AuditService
from app.services.auth import AuthService
from app.services.coffee_tickets import CoffeeTicketService
from app.services.email_policy import EmailPolicy
from app.services.grants import GrantService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.invariants import InvariantChecker
from app.services.ledger import LedgerService
from app.services.live_quotes import LiveQuoteService
from app.services.market_lifecycle import MarketLifecycleService
from app.services.market_view import MarketView
from app.services.markets import MarketService
from app.services.public_config import PublicConfigService
from app.services.quotes import QuoteService
from app.services.risk import RiskService
from app.services.sessions import SessionService
from app.services.trading import TradingService
from app.services.upkeep import UserUpkeepService
from app.services.voucher_codes import VoucherCodeService

logger = logging.getLogger(__name__)


class Container:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock,
        mailer: Mailer,
        database: Database | None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.mailer = mailer

        # Stores and their adapters.
        self._owns_database = database is None
        self.database = database or Database(settings.postgres.app_url, settings.postgres)
        self.uow_factory: UowFactory = self._new_unit_of_work
        self.redis = RedisClient(settings.redis)
        self.session_cache = SessionCache(self.redis)
        self.market_cache = MarketCache(self.redis, settings.redis)
        self.quote_bus = QuoteBus(self.redis)
        self.rate_limiter = RedisRateLimiter(self.redis)
        self.fallback_rate_limiter = InProcessRateLimiter(clock)
        self.scylla = ScyllaClient(settings.scylla)
        self.quote_history = QuoteHistoryStore(self.scylla, settings.scylla)

        # Services, in dependency order.
        uow_factory = self.uow_factory
        self.market_view = MarketView(settings)
        self.ledger = LedgerService()
        self.audit = AuditService(clock)
        self.email_policy = EmailPolicy(settings)
        self.public_config = PublicConfigService(settings)
        self.health = HealthService(self.database, self.redis, self.scylla)
        self.invariants = InvariantChecker()

        self.grants = GrantService(uow_factory, settings, clock, self.ledger, self.audit)
        self.coffee_tickets = CoffeeTicketService(uow_factory, settings, clock, self.ledger)
        self.upkeep = UserUpkeepService(uow_factory, self.grants, self.coffee_tickets)
        self.sessions = SessionService(
            uow_factory, settings, clock, self.session_cache, self.upkeep
        )
        self.auth = AuthService(
            uow_factory,
            settings,
            clock,
            self.email_policy,
            self.sessions,
            self.grants,
            self.rate_limiter,
            mailer,
        )
        self.account = AccountService(uow_factory, settings, clock, self.grants, self.market_view)
        self.voucher_codes = VoucherCodeService(
            uow_factory, settings, clock, self.ledger, self.audit, self.rate_limiter
        )

        self.live_quotes = LiveQuoteService(
            uow_factory, settings, clock, self.market_view, self.quote_bus
        )
        self.markets = MarketService(
            uow_factory, settings, clock, self.market_view, self.market_cache
        )
        self.market_lifecycle = MarketLifecycleService(
            uow_factory,
            settings,
            clock,
            self.market_view,
            self.ledger,
            self.audit,
            self.markets,
            self.market_cache,
            self.live_quotes,
        )
        self.risk = RiskService(uow_factory, settings, clock, self.market_view, self.audit)
        self.quotes = QuoteService(uow_factory, settings, clock, self.market_view)
        self.trading = TradingService(
            uow_factory,
            settings,
            clock,
            self.market_view,
            self.quotes,
            self.ledger,
            self.invariants,
            self.audit,
            self.rate_limiter,
            self.fallback_rate_limiter,
            self.market_cache,
            self.live_quotes,
        )
        self.history = HistoryService(
            uow_factory, settings, clock, self.market_view, self.quote_history
        )

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        clock: Clock | None = None,
        mailer: Mailer | None = None,
        database: Database | None = None,
    ) -> Self:
        """Construct every adapter and service exactly once.

        `clock` and `mailer` default to the system clock and SMTP; tests pass a frozen
        clock and a mailer that records. A `database` passed in is shared, not owned:
        `stop` leaves it open for whoever built it.
        """
        return cls(
            settings,
            clock=clock or SystemClock(),
            mailer=mailer or SmtpMailer(settings.mail),
            database=database,
        )

    async def start(self) -> None:
        """Start what runs in the background: the listener for live quotes.

        Safe with Redis or ScyllaDB down. The listener waits at most one Redis timeout
        for its first connection and otherwise starts disconnected and keeps retrying;
        ScyllaDB is not contacted until its first statement.
        """
        await self.quote_bus.start()

    async def stop(self) -> None:
        """Release every connection. Safe to call without `start`, and once only.

        Each step runs even if an earlier one failed; the failure is logged.
        """
        steps = [self.quote_bus.stop, self.redis.close, self.scylla.close]
        if self._owns_database:
            steps.append(self.database.dispose)
        for step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Shutdown step %s failed", step.__qualname__)

    def _new_unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.database.session_factory)
