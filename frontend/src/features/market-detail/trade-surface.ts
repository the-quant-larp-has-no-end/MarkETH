"use client";

import { useEffect, useEffectEvent, useState } from "react";

import type { TradeIntent } from "@/features/trade";
import type { MarketDetailResponse, MarketViewer, Outcome } from "@/lib/api/schema";

import { sameIntent, useTradeAddress } from "./trade-address";

/** DESIGN.md §13: the sticky panel starts on Buy and YES. */
export const START: TradeIntent = { side: "BUY", outcome: "YES" };

/**
 * Whether the viewer can sell: at least one entry of `sell_presets` has `allowed: true`,
 * of `outcome` when one is named. `allowed` is the server's answer; nothing is compared
 * here.
 */
export function canSell(viewer: MarketViewer, outcome?: Outcome): boolean {
  return viewer.sell_presets.some(
    (preset) => preset.allowed && (outcome === undefined || preset.outcome === outcome),
  );
}

/**
 * What a request of the address leads to (DESIGN.md §13, Trade sheet): the trade to show,
 * or null for a trade the viewer cannot make, which opens the market without the sheet.
 *
 * A buy needs `viewer.can_trade`. A sell needs a sell preset that is allowed: of the
 * outcome that was asked for, or else of the other one, which is then shown instead.
 * Where the sticky panel is in place there is no sheet to keep shut, so a buy is shown
 * there whatever `can_trade` says, and the ticket shows that nothing can be chosen.
 */
export function grant(
  viewer: MarketViewer,
  requested: TradeIntent,
  panel: boolean,
): TradeIntent | null {
  if (requested.side === "BUY") return panel || viewer.can_trade ? requested : null;
  if (canSell(viewer, requested.outcome)) return requested;
  const other: Outcome = requested.outcome === "YES" ? "NO" : "YES";
  return canSell(viewer, other) ? { side: "SELL", outcome: other } : null;
}

export type TradeSurface = {
  /** The trade sheet is open. Never from 1024 px, where the ticket is the sticky panel. */
  sheetOpen: boolean;
  /** What the ticket in the sheet is set to. It keeps its last value while the sheet closes. */
  sheetIntent: TradeIntent;
  /** What the ticket in the sticky panel is set to. */
  panelIntent: TradeIntent;
  /** An outcome button was tapped. */
  buy(outcome: Outcome): void;
  /** "Sell" was tapped: the ticket opens on the first outcome that can be sold. */
  sell(): void;
  /** The ticket's `onIntentChange`: another side or outcome, or null when it is done. */
  change(intent: TradeIntent | null): void;
  /** The sheet was dismissed. */
  close(): void;
};

/**
 * Where the trade ticket stands and what it is set to, with the address as its one
 * source (DESIGN.md §11, §13): below 1024 px a sheet that is open while the address asks
 * for a trade, from 1024 px the sticky panel, which follows the address and starts on Buy
 * and YES.
 *
 * A request is looked at once, when it appears: one the viewer cannot make is taken out
 * of the address, so that it cannot open a sheet by itself later. A sheet that is open
 * stays open when the market changes behind it (a halt, a sale of the last shares): the
 * ticket has something to say then, and only the user or the ticket closes it.
 *
 * `market` is undefined until it has been read; `panel` says that the sticky panel is in
 * place.
 */
export function useTradeSurface(
  market: MarketDetailResponse | undefined,
  { desktop, panel }: { desktop: boolean; panel: boolean },
): TradeSurface {
  const address = useTradeAddress();
  const { requested } = address;
  const [admitted, setAdmitted] = useState(false);
  const [last, setLast] = useState<TradeIntent>(START);

  const granted =
    market === undefined || requested === null ? null : grant(market.viewer, requested, panel);
  const asAsked = requested !== null && granted !== null && sameIntent(granted, requested);

  // The request is let in once and then stays, until the address drops it.
  if (requested === null) {
    if (admitted) setAdmitted(false);
  } else if (asAsked && !admitted) {
    setAdmitted(true);
  }
  const current = requested !== null && admitted ? requested : null;
  if (current !== null && !sameIntent(current, last)) setLast(current);

  // A request that is not let in as it stands: show the trade it leads to, or drop it.
  const refused = market !== undefined && requested !== null && !admitted && !asAsked;
  const settle = useEffectEvent(() => {
    if (granted === null) address.close();
    else address.show(granted);
  });
  useEffect(() => {
    if (refused) settle();
  }, [refused]);

  const ask = (intent: TradeIntent) => {
    // The panel is always there: choosing in it is no step to go back from.
    if (desktop) address.show(intent);
    else address.open(intent);
  };

  return {
    sheetOpen: !desktop && current !== null,
    sheetIntent: current ?? last,
    panelIntent: current ?? START,
    buy: (outcome) => ask({ side: "BUY", outcome }),
    sell: () => {
      if (market === undefined) return;
      ask({ side: "SELL", outcome: canSell(market.viewer, "YES") ? "YES" : "NO" });
    },
    change: (intent) => {
      if (intent === null) address.close();
      else address.show(intent);
    },
    close: () => address.close(),
  };
}
