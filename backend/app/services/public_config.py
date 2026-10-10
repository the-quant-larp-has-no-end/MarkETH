"""`GET /config`: the tunables the UI needs (Appendix A.2, A.11).

The frontend reads no environment. Whatever it has to know about limits, presets and
timings comes from here, straight out of `Settings`. Two things that are no tunables
travel with it, because a form needs them before anybody types: the degree catalogue,
and how long the texts of a market may be, which the request models fix.
"""

from typing import Final

from app.core.config import Settings
from app.core.degrees import CATALOGUE
from app.core.enums import MarketTier
from app.core.units import to_cr
from app.pricing import lmsr
from app.schemas.config import DegreeLevelOption, PublicConfigResponse, TierConfig
from app.schemas.markets import (
    LONG_TEXT_MAX_LENGTH,
    QUESTION_MAX_LENGTH,
    QUESTION_MIN_LENGTH,
    SHORT_TEXT_MAX_LENGTH,
)

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
        identity, pricing, grants, codes, vouchers = (
            settings.identity,
            settings.pricing,
            settings.grants,
            settings.codes,
            settings.vouchers,
        )
        degrees = [
            DegreeLevelOption(level=entry.level, name=entry.name, programmes=list(entry.programmes))
            for entry in CATALOGUE
        ]
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
            signup_grant_cr=to_cr(grants.signup_grant_micro),
            monthly_grant_cr=to_cr(grants.monthly_grant_micro),
            username_min_length=identity.username_min_length,
            username_max_length=identity.username_max_length,
            degrees=degrees,
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
            question_min_length=QUESTION_MIN_LENGTH,
            question_max_length=QUESTION_MAX_LENGTH,
            short_text_max_length=SHORT_TEXT_MAX_LENGTH,
            long_text_max_length=LONG_TEXT_MAX_LENGTH,
            code_length=codes.length,
            code_min_value_cr=to_cr(codes.min_value_micro),
            code_max_value_cr=to_cr(codes.max_value_micro),
            code_default_value_cr=to_cr(codes.default_value_micro),
            max_code_issuance_per_month_cr=to_cr(codes.max_issuance_per_month_micro),
            voucher_code_length=vouchers.code_length,
            voucher_expiry_days=vouchers.expiry_days,
        )
