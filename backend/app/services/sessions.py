"""Sessions: the opaque cookie token and what it resolves to (SPEC §3, Appendix A.3).

The cookie holds a random token. `sessions.id` is its SHA-256, so neither the database
nor Redis ever holds a value that opens a session. The row lives in Postgres and is
cached in Redis; with Redis down every request reads Postgres.

A session is valid while `now < expires_at` (absolute, `SESSION_TTL_DAYS`) and
`now < last_seen_at + SESSION_IDLE_TTL_DAYS` (idle).
"""

from dataclasses import dataclass
from datetime import datetime

from app.core.clock import Clock
from app.core.config import Settings
from app.core.identity import AuthenticatedUser
from app.integrations.redis.session_cache import SessionCache
from app.models.user import User
from app.repositories.unit_of_work import UnitOfWork
from app.services import UowFactory
from app.services.upkeep import UserUpkeepService


@dataclass(frozen=True, slots=True)
class NewSession:
    token: str
    """What the cookie carries. It exists only here and in the browser."""

    expires_at: datetime


class SessionService:
    def __init__(
        self,
        uow_factory: UowFactory,
        settings: Settings,
        clock: Clock,
        cache: SessionCache,
        upkeep: UserUpkeepService,
    ) -> None:
        self._uow_factory = uow_factory
        self._auth = settings.auth
        self._cache_ttl_seconds = settings.redis.session_cache_ttl_seconds
        self._clock = clock
        self._cache = cache
        self._upkeep = upkeep

    async def create(self, uow: UnitOfWork, user: User) -> NewSession:
        """Open a session for `user` inside the caller's transaction (sign-in, A.3).

        1. `token = generate_token(LOGIN_TOKEN_BYTES)`.
        2. Insert `UserSession(id=sha256_hex(token), user_id=user.id, created_at=now,
           expires_at=now + SESSION_TTL_DAYS, last_seen_at=now)`.
        3. Return the token and `expires_at`.

        Nothing is cached here: the transaction has not committed yet. The first
        `authenticate` misses the cache and loads the row.
        """
        raise NotImplementedError

    async def authenticate(self, token: str) -> AuthenticatedUser | None:
        """Resolve a cookie token to its user, or None if it opens no valid session.

        Never raises for a bad token. `session_id = sha256_hex(token)`.

        Cache hit (`SessionCache.get(session_id)`): the payload is
        `{"user_id", "email", "canonical_email", "role", "status", "expires_at",
        "last_seen_at"}`, with the UUID and the two instants as ISO 8601 strings. Check
        both validity rules against `clock.now()`; if either fails, delete the cache
        entry and return None. Otherwise return the user. A payload that does not parse
        is a miss.

        Cache miss:
        1. In one transaction load the `sessions` row and its user. No row or no user:
           return None. A row that fails a validity rule is deleted and None returned.
        2. Still in that transaction, if `now - last_seen_at >=
           SESSION_TOUCH_INTERVAL_SECONDS`, write `sessions.touch(session_id, now)`.
        3. After the commit run the lazy upkeep, `UserUpkeepService.run(user.id)`. An
           exception from it is logged with its traceback and swallowed: signing in
           must not depend on it.
        4. Cache the payload for the smaller of `SESSION_CACHE_TTL_SECONDS` and the
           whole seconds until the session stops being valid.
        5. Return the user.

        A SUSPENDED user is returned like any other, with that status; the route guards
        refuse it (`app/api/deps.py`).
        """
        raise NotImplementedError

    async def revoke(self, token: str) -> None:
        """Sign out: delete the `sessions` row in a transaction, then the cache entry.

        Idempotent; an unknown token is not an error. If Redis is down the entry stays
        cached until its TTL runs out (`SessionCache.delete`).
        """
        raise NotImplementedError
