import type { MarketTier, PublicConfigResponse } from "@/lib/api/schema";

/** The tiers a proposal may ask for: those `/config` lists, in its order. */
export function proposableTiers(config: PublicConfigResponse): MarketTier[] {
  return config.tiers.map((entry) => entry.tier).filter((tier) => tier !== "CUSTOM");
}

/**
 * The tier a new proposal starts on: DEFAULT, which is "right for most questions", when
 * `/config` offers it. Otherwise none, and the proposer has to choose.
 */
export function startingTier(config: PublicConfigResponse): MarketTier | "" {
  return proposableTiers(config).includes("DEFAULT") ? "DEFAULT" : "";
}
