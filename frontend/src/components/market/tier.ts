import type { MarketTier } from "@/lib/api/schema";

/**
 * `tier` is a key, not copy: these are its words (DESIGN.md §13). A proposal asks for one
 * of the three fixed tiers; "Custom" is for VIS to choose when it approves a market.
 */
const NAMES: Record<MarketTier, string> = {
  TIGHT: "Tight",
  DEFAULT: "Default",
  MEDIUM: "Medium",
  CUSTOM: "Custom",
};

/** One line that tells a proposer what a tier does to the price. */
const EXPLANATIONS: Partial<Record<MarketTier, string>> = {
  TIGHT: "The price moves fast. For a question few people will trade.",
  DEFAULT: "Right for most questions.",
  MEDIUM: "The price moves slowly. For a question many people will trade.",
};

export function tierName(tier: MarketTier): string {
  return NAMES[tier];
}

export function tierExplanation(tier: MarketTier): string | undefined {
  return EXPLANATIONS[tier];
}
