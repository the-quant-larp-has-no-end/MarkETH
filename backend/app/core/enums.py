"""Domain vocabulary shared by models, schemas and services.

These are plain string enums with no framework imports, so every layer may use them.
Values appear verbatim in the API, and those a table keeps are stored verbatim in a
VARCHAR column.
"""

from enum import StrEnum
from typing import Final


class UserRole(StrEnum):
    """What a caller may do. `users.role` keeps it: USER when a profile is created,
    and whatever an admin sets after that. An address of `ADMIN_EMAILS` is ADMIN
    whatever its row says (Appendix A.3)."""

    USER = "USER"
    ADMIN = "ADMIN"
    BARISTA = "BARISTA"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class DegreeLevel(StrEnum):
    """The levels of the degree catalogue (`app.core.degrees`), in the order they are
    offered. OTHER is for ETH members who are in no programme."""

    BACHELOR = "BACHELOR"
    MASTER = "MASTER"
    DOCTORATE = "DOCTORATE"
    CONTINUING_EDUCATION = "CONTINUING_EDUCATION"
    TEACHER_EDUCATION = "TEACHER_EDUCATION"
    OTHER = "OTHER"


class MarketStatus(StrEnum):
    """What a market is reported as: one of the seven states of SPEC §6, which its
    row keeps, or EXPIRED, which is worked out from the clock and never kept: a
    proposal whose `close_at` passed before an admin decided on it."""

    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    HALTED = "HALTED"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    VOID = "VOID"
    EXPIRED = "EXPIRED"
    """A proposal whose `close_at` passed before an admin decided on it
    (`MarketView.effective_status`). Its row says PROPOSED and stays that way, and
    nobody can approve or reject it."""


STORED_MARKET_STATUSES: Final[tuple[MarketStatus, ...]] = (
    MarketStatus.PROPOSED,
    MarketStatus.REJECTED,
    MarketStatus.OPEN,
    MarketStatus.HALTED,
    MarketStatus.CLOSED,
    MarketStatus.RESOLVED,
    MarketStatus.VOID,
)
"""The statuses `markets.status` holds, in the order its CHECK lists them. The column
is built from this tuple and from nothing else, so a status that is not here can be
neither written nor asked for: binding it to a statement fails, and the database's
CHECK refuses it from anybody who goes around the application."""


class MarketTier(StrEnum):
    """Liquidity tiers of SPEC §4. CUSTOM derives b from an explicit budget."""

    TIGHT = "TIGHT"
    DEFAULT = "DEFAULT"
    MEDIUM = "MEDIUM"
    CUSTOM = "CUSTOM"


class Outcome(StrEnum):
    YES = "YES"
    NO = "NO"

    @property
    def opposite(self) -> Outcome:
        return Outcome.NO if self is Outcome.YES else Outcome.YES


class ResolutionOutcome(StrEnum):
    YES = "YES"
    NO = "NO"
    VOID = "VOID"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class AccountKind(StrEnum):
    """Ledger accounts of SPEC §7. The VIS_* kinds are singletons."""

    USER = "USER"
    MARKET = "MARKET"
    VIS_ISSUANCE = "VIS_ISSUANCE"
    VIS_MM_PNL = "VIS_MM_PNL"
    VIS_REDEMPTION_PENDING = "VIS_REDEMPTION_PENDING"
    VIS_REDEEMED = "VIS_REDEEMED"


SYSTEM_ACCOUNT_KINDS: tuple[AccountKind, ...] = (
    AccountKind.VIS_ISSUANCE,
    AccountKind.VIS_MM_PNL,
    AccountKind.VIS_REDEMPTION_PENDING,
    AccountKind.VIS_REDEEMED,
)


class JournalKind(StrEnum):
    """Why a journal entry exists. One value per way credits can move."""

    SIGNUP_GRANT = "SIGNUP_GRANT"
    MONTHLY_GRANT = "MONTHLY_GRANT"
    CODE_REDEMPTION = "CODE_REDEMPTION"
    TRADE_BUY = "TRADE_BUY"
    TRADE_SELL = "TRADE_SELL"
    MARKET_RESOLUTION = "MARKET_RESOLUTION"
    MARKET_VOID = "MARKET_VOID"
    VOUCHER_ISSUE = "VOUCHER_ISSUE"
    VOUCHER_REDEEM = "VOUCHER_REDEEM"
    VOUCHER_REFUND = "VOUCHER_REFUND"
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"
    STREAK_REWARD = "STREAK_REWARD"
    """The tokens of one counted day of a streak, from `vis:issuance` to the user."""

    STREAK_BONUS = "STREAK_BONUS"
    """The bonus of a milestone a streak reached, from `vis:issuance` to the user."""


class CodeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REDEEMED = "REDEEMED"
    REVOKED = "REVOKED"


class VoucherStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REDEEMED = "REDEEMED"
    EXPIRED = "EXPIRED"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OutboxTopic(StrEnum):
    QUOTE_UPDATED = "quote.updated"


class NoticeKind(StrEnum):
    """What a notice tells its user (`notices.kind`)."""

    PROPOSAL_APPROVED = "PROPOSAL_APPROVED"
    """An admin approved a market the user proposed: it is open."""


class BroadcastStatus(StrEnum):
    """Where a broadcast stands (`broadcasts.status`). A row holds every one of the five.

    A proposer's broadcast is REQUESTED until an admin approves or refuses it; an
    admin's own starts LIVE. REQUESTED, APPROVED and LIVE are open: a market has at
    most one open broadcast."""

    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REFUSED = "REFUSED"
    LIVE = "LIVE"
    ENDED = "ENDED"


class BroadcastEndReason(StrEnum):
    """Why a broadcast ended (`broadcasts.end_reason`)."""

    BROADCASTER = "BROADCASTER"
    """Its broadcaster ended it."""

    ADMIN = "ADMIN"
    """An admin ended it."""

    STALLED = "STALLED"
    """No piece arrived for `BROADCAST_STALL_SECONDS`."""

    TIME_LIMIT = "TIME_LIMIT"
    """It ran for `BROADCAST_MAX_MINUTES`."""


class LeaderboardSort(StrEnum):
    """The measure a leaderboard is ordered by (`GET /leaderboard`, Appendix A.2)."""

    CREDITS = "credits"
    """What the user has: the balance of their account plus the value of their open
    positions."""

    WIN_RATE = "win_rate"
    """The share of the user's resolved markets that returned more than was put in."""

    TRADES = "trades"
    """The number of trades the user made."""

    AVG_PAYOUT_RATIO = "avg_payout_ratio"
    """Credits returned per credit put in, over the user's resolved markets."""


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


SIGNUP_GRANT_PERIOD = "SIGNUP"
"""`grants.period` value of the one-time signup grant (SPEC §2)."""
