"use client";

import { EmptyState } from "@/components/feedback";
import type { MarketDetailResponse, Outcome, TradeSide } from "@/lib/api/schema";

/** What the user is about to do in the ticket: buy or sell, and which outcome. */
export type TradeIntent = {
  side: TradeSide;
  outcome: Outcome;
};

export type TradeTicketProps = {
  /**
   * The market as `GET /markets/{market_id}` answers it. The ticket takes its presets,
   * the balance and what may be sold from `market.viewer`.
   */
  market: MarketDetailResponse;
  /** What the ticket is set to. The owner keeps it, so that the address can follow it. */
  intent: TradeIntent;
  /**
   * The user changed the side or the outcome inside the ticket. `null` means the ticket
   * is finished: "Done" on the receipt was tapped, the ticket is back on its form, and
   * the owner closes the sheet (or, in the sticky panel, starts over on Buy and YES).
   */
  onIntentChange: (intent: TradeIntent | null) => void;
};

/**
 * The trade ticket: the content of the trade sheet (DESIGN.md §13). A stand-in until it
 * is built: the name and the props are final, the content is not.
 *
 * It is content only. Market detail puts it in a bottom sheet, a dialog or the sticky
 * panel, hides the tab bar while a sheet is open and reads the address; the ticket is
 * the same in all three. What the ticket owns: the quote, the request with its
 * Idempotency-Key and its retry (DESIGN.md §14), the receipt, and refetching the market
 * when it opens so that the balance and the presets are current.
 */
export function TradeTicket({ market, intent }: TradeTicketProps) {
  return (
    <div data-slot="trade-ticket" data-market-id={market.id} data-side={intent.side}>
      <EmptyState>This ticket is not built yet.</EmptyState>
    </div>
  );
}
