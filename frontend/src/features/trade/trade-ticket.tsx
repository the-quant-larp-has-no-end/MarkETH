"use client";

import { useQuery } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { marketQuery, meQuery } from "@/lib/api/queries";
import type { MarketDetailResponse } from "@/lib/api/schema";

import type { TradeIntent } from "./intent";
import { TradeForm } from "./trade-form";
import { TradeReceipt } from "./trade-receipt";
import { useTicket } from "./use-ticket";

export type { TradeIntent } from "./intent";

export type TradeTicketProps = {
  /**
   * The market as `GET /markets/{market_id}` answers it. The ticket takes its presets,
   * the range of its slider, the balance and what may be sold from `market.viewer`.
   */
  market: MarketDetailResponse;
  /** What the ticket is set to. The owner keeps it, so that the address can follow it. */
  intent: TradeIntent;
  /**
   * The user changed the side or the outcome inside the ticket. `null` means the ticket
   * is finished: "Done" on the receipt was tapped. The owner closes the sheet, and the
   * next opening finds the form; in the sticky panel the form is back at once, and the
   * owner starts over on Buy and YES.
   */
  onIntentChange: (intent: TradeIntent | null) => void;
};

/**
 * The trade ticket: the content of the trade sheet (DESIGN.md §13). Market detail puts it
 * in a bottom sheet, a dialog or the sticky panel, hides the tab bar while a sheet is open
 * and reads the address; the ticket is the same in all three.
 *
 * It brings its own two parts, the `SheetBody` that scrolls and the `SheetFooter` that
 * keeps Confirm pinned, each with the 16 px padding of a sheet. Put it straight into a
 * `ResponsiveSheet` or onto the panel's surface, not inside another body or padding. The
 * header above it is the surface's: the question, and in a sheet the close cross. A sheet
 * cannot be closed while its trade is on its way, which the surface learns from
 * `useTradeOut`.
 *
 * What the ticket owns: the amount of a buy, the quote, the request with its
 * Idempotency-Key and its retry (DESIGN.md §14), the receipt, and reading the market and
 * the caller's balance again when it opens, so that the balance, the presets and the
 * slider are current. None of it is component state (`ticket-store.ts`): a trade without a
 * verdict, and whatever else the ticket shows, is still there when the ticket is mounted
 * afresh, as it is when a sheet turns into a dialog. While such a trade waits, the ticket
 * shows it, whatever `intent` says.
 */
export function TradeTicket({ market, intent, onIntentChange }: TradeTicketProps) {
  const { ticket, store } = useTicket(market.id);
  const frame = useRef<HTMLDivElement>(null);

  // DESIGN.md §14, "Live data": balances change between visits, so the market is read
  // again whenever the ticket opens. A query is stale at once; reading it here asks.
  const { refetch } = useQuery(marketQuery(market.id));
  const readMarketAgain = useCallback(() => void refetch({ cancelRefetch: false }), [refetch]);
  // The balance in the top bar is the same balance: it is read again with the market, so
  // that the two figures do not disagree while the ticket is open.
  useQuery(meQuery);

  const done = () => {
    // In a sheet or a dialog the owner closes it now. The receipt stays on it while it
    // slides away, and is forgotten once the ticket is gone: swapping in the form would
    // make a sheet jump to the form's height on its way out. Where the ticket stays in
    // place (the sticky panel) the form is back at once, and so is the focus.
    const stays = frame.current?.closest("[role=dialog]") == null;
    if (stays) store.done(market.id);
    onIntentChange(null);
    if (stays) frame.current?.focus({ preventScroll: true });
  };

  const shown = ticket.attempt?.intent ?? intent;

  return (
    <div
      ref={frame}
      tabIndex={-1}
      data-slot="trade-ticket"
      data-market-id={market.id}
      data-side={shown.side}
      data-outcome={shown.outcome}
      data-view={ticket.receipt === null ? "form" : "receipt"}
      className="flex min-h-0 flex-1 flex-col outline-none"
    >
      {ticket.receipt === null ? (
        <TradeForm
          market={market}
          intent={intent}
          ticket={ticket}
          store={store}
          onIntentChange={onIntentChange}
          onMarketBehind={readMarketAgain}
        />
      ) : (
        <TradeReceipt trade={ticket.receipt} onDone={done} />
      )}
    </div>
  );
}
