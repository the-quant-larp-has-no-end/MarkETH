"""`GET /config` (Appendix A.2, A.11): the tunables the UI needs, so it reads no environment.

Everything here is public. Nothing in it is a secret or an address of a person.
"""

from app.core.enums import DegreeLevel, MarketTier
from app.schemas.common import CreditAmount, ResponseModel, ShareAmount


class TierConfig(ResponseModel):
    """A fixed liquidity tier of SPEC §4: what an admin picks from when approving a
    market. CUSTOM is not listed: it has no fixed b."""

    tier: MarketTier
    b_cr: CreditAmount
    max_loss_cr: CreditAmount
    """What VIS can lose at most in a market of this tier: b · ln 2, rounded up."""


class DegreeLevelOption(ResponseModel):
    """One level of the degree catalogue and the programmes a profile may name in it."""

    level: DegreeLevel
    name: str
    """How the level is shown, for example "Continuing education"."""

    programmes: list[str]
    """The labels of the level, in the order to offer them. A label is both what is
    shown and what `PUT /me/profile` takes as `degree_programme`."""


class PublicConfigResponse(ResponseModel):
    timezone: str
    """IANA name of the calendar behind monthly grants and daily limits."""

    signup_grant_cr: CreditAmount
    monthly_grant_cr: CreditAmount

    username_min_length: int
    username_max_length: int
    degrees: list[DegreeLevelOption]
    """Every level, in the order to offer them."""

    max_stake_per_market_cr: CreditAmount
    stake_presets_cr: list[CreditAmount]
    stake_step_cr: CreditAmount
    """The step of the slider of a buy on the trade sheet: a whole number of credits.
    Where the slider runs for one viewer of one market is `MarketViewer.buy_slider`."""

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

    question_min_length: int
    question_max_length: int
    """Characters a market's question may have, from the shortest to the longest."""

    short_text_max_length: int
    """The longest `yes_definition`, `no_definition` and `resolution_source` of a
    proposal, and the longest reason or note an admin gives when rejecting, halting,
    resuming or closing a market."""

    long_text_max_length: int
    """The longest `resolution_criteria` of a proposal, and the longest evidence or
    reason an admin gives when resolving or voiding a market."""

    code_length: int
    code_min_value_cr: CreditAmount
    code_max_value_cr: CreditAmount
    code_default_value_cr: CreditAmount
    max_code_issuance_per_month_cr: CreditAmount
    code_batch_max_count: int
    """The most codes one batch may have: the upper bound of `count` in
    `POST /admin/code-batches`."""

    code_label_max_length: int
    """Characters the label of a batch may have at most."""

    voucher_code_length: int
    voucher_expiry_days: int
    """An unused voucher is refunded this many days after it was issued. What a voucher
    can be for, and what each product takes, is `GET /vouchers/products`."""
