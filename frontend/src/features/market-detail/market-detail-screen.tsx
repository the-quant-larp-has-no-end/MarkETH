"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

export type MarketDetailScreenProps = {
  /** The `[id]` of the route `/markets/[id]`. */
  marketId: string;
};

/**
 * Market detail (`/markets/[id]`): DESIGN.md §13. A stand-in until the screen is built:
 * the name and the props are final, the content is not.
 *
 * This screen owns the trade ticket's surroundings. It decides the `TradeIntent` (from
 * an outcome button, from "Sell", or from `?buy=YES`, `?buy=NO`, `?sell=YES`,
 * `?sell=NO`), and shows `TradeTicket` of `@/features/trade` in a `ResponsiveSheet`
 * below 1024 px and in the sticky panel from there.
 */
export function MarketDetailScreen({ marketId }: MarketDetailScreenProps) {
  return (
    <Screen top="inner">
      <h1 className="sr-only">Market</h1>
      <EmptyState>This screen is not built yet.</EmptyState>
      <span hidden data-market-id={marketId} />
    </Screen>
  );
}
