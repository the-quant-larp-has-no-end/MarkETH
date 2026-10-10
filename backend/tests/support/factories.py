"""Test data written straight to the database, with the books kept straight.

`Factory` puts users, markets and positions in place through the repositories and
`LedgerService`, without going through the services under test. Whatever it writes
leaves every invariant intact: entries balance, all accounts sum to zero, a market's
quantities equal the sum of its positions, no user balance is negative. These are the
rules `tests/support/books.py` holds a database against.

    user = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()                       # OPEN, default tier
    await factory.position(user, market, Outcome.YES, shares_micro=MICRO)
    admin = await factory.admin()                         # a bootstrap admin
    barista = await factory.barista()                     # a row with the role BARISTA
    as_user(user)                                         # a plain user
    as_user(admin)                                        # an admin: the address says so
    as_user(barista)                                      # a barista: the row says so
    as_user(user, UserRole.ADMIN)                         # a guard test names the role
    client.get("/api/me", headers=portal_headers(admin))  # through the real headers

Who has which role is worked out the way the application does it: the role of the row,
unless the address is a bootstrap admin of the test settings (tests/support/settings.py).
`factory.identity`, `as_user` and the real seam therefore agree about every user the
factory makes.

Rows come back detached and readable. Time is the container's clock.
"""

from datetime import timedelta
from typing import Final, Protocol
from urllib.parse import quote
from uuid import uuid7

from app.container import Container
from app.core.config import IdentitySettings
from app.core.degrees import CATALOGUE
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    AccountKind,
    DegreeLevel,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.identity import AuthenticatedUser, Caller, PortalIdentity
from app.models import Market, Position, Resolution, Trade, User
from app.pricing import lmsr
from app.services.email_policy import EmailPolicy
from app.services.ledger import LedgerRef, PostingLine, signup_grant_key, trade_key
from app.services.market_view import HIDDEN_STATUSES, SETTLED_STATUSES, UNSETTLED_STATUSES

DEFAULT_CLOSE_IN: Final[timedelta] = timedelta(days=7)
DEFAULT_RESOLVE_AFTER_CLOSE: Final[timedelta] = timedelta(days=7)
DEFAULT_QUESTION: Final[str] = "Will the Polymensa serve Älplermagronen on Friday?"

DEFAULT_DEGREE_LEVEL: Final[DegreeLevel] = CATALOGUE[0].level
DEFAULT_DEGREE_PROGRAMME: Final[str] = CATALOGUE[0].programmes[0]
"""The degree of a factory user: the first pair of the catalogue."""

type Actor = User | AuthenticatedUser | PortalIdentity | None
"""Whom a test can make the application believe is calling: a user row, a registered
caller built by hand, somebody the portal named who has no profile, or nobody."""

type Named = User | AuthenticatedUser | PortalIdentity
"""Anybody the portal can name: each has the address it sends and a display name."""


class ActAs(Protocol):
    """The `as_user` fixture. A `User` row acts with the role the application gives it
    (the row's own, or ADMIN for a bootstrap admin), or with `role` when a test names
    one. The other actors say for themselves what they are, so giving one of them a
    role is refused."""

    def __call__(self, who: Actor, role: UserRole | None = None) -> None: ...


class PortalHeaders(Protocol):
    """The `portal_headers` fixture: the two request headers in which the portal
    would name `who`."""

    def __call__(self, who: Named) -> dict[str, str]: ...


def identity_of(user: User, role: UserRole) -> AuthenticatedUser:
    """`user` as a registered caller who has `role`, whatever the row and the settings
    say. This is for a test of a guard, which asks what a role may do whoever holds
    it. The role a user of the factory has in effect is `Factory.identity`."""
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        name=user.name,
        username=user.username,
        degree_programme=user.degree_programme,
        role=role,
        status=user.status,
    )


