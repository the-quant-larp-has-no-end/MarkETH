"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";

import type { TradeIntent } from "@/features/trade";

/**
 * The trade a market's address asks for (DESIGN.md §11): `?buy=YES`, `?buy=NO`,
 * `?sell=YES` or `?sell=NO`. Anything else asks for none.
 */
export function readIntent(search: URLSearchParams): TradeIntent | null {
  const buy = search.get("buy");
  if (buy === "YES" || buy === "NO") return { side: "BUY", outcome: buy };
  const sell = search.get("sell");
  if (sell === "YES" || sell === "NO") return { side: "SELL", outcome: sell };
  return null;
}

/** The address of the same market with `intent` in it, or with none. */
export function intentHref(
  pathname: string,
  search: URLSearchParams,
  intent: TradeIntent | null,
): string {
  const next = new URLSearchParams(search);
  next.delete("buy");
  next.delete("sell");
  if (intent !== null) next.set(intent.side === "BUY" ? "buy" : "sell", intent.outcome);
  const query = next.toString();
  return query === "" ? pathname : `${pathname}?${query}`;
}

export function sameIntent(a: TradeIntent, b: TradeIntent): boolean {
  return a.side === b.side && a.outcome === b.outcome;
}

export type TradeAddress = {
  /** What the address asks for right now. */
  requested: TradeIntent | null;
  /**
   * A tap in the page asks for a trade. This is a step in the history, so the phone's
   * Back closes the sheet again and leaves the market on screen.
   */
  open(intent: TradeIntent): void;
  /**
   * The address follows what the ticket is set to. No step in the history: there is
   * nothing to go back to.
   */
  show(intent: TradeIntent): void;
  /**
   * The request is over: the sheet was closed, the ticket is done, or the request cannot
   * be granted. A step that `open` made is taken back; a request the address arrived
   * with is taken out of it, so that the way back still leads where it led.
   */
  close(): void;
};

/**
 * Reads the trade intent from the address of a market and writes it back.
 *
 * It is written with the browser's own History API, which the router follows
 * (`useSearchParams` sees the change) without asking the server for the route again: a
 * sheet opens at once, also on a slow connection.
 */
export function useTradeAddress(): TradeAddress {
  const pathname = usePathname();
  const router = useRouter();
  const query = useSearchParams().toString();
  const requested = useMemo(() => readIntent(new URLSearchParams(query)), [query]);
  /** True while the entry the page is on was made by `open`. */
  const stepped = useRef(false);

  // Back took the step away: the next close has nothing to take back.
  useEffect(() => {
    if (requested === null) stepped.current = false;
  }, [requested]);

  const href = (intent: TradeIntent | null) =>
    intentHref(pathname, new URLSearchParams(query), intent);

  return {
    requested,
    open(intent) {
      // Asking again from an open sheet changes it; it does not stack a second step.
      if (requested === null) {
        stepped.current = true;
        window.history.pushState(null, "", href(intent));
      } else {
        window.history.replaceState(null, "", href(intent));
      }
    },
    show(intent) {
      window.history.replaceState(null, "", href(intent));
    },
    close() {
      if (requested === null) return;
      if (stepped.current) {
        stepped.current = false;
        router.back();
      } else {
        window.history.replaceState(null, "", href(null));
      }
    },
  };
}
