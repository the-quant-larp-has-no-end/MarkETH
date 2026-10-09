"""`GET /config`: the tunables the UI needs (Appendix A.2, A.11).

The frontend reads no environment. Whatever it has to know about limits, presets and
timings comes from here, straight out of `Settings`.
"""

from typing import Final

from app.core.config import Settings
from app.core.enums import MarketTier
from app.core.units import to_cr
from app.pricing import lmsr
from app.schemas.config import PublicConfigResponse, TierConfig

FIXED_TIERS: Final[tuple[MarketTier, ...]] = (
    MarketTier.TIGHT,
    MarketTier.DEFAULT,
    MarketTier.MEDIUM,
)
"""The tiers whose b comes from `.env`. CUSTOM takes a budget instead (A.5)."""


class PublicConfigService:
    def __init__(self, settings: Settings) -> None:
        # Settings never change while the process runs, so neither does the answer.
        self._response = self._build(settings)

    def get(self) -> PublicConfigResponse:
        return self._response

    @staticmethod
    def _build(settings: Settings) -> PublicConfigResponse:
        pricing, vouchers = settings.pricing, settings.vouchers
        tiers = [
            TierConfig(
                tier=tier,
                b_cr=to_cr(pricing.tier_b_micro(tier)),
                max_loss_cr=to_cr(
                    lmsr.max_loss_micro(
                        pricing.tier_b_micro(tier), precision=pricing.decimal_precision
                    )
                ),
            )
            for tier in FIXED_TIERS
        ]
        return PublicConfigResponse(
            timezone=settings.app.timezone_name,
            allowed_email_domains=list(settings.auth.allowed_email_domains),
            login_link_ttl_minutes=settings.auth.login_token_ttl_minutes,
            signup_grant_cr=to_cr(vouchers.signup_grant_micro),
            monthly_grant_cr=to_cr(vouchers.monthly_grant_micro),
            max_stake_per_market_cr=to_cr(pricing.max_stake_per_user_market_micro),
            stake_presets_cr=[to_cr(preset) for preset in pricing.stake_presets_micro],
            min_trade_cr=to_cr(pricing.min_trade_micro),
            min_trade_shares=to_cr(pricing.min_trade_shares_micro),
            price_display_decimals=pricing.price_display_decimals,
            quote_debounce_ms=settings.ui.quote_debounce_ms,
            live_poll_fallback_seconds=settings.ui.live_poll_fallback_seconds,
            tiers=tiers,
            max_market_budget_cr=to_cr(settings.risk.max_market_budget_micro),
            max_market_lifetime_days=settings.risk.max_market_lifetime_days,
            max_resolution_delay_days=settings.lifecycle.max_resolution_delay_days,
            max_proposals_per_day=settings.lifecycle.max_proposals_per_user_per_day,
            voucher_code_length=vouchers.code_length,
            voucher_code_min_value_cr=to_cr(vouchers.code_min_value_micro),
            voucher_code_max_value_cr=to_cr(vouchers.code_max_value_micro),
            voucher_code_default_value_cr=to_cr(vouchers.code_default_value_micro),
            max_code_issuance_per_month_cr=to_cr(vouchers.max_code_issuance_per_month_micro),
            coffee_ticket_value_cr=to_cr(vouchers.coffee_ticket_value_micro),
            coffee_ticket_code_length=vouchers.coffee_ticket_code_length,
            coffee_ticket_expiry_days=vouchers.coffee_ticket_expiry_days,
        )