def caller_of(who: Actor, role: UserRole | None, roles: EmailPolicy) -> Caller | None:
    """What `authenticate_request` returns for `who`: None for nobody, a `Caller`
    without a user for a bare `PortalIdentity`, a registered caller otherwise.

    A `User` row gets `role`, and without one the role it has in effect
    (`EmailPolicy.effective_role`), as the application would give it. Every other
    actor carries its own role, and a second one beside it would silently lose, so it
    is refused.
    """
    if isinstance(who, User):
        user = identity_of(who, role or roles.effective_role(who.email, who.role))
    elif role is not None:
        raise ValueError("only a `User` row takes a role; this actor carries its own")
    elif who is None:
        return None
    elif isinstance(who, PortalIdentity):
        return Caller(identity=who, user=None)
    else:
        user = who
    portal = PortalIdentity(email=user.email, name=user.name, role=user.role)
    return Caller(identity=portal, user=user)


def portal_headers_of(who: Named, identity: IdentitySettings) -> dict[str, str]:
    """The two headers the portal would put on a request of `who`: the address as the
    id, and the display name percent-encoded, which is how the portal sends whatever is
    outside ASCII (`Zo%C3%AB`). The header names are those of `identity`."""
    return {
        identity.header_user_id: who.email,
        identity.header_user_name: quote(who.name, safe=""),
    }


