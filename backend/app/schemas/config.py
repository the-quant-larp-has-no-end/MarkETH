"""`GET /config` (Appendix A.2, A.11): the tunables the UI needs, so it reads no environment.

Everything here is public. Nothing in it is a secret or an address of a person.
"""

from app.core.enums import MarketTier
from app.schemas.common import CreditAmount, ResponseModel, ShareAmount


class TierConfig(ResponseModel):
    """A fixed liquidity tier of SPEC §4. CUSTOM is not listed: it has no fixed b."""

    tier: MarketTier
    b_cr: CreditAmount
    max_loss_cr: CreditAmount
    """What VIS can lose at most in a market of this tier: b · ln 2, rounded up."""


class PublicConfigResponse(ResponseModel):
    timezone: str
    """IANA name of the calendar behind monthly grants and daily limits."""

    allowed_email_domains: list[str]
    login_link_ttl_minutes: int

    signup_grant_cr: CreditAmount
    monthly_grant_cr: CreditAmount

    max_stake_per_market_cr: CreditAmount
    stake_presets_cr: list[CreditAmount]
    min_trade_cr: CreditAmount
    min_trade_shares: ShareAmount
    price_display_decimals: int
    """Decimals of a displayed percentage: 1 means 54.8%."""

    quote_debounce_ms: int
    live_poll_fallback_seconds: int

    tiers: list[TierConfig]
    max_market_budget_cr: CreditAmount
    """Upper bound of the budget an admin may give a CUSTOM market."""

    max_market_lifetime_days: int
    max_resolution_delay_days: int
    max_proposals_per_day: int

    voucher_code_length: int
    voucher_code_min_value_cr: CreditAmount
    voucher_code_max_value_cr: CreditAmount
    voucher_code_default_value_cr: CreditAmount
    max_code_issuance_per_month_cr: CreditAmount

    coffee_ticket_value_cr: CreditAmount
    coffee_ticket_code_length: int
    coffee_ticket_expiry_days: int
