"""`Container`: every long-lived object of a process, built once (backend/CLAUDE.md).

This is the only place where classes are wired together. The API, the relay, scripts
and tests all start from `Container.build(settings)` and read what they need from its
attributes; nothing else constructs a service, a repository factory or an adapter.

Building connects to nothing. Postgres, Redis, ScyllaDB and the media box are each
reached on first use, so a process starts with any of them down (SPEC §9).
"""

import logging
from typing import Self

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.db.database import Database
from app.integrations.media.client import MediaClient
from app.integrations.redis.client import RedisClient
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.quote_bus import QuoteBus
from app.integrations.redis.rate_limiter import InProcessRateLimiter, RedisRateLimiter
from app.integrations.scylla.client import ScyllaClient
from app.integrations.scylla.quote_history import QuoteHistoryStore
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.account import AccountService
from app.services.admin_users import AdminUserService
from app.services.announcer import Announcer, LeaderboardPace
from app.services.audit import AuditService
from app.services.broadcast_audience import BroadcastAudience
from app.services.broadcast_view import BroadcastLookup, BroadcastView
from app.services.broadcasts import BroadcastService
from app.services.chat import ChatService
from app.services.codes import CodeService
from app.services.email_policy import EmailPolicy
from app.services.grants import GrantService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.identity import IdentityService
from app.services.invariants import InvariantChecker
from app.services.leaderboard import LeaderboardService
from app.services.ledger import LedgerService
from app.services.live_events import LiveEvents
from app.services.live_hub import LiveHub
from app.services.live_quotes import LiveQuoteService
from app.services.market_access import MarketAccess
from app.services.market_lifecycle import MarketLifecycleService
from app.services.market_view import MarketView
from app.services.markets import MarketService
from app.services.notices import NoticeService
from app.services.profile import ProfileService
from app.services.public_config import PublicConfigService
from app.services.quotes import QuoteService
from app.services.risk import RiskService
from app.services.trading import TradingService
from app.services.upkeep import UserUpkeepService
from app.services.vouchers import VoucherService

logger = logging.getLogger(__name__)


class Container:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock,
        database: Database | None,
    ) -> None:
        self.settings = settings
        self.clock = clock

        # Stores and their adapters.
        self._owns_database = database is None
        self.database = database or Database(settings.postgres.app_url, settings.postgres)
        self.uow_factory: UowFactory = self._new_unit_of_work
        self.redis = RedisClient(settings.redis)
        self.market_cache = MarketCache(self.redis, settings.redis)
        self.quote_bus = QuoteBus(self.redis)
        self.rate_limiter = RedisRateLimiter(self.redis)
        self.fallback_rate_limiter = InProcessRateLimiter(clock)
        self.scylla = ScyllaClient(settings.scylla)
        self.quote_history = QuoteHistoryStore(self.scylla, settings.scylla)
        self.media = MediaClient(settings.broadcast)

        # The event stream. A unit of work hands what its transaction announced to
        # the announcer once the commit has returned; the hub holds the open
        # streams and gives each event to the ones it is for.
        self.live = LiveHub(settings.live, clock)
        self.announcer = Announcer(self.live, self.market_cache)
        self.leaderboard_pace = LeaderboardPace(settings.live, clock, self.live.publish)
        self.live.watch(self.leaderboard_pace.saw)

        # Services, in dependency order.
        uow_factory = self.uow_factory
        self.market_view = MarketView(settings)
        self.market_access = MarketAccess()
        self.live_events = LiveEvents(self.market_view)
        self.ledger = LedgerService()
        self.audit = AuditService(clock)
        self.email_policy = EmailPolicy(settings)
        self.public_config = PublicConfigService(settings)
        self.health = HealthService(self.database, self.redis, self.scylla, self.media)
        self.invariants = InvariantChecker()
        self.notices = NoticeService(uow_factory, clock)

        # The live chat of a market is open while its broadcast is LIVE: it asks the
        # lookup, and every stored end of a broadcast clears it.
        self.broadcast_view = BroadcastView(settings)
        self.broadcast_lookup = BroadcastLookup(self.broadcast_view, clock)
        self.broadcast_audience = BroadcastAudience(settings, clock)
        self.chat = ChatService(
            uow_factory,
            settings,
            clock,
            self.market_access,
            self.broadcast_lookup,
            self.audit,
            self.rate_limiter,
            self.fallback_rate_limiter,
            self.live_events,
            self.email_policy,
        )

        self.grants = GrantService(uow_factory, settings, clock, self.ledger, self.audit)
        self.vouchers = VoucherService(uow_factory, settings, clock, self.ledger)
        self.upkeep = UserUpkeepService(uow_factory, settings, clock, self.grants, self.vouchers)
        self.identity = IdentityService(uow_factory, self.email_policy, self.upkeep)
        self.account = AccountService(
            uow_factory, settings, clock, self.grants, self.market_view, self.notices
        )
        self.profile = ProfileService(uow_factory, settings, clock, self.grants, self.account)
        self.admin_users = AdminUserService(
            uow_factory, settings, clock, self.email_policy, self.ledger, self.audit, self.chat
        )
        self.codes = CodeService(
            uow_factory, settings, clock, self.ledger, self.audit, self.rate_limiter
        )
        self.leaderboard = LeaderboardService(uow_factory, settings, self.market_view)

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
            self.notices,
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
        self.broadcasts = BroadcastService(
            uow_factory,
            settings,
            clock,
            self.broadcast_view,
            self.market_view,
            self.market_access,
            self.audit,
            self.media,
            self.broadcast_audience,
            self.live_events,
            self.chat,
        )

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        clock: Clock | None = None,
        database: Database | None = None,
    ) -> Self:
        """Construct every adapter and service exactly once.

        `clock` defaults to the system clock; tests pass a frozen one. A `database`
        passed in is shared, not owned: `stop` leaves it open for whoever built it.
        """
        return cls(settings, clock=clock or SystemClock(), database=database)

    async def start(self) -> None:
        """Start what runs in the background: the listener for live quotes and the
        ticker of the event stream.

        Safe with Redis or ScyllaDB down. The listener waits at most one Redis timeout
        for its first connection and otherwise starts disconnected and keeps retrying;
        ScyllaDB is not contacted until its first statement. The ticker reaches no
        store: it beats on the open streams and ends the ones whose life is over.
        """
        await self.quote_bus.start()
        await self.live.start()

    async def stop(self) -> None:
        """End every open stream and release every connection. Safe to call without
        `start`, and once only.

        The streams go first: the pace of the leaderboard drops what it still
        owes, then the hub ends every stream at once and its ticker. Each step
        runs even if an earlier one failed; the failure is logged.
        """
        steps = [
            self._stop_leaderboard_pace,
            self.live.stop,
            self.quote_bus.stop,
            self.redis.close,
            self.scylla.close,
        ]
        if self._owns_database:
            steps.append(self.database.dispose)
        for step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Shutdown step %s failed", step.__qualname__)

    async def _stop_leaderboard_pace(self) -> None:
        self.leaderboard_pace.stop()

    def _new_unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.database.session_factory, self.announcer)
