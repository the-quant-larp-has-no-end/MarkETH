"""The economy of the test settings, as numbers a test module can import.

Everything here follows from `.example.env` (`tests/support/settings.py`) and from
`app.pricing.lmsr`; nothing is typed in. A test that only needs these at run time reads
them from the `base_settings` fixture instead.
"""

from collections.abc import Mapping
from typing import Final

from app.core.enums import MarketTier
from app.pricing import lmsr
from app.services.public_config import FIXED_TIERS
from tests.support.settings import load_base_settings

_PRICING = load_base_settings().pricing
_RISK = load_base_settings().risk

PRECISION: Final[int] = _PRICING.decimal_precision
"""PRICING_DECIMAL_PRECISION: the digits the application prices with."""

TIER_B: Final[Mapping[MarketTier, int]] = {
    tier: _PRICING.tier_b_micro(tier) for tier in FIXED_TIERS
}
"""b of each fixed tier in µCR: LMSR_TIER_TIGHT_B, LMSR_TIER_DEFAULT_B, LMSR_TIER_MEDIUM_B."""

DEFAULT_B: Final[int] = TIER_B[MarketTier.DEFAULT]

MAX_MARKET_BUDGET: Final[int] = _RISK.max_market_budget_micro
"""MAX_MARKET_BUDGET in µCR: the largest budget a market can be approved with."""

MAX_B: Final[int] = lmsr.b_from_budget_micro(MAX_MARKET_BUDGET, precision=PRECISION)
"""The largest b a market can have, in µCR: the b of a budget of MAX_MARKET_BUDGET."""

MARKET_B: Final[tuple[int, ...]] = (*TIER_B.values(), MAX_B)
"""The values of b worth trying by name: the three tiers and the largest."""

STAKE_LIMIT: Final[int] = _PRICING.max_stake_per_user_market_micro
"""MAX_STAKE_PER_USER_MARKET in µCR: the most a user may have at stake in one market."""

MIN_TRADE_SHARES: Final[int] = _PRICING.min_trade_shares_micro
"""MIN_TRADE_SHARES in µshares: the smallest trade that is sized by shares."""
