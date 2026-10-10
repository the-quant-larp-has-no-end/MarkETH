import type { MarketTier } from "@/lib/api/schema";

/**
 * `tier` is a key, not copy: these are its words (DESIGN.md §13). They are for the admin
 * area alone: how a market is priced is VIS's business, and no screen for players names a
 * tier (DESIGN.md §10). "Custom" is the tier of a market whose budget an admin set by hand.
 */
const NAMES: Record<MarketTier, string> = {
  TIGHT: "Tight",
  DEFAULT: "Default",
  MEDIUM: "Medium",
  CUSTOM: "Custom",
};

export function tierName(tier: MarketTier): string {
  return NAMES[tier];
}
