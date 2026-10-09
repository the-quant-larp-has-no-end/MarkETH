"""Test data written straight to the database, with the books kept straight.

`Factory` puts users, markets and positions in place through the repositories and
`LedgerService`, without going through the services under test. Whatever it writes
leaves every invariant intact: entries balance, all accounts sum to zero, a market's
quantities equal the sum of its positions, no user balance is negative.

    user = await factory.user(balance_micro=5 * MICRO)
    market = await factory.market()                       # OPEN, default tier
    await factory.position(user, market, Outcome.YES, shares_micro=MICRO)
    as_user(user)

Rows come back detached and readable. Time is the container's clock.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Final
from uuid import uuid7

from app.container import Container
from app.core.enums import (
    SIGNUP_GRANT_PERIOD,
    AccountKind,
    JournalKind,
    MarketStatus,
    MarketTier,
    Outcome,
    ResolutionOutcome,
    TradeSide,
    UserRole,
    UserStatus,
)
from app.core.identity import AuthenticatedUser
from app.models import Market, Position, Resolution, Trade, User
from app.pricing import lmsr
from app.services.ledger import LedgerRef, PostingLine, signup_grant_key, trade_key
from app.services.market_view import HIDDEN_STATUSES, SETTLED_STATUSES, UNSETTLED_STATUSES

DEFAULT_CLOSE_IN: Final[timedelta] = timedelta(days=7)
DEFAULT_RESOLVE_AFTER_CLOSE: Final[timedelta] = timedelta(days=7)
DEFAULT_QUESTION: Final[str] = "Will the Polymensa serve Älplermagronen on Friday?"

type ActAs = Callable[[AuthenticatedUser | User | None], None]
"""The `as_user` fixture: pass a user (or its identity) to act as, None to be anonymous."""


def identity_of(user: User) -> AuthenticatedUser:
    """The `AuthenticatedUser` a session of `user` would resolve to."""
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        canonical_email=user.canonical_email,
        role=user.role,
        status=user.status,
    )


class Factory:
    def __init__(self, container: Container) -> None:
        self._uow_factory = container.uow_factory
        self._ledger = container.ledger
        self._clock = container.clock
        self._settings = container.settings
        self._users_made = 0

    async def user(
        self,
        *,
        role: UserRole = UserRole.USER,
        balance_micro: int = 0,
        email: str | None = None,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> User:
        """A verified user with their ledger account, created now.

        The user counts as having received the signup grant: the `voucher_grants` row
        exists, and a positive `balance_micro` is booked as that grant from
        `vis:issuance`. Without `email`, addresses are `user1@…`, `user2@…` on the
        first allowed domain; the canonical address swaps in the canonical domain.
        """
        auth = self._settings.auth
        self._users_made += 1
        address = email or f"user{self._users_made}@{auth.allowed_email_domains[0]}"
        canonical = f"{address.partition('@')[0]}@{auth.canonical_email_domain}"
        now = self._clock.now()
        async with self._uow_factory() as uow:
            user = await uow.users.add(
                User(
                    canonical_email=canonical,
                    email=address,
                    role=role,
                    status=status,
                    created_at=now,
                )
            )
            account = await uow.accounts.create_user_account(user.id)
            grant = await uow.voucher_grants.insert_if_absent(user.id, SIGNUP_GRANT_PERIOD, now)
            assert grant is not None
            if balance_micro:
                issuance = await uow.accounts.get_system_account(AccountKind.VIS_ISSUANCE)
                entry = await self._ledger.post(
                    uow,
                    kind=JournalKind.SIGNUP_GRANT,
                    ref_type=LedgerRef.VOUCHER_GRANT,
                    ref_id=grant.id,
                    idempotency_key=signup_grant_key(user.id),
                    lines=[
                        PostingLine(issuance.id, -balance_micro),
                        PostingLine(account.id, balance_micro),
                    ],
                    at=now,
                )
                await uow.voucher_grants.set_entry(grant.id, entry.id)
        return user

    def identity(self, user: User) -> AuthenticatedUser:
        return identity_of(user)

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
            requested_tier=MarketTier.DEFAULT if tier is MarketTier.CUSTOM else tier,
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
        that balance. No rule of the trading service is applied: no stake limit, no
        self-dealing check, no outbox row.

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
            if paid_micro <= 0:
                raise ValueError("a position must cost something; cost_micro must be positive")

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
