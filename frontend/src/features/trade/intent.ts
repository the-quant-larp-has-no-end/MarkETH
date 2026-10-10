import type { Outcome, TradeResponse, TradeSide } from "@/lib/api/schema";
import { formatShares } from "@/lib/format";

/** What the user is about to do in the ticket: buy or sell, and which outcome. */
export type TradeIntent = {
  side: TradeSide;
  outcome: Outcome;
};

/** YES for NO and NO for YES. */
export function otherOutcome(outcome: Outcome): Outcome {
  return outcome === "YES" ? "NO" : "YES";
}

/** The words on the Confirm button (DESIGN.md §13): "Buy YES", "Sell NO shares". */
export function confirmLabel({ side, outcome }: TradeIntent): string {
  return side === "BUY" ? `Buy ${outcome}` : `Sell ${outcome} shares`;
}

/**
 * The first line of a receipt (DESIGN.md §13): "Bought 183.29 YES shares". A receipt is a
 * detail view, so the shares are the whole string the API sent (DESIGN.md §10).
 */
export function receiptHeadline(trade: Pick<TradeResponse, "side" | "outcome" | "shares">): string {
  const verb = trade.side === "BUY" ? "Bought" : "Sold";
  return `${verb} ${formatShares(trade.shares, { outcome: trade.outcome, full: true })}`;
}