class Factory:
    def __init__(self, container: Container) -> None:
        self._uow_factory = container.uow_factory
        self._ledger = container.ledger
        self._clock = container.clock
        self._settings = container.settings
        self._roles = container.email_policy
        self._users_made = 0

    async def user(
        self,
        *,
        balance_micro: int = 0,
        email: str | None = None,
        username: str | None = None,
        name: str | None = None,
        degree_level: DegreeLevel = DEFAULT_DEGREE_LEVEL,
        degree_programme: str = DEFAULT_DEGREE_PROGRAMME,
        role: UserRole = UserRole.USER,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> User:
        """A user with a profile and their ledger account, created now, who accepted
        the usage policy at that instant.

        The user counts as having received the signup grant: the `grants` row
        exists, and a positive `balance_micro` is booked as that grant from
        `vis:issuance`. Left out, the n-th user of a test is `user{n}@…` at the first
        of `ALLOWED_EMAIL_DOMAINS`, with the username `user{n}` and the name
        `User {n}`. `email` is stored as given: pass an id as `EmailPolicy.identify`
        returns it, in lower case. `role` is what the row says, as if an admin had
        set it.
        """
        self._users_made += 1
        number = self._users_made
        domain = self._settings.identity.allowed_email_domains[0]
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await uow.users.add(
                User(
                    email=email or f"user{number}@{domain}",
                    name=f"User {number}" if name is None else name,
                    username=username or f"user{number}",
                    degree_level=degree_level,
                    degree_programme=degree_programme,
                    role=role,
                    status=status,
                    policy_accepted_at=now,
                    created_at=now,
                )
            )
            account = await uow.accounts.create_user_account(user.id)
            grant = await uow.grants.insert_if_absent(user.id, SIGNUP_GRANT_PERIOD, now)
            assert grant is not None
            if balance_micro:
                issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
                entry = await self._ledger.post(
                    uow,
                    kind=JournalKind.SIGNUP_GRANT,
                    ref_type=LedgerRef.GRANT,
                    ref_id=grant.id,
                    idempotency_key=signup_grant_key(user.id),
                    lines=[
                        PostingLine(issuance.id, -balance_micro),
                        PostingLine(account.id, balance_micro),
                    ],
                    at=now,
                )
                await uow.grants.set_entry(grant.id, entry.id)
        return user

    async def admin(self, index: int = 0, *, balance_micro: int = 0) -> User:
        """A registered bootstrap admin: the user whose address is the `index`-th of
        `ADMIN_EMAILS`.

        The test settings name two, so `admin()` and `admin(1)` are two admins: the
        second can settle a market the first traded (A.4) and act on the account of
        the first. The row says USER, as the profile service writes it; the address
        makes the admin, to `identity`, to `as_user` and to the application. One per
        index and test: the address is unique. An admin by the row alone is
        `user(role=UserRole.ADMIN)`.
        """
        addresses = self._settings.identity.admin_emails
        if not 0 <= index < len(addresses):
            raise ValueError(
                f"the test settings name {len(addresses)} admin address(es); "
                f"there is none with index {index}"
            )
        return await self.user(
            balance_micro=balance_micro,
            # A list entry may be written with capitals; the row holds the id.
            email=self._roles.identify(addresses[index]),
            username=f"admin{index}",
            name=f"Admin {index}",
        )

    async def barista(self, index: int = 0, *, balance_micro: int = 0) -> User:
        """A registered user whose row has the role BARISTA: `barista{index}@…`, with
        the username `barista{index}`. One per index and test."""
        if index < 0:
            raise ValueError(f"there is no barista with index {index}")
        domain = self._settings.identity.allowed_email_domains[0]
        return await self.user(
            balance_micro=balance_micro,
            email=f"barista{index}@{domain}",
            username=f"barista{index}",
            name=f"Barista {index}",
            role=UserRole.BARISTA,
        )

    def identity(self, user: User) -> AuthenticatedUser:
        """The registered caller a request of `user` resolves to: the row, with the role
        it has in effect (`EmailPolicy.effective_role`). For calling a service directly
        as that user."""
        return identity_of(user, self._roles.effective_role(user.email, user.role))

    async def market(
        self,
        *,
        status: MarketStatus = MarketStatus.OPEN,
        b_micro: int | None = None,
        close_in: timedelta = DEFAULT_CLOSE_IN,
        created_by: User | None = None,
        tier: MarketTier = MarketTier.DEFAULT,
        question: str = DEFAULT_QUESTION,
        outcome: ResolutionOutcome | None = None,
    ) -> Market:
        """A market in `status`, as the lifecycle would have left it, with nobody in it.

        * `created_by` is the proposer; left out, a new user is created for it. The
          proposer also stands in as the approving and resolving admin.
        * PROPOSED and REJECTED have no tier, budget or b. Every other status has the
          b of `tier` (or `b_micro`, which makes the tier CUSTOM), the budget
          ceil(b · ln 2) and a market account at balance 0.
        * `close_at` is now + `close_in`. A negative `close_in` gives an OPEN market
          past its deadline, which the API reports as CLOSED.
        * RESOLVED and VOID get their `resolutions` row; `outcome` picks YES or NO for
          RESOLVED (default YES). No credit moves, so use these only for a market
          without positions and settle a traded one through the lifecycle service.
        """
        if tier is MarketTier.CUSTOM and b_micro is None:
            raise ValueError("a CUSTOM market needs b_micro")
        creator = created_by or await self.user()
        now = self._clock.now()
        close_at = now + close_in
        market = Market(
            question=question,
            yes_def="It is on the menu that day.",
            no_def="It is not on the menu that day.",
            resolution_criteria="The menu published for that Friday decides.",
            resolution_source="ethz.ch/menu",
            status=status,
            close_at=close_at,
            resolve_by=close_at + DEFAULT_RESOLVE_AFTER_CLOSE,
            created_by=creator.id,
            created_at=now,
        )
        if status is MarketStatus.REJECTED:
            market.rejection_reason = "Rejected in a test."
        if status not in HIDDEN_STATUSES:
            pricing = self._settings.pricing
            market.tier = tier if b_micro is None else MarketTier.CUSTOM
            market.b_micro = pricing.tier_b_micro(tier) if b_micro is None else b_micro
            market.budget_micro = lmsr.max_loss_micro(
                market.b_micro, precision=pricing.decimal_precision
            )
            market.approved_by = creator.id
            market.opened_at = now
        if status is MarketStatus.CLOSED or status in SETTLED_STATUSES:
            market.closed_at = now

        async with self._uow_factory() as uow:
            await uow.markets.add(market)
            if status not in HIDDEN_STATUSES:
                await uow.accounts.create_market_account(market.id)
            if status in SETTLED_STATUSES:
                settled_as = (
                    ResolutionOutcome.VOID
                    if status is MarketStatus.VOID
                    else outcome or ResolutionOutcome.YES
                )
                await uow.resolutions.add(
                    Resolution(
                        market_id=market.id,
                        outcome=settled_as,
                        evidence="Settled in a test.",
                        resolved_by=creator.id,
                        created_at=now,
                    )
                )
        return market

    async def position(
        self,
        user: User,
        market: Market,
        outcome: Outcome,
        *,
        shares_micro: int,
        cost_micro: int | None = None,
    ) -> Position:
        """Let `user` buy `shares_micro` of `outcome`, as a committed trade would.

        Books a `TRADE_BUY` entry (user to market account), raises the position, adds
        the trade row and moves the market's quantity, `version` and volume together.
        `cost_micro` defaults to the LMSR cost of the shares; the user needs at least
        that balance. A test may charge more than that, never less: a market that sold
        shares below their cost could lose more than its budget, which is the fourth
        invariant the circuit breaker checks. No rule of the trading service is
        applied: no stake limit, no self-dealing check, no outbox row.

        Returns the position. `market` is brought up to the committed state in place.
        """
        if market.status not in UNSETTLED_STATUSES:
            raise ValueError(f"a {market.status} market cannot take a position")
        now = self._clock.now()
        precision = self._settings.pricing.decimal_precision
        async with self._uow_factory() as uow:
            row = await uow.markets.get_for_update(market.id)
            account = await uow.accounts.get_user_account(user.id, for_update=True)
            market_account = await uow.accounts.get_market_account(market.id)
            assert row is not None
            assert account is not None
            assert market_account is not None
            state = lmsr.MarketState(q_yes=row.q_yes_micro, q_no=row.q_no_micro, b=row.b_micro)
            quote = lmsr.quote_buy_shares(state, outcome, shares_micro, precision=precision)
            paid_micro = quote.amount_micro if cost_micro is None else cost_micro
            if paid_micro < quote.amount_micro:
                raise ValueError(
                    f"{shares_micro} µshares cost {quote.amount_micro} µCR here; a position "
                    f"sold for {paid_micro} µCR would break the market's loss bound"
                )

            trade_id = uuid7()
            await self._ledger.post(
                uow,
                kind=JournalKind.TRADE_BUY,
                ref_type=LedgerRef.TRADE,
                ref_id=trade_id,
                idempotency_key=trade_key(trade_id),
                lines=[
                    PostingLine(account.id, -paid_micro),
                    PostingLine(market_account.id, paid_micro),
                ],
                at=now,
            )
            position = await uow.positions.get(user.id, market.id, outcome, for_update=True)
            if position is None:
                position = await uow.positions.add(
                    Position(user_id=user.id, market_id=market.id, outcome=outcome)
                )
            position.shares_micro += shares_micro
            position.cost_micro += paid_micro
            row.q_yes_micro = quote.after.q_yes
            row.q_no_micro = quote.after.q_no
            row.version += 1
            row.volume_micro += paid_micro
            await uow.trades.add(
                Trade(
                    id=trade_id,
                    market_id=market.id,
                    user_id=user.id,
                    side=TradeSide.BUY,
                    outcome=outcome,
                    shares_micro=shares_micro,
                    amount_micro=paid_micro,
                    p_yes_before_micro=quote.p_yes_before_micro,
                    p_yes_after_micro=quote.p_yes_after_micro,
                    market_version=row.version,
                    created_at=now,
                )
            )
        market.q_yes_micro = row.q_yes_micro
        market.q_no_micro = row.q_no_micro
        market.version = row.version
        market.volume_micro = row.volume_micro
        return position

    async def balance_micro(self, user: User) -> int:
        """The user's current balance, read from Postgres."""
        async with self._uow_factory() as uow:
            account = await uow.accounts.get_user_account(user.id)
            assert account is not None
            return account.balance_micro

    async def accounts_total_micro(self) -> int:
        """Sum of every account's balance. Zero, or credit was created or destroyed."""
        async with self._uow_factory() as uow:
            return await uow.accounts.total_balance()
