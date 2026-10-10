"""Domain vocabulary shared by models, schemas and services.

These are plain string enums with no framework imports, so every layer may use them.
Values appear verbatim in the API, and those a table keeps are stored verbatim in a
VARCHAR column.
"""

from enum import StrEnum


class UserRole(StrEnum):
    """What a caller may do. Derived from `ADMIN_EMAILS` and `BARISTA_EMAILS` on every
    request and stored nowhere (Appendix A.3)."""

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
    """The seven market states of SPEC §6."""

    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    HALTED = "HALTED"
    CLOSED = "CLOSED"
    RESOLVED = "RESOLVED"
    VOID = "VOID"


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


class LeaderboardSort(StrEnum):
    """The measure a leaderboard is ordered by (`GET /leaderboard`, Appendix A.2)."""

    CREDITS = "credits"
    """The balance of the user's account."""

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
