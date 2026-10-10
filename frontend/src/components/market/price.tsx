"use client";

import { Flash } from "@/components/feedback";
import type { MarketSummary } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { PRICE_ANNOUNCE_MS } from "@/lib/design";
import { formatPercent, spokenPercent } from "@/lib/format";
import { useThrottledValue } from "@/lib/hooks";
import { cn } from "@/lib/utils";

export type PriceProps = {
  /** A market as the API sends it: a summary, a detail, or a portfolio entry's `market`. */
  market: Pick<MarketSummary, "p_yes" | "status" | "outcome">;
  /** `card`: `number-lg` with "chance" under it. `detail`: `display` with "chance" beside it. */
  size?: "card" | "detail";
  /**
   * The price follows the stream: changes are announced to a screen reader, at most once
   * per 10 s (DESIGN.md §15).
   */
  live?: boolean;
  className?: string;
};

/**
 * The probability figure (DESIGN.md §12): the YES percentage in white with a "chance"
 * label, as on Polymarket. It is white for every live market: colour belongs to the Yes
 * and No controls. For a resolved market it is replaced by the outcome, "YES" in green
 * or "NO" in red. It flashes when it changes.
 *
 * The figure is the API's `p_yes`, formatted with the decimals of `/config`.
 */
export function Price({ market, size = "card", live = false, className }: PriceProps) {
  const { price_display_decimals } = useConfig();
  const figure = size === "detail" ? "type-display" : "type-number-lg";

  if (market.status === "RESOLVED" && (market.outcome === "YES" || market.outcome === "NO")) {
    return (
      <span
        data-slot="price"
        className={cn(figure, market.outcome === "YES" ? "text-yes" : "text-no", className)}
      >
        <span className="sr-only">Resolved </span>
        {market.outcome}
      </span>
    );
  }

  const shown = formatPercent(market.p_yes, price_display_decimals);
  const spoken = `YES ${spokenPercent(market.p_yes, price_display_decimals)}`;

  return (
    <span
      data-slot="price"
      className={cn(
        "inline-flex",
        size === "detail" ? "items-baseline gap-2" : "flex-col items-end",
        className,
      )}
    >
      <span aria-hidden className={cn(figure, "text-foreground")}>
        <Flash watch={shown}>{shown}</Flash>
      </span>
      <span aria-hidden className="type-caption text-muted-foreground">
        chance
      </span>
      {live ? <LivePrice spoken={spoken} /> : <span className="sr-only">{spoken}</span>}
    </span>
  );
}

/** The price for a screen reader, in a polite live region that speaks at most every 10 s. */
function LivePrice({ spoken }: { spoken: string }) {
  const announced = useThrottledValue(spoken, PRICE_ANNOUNCE_MS);
  return (
    <span className="sr-only" aria-live="polite" aria-atomic="true">
      {announced}
    </span>
  );
}
