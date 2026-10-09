"""Domain vocabulary shared by models, schemas and services.

These are plain string enums with no framework imports, so every layer may use them.
Values are stored verbatim in VARCHAR columns and appear verbatim in the API.
"""

from enum import StrEnum


class UserRole(StrEnum):
    USER = "USER"
    ADMIN = "ADMIN"
    BARISTA = "BARISTA"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


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
    COFFEE_TICKET_ISSUE = "COFFEE_TICKET_ISSUE"
    COFFEE_TICKET_REDEEM = "COFFEE_TICKET_REDEEM"
    COFFEE_TICKET_REFUND = "COFFEE_TICKET_REFUND"


class VoucherCodeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REDEEMED = "REDEEMED"
    REVOKED = "REVOKED"


class CoffeeTicketStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REDEEMED = "REDEEMED"
    EXPIRED = "EXPIRED"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class OutboxTopic(StrEnum):
    QUOTE_UPDATED = "quote.updated"


SIGNUP_GRANT_PERIOD = "SIGNUP"
"""`voucher_grants.period` value of the one-time signup grant (SPEC §2)."""
