"""Redis adapters: rate limits, the market cache and live quotes (SPEC §9).

Redis never decides a balance, a price or whether a trade is allowed. With Redis down,
trades still commit; only speed, limits and live updates degrade.
"""

from app.integrations.redis.client import RedisClient, RedisUnavailableError
from app.integrations.redis.market_cache import MarketCache
from app.integrations.redis.quote_bus import QuoteBus
from app.integrations.redis.rate_limiter import (
    InProcessRateLimiter,
    RateLimit,
    RateLimitDecision,
    RateLimiter,
    RedisRateLimiter,
)

__all__ = [
    "InProcessRateLimiter",
    "MarketCache",
    "QuoteBus",
    "RateLimit",
    "RateLimitDecision",
    "RateLimiter",
    "RedisClient",
    "RedisRateLimiter",
    "RedisUnavailableError",
]
