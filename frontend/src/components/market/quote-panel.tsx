"use client";

import type { ReactNode } from "react";

import { TokenIcon } from "@/components/brand";
import { Flash } from "@/components/feedback";
import { Amount, Missing } from "@/components/money";
import { Skeleton } from "@/components/ui/skeleton";
import type { Outcome, QuoteResponse, TradeSide } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatAmount, formatAveragePrice, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A row is 24 px tall whatever stands in it, so the panel never changes its height. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex h-6 items-center justify-between gap-3">
      <dt className="type-body-sm text-muted-foreground">{label}</dt>
      <dd className="flex items-center justify-end type-number">{children}</dd>
    </div>
  );
}

/* A value and its flash are one box of the row, not a line of text around a box. */
const VALUE = "inline-flex items-center";

/**
 * The quote panel of the trade sheet (DESIGN.md §12): a `--background` inset block with
 * what the trade would do right now. Every value is a field of the quote, shown as sent:
 * "You pay" is the quote's `amount_cr`, not what the amount field holds.
 *
 * The labels stay put in each of its three states, so the sheet does not jump:
 *
 * - With a quote: its values. One that changes flashes, which is how a `QUOTE_STALE`
 *   answer shows what moved.
 * - Without one (`null`), while it is on its way: skeleton bars.
 * - `idle`, with nothing to quote and nothing on its way: no amount is set, the server
 *   refused the amount, or the quote could not be read. No row has a value then: each
 *   shows the en dash of a missing figure, read out as "none yet", and nothing pulses.
 */
export function QuotePanel({
  side,
  outcome,
  quote,
  idle = false,
  className,
}: {
  /** What the user is doing, which names the first row. */
  side: TradeSide;
  /** The selected outcome, for the line under the panel. */
  outcome: Outcome;
  /** The quote for the current input, or null while there is none. */
  quote: QuoteResponse | null;
  /** There is no quote and none is on its way. */
  idle?: boolean;
  className?: string;
}) {
  const { price_display_decimals } = useConfig();
  const waiting = quote === null && !idle;
  const missing = waiting ? (
    <Skeleton className="h-4 w-20" />
  ) : (
    <Missing className="text-muted-foreground" />
  );
  const averagePrice =
    quote === null ? "" : formatAveragePrice(quote.average_price, price_display_decimals);
  const probability =
    quote === null ? "" : formatPercent(quote.p_yes_after, price_display_decimals);

  return (
    <div
      data-slot="quote-panel"
      data-state={quote !== null ? "quoted" : waiting ? "waiting" : "idle"}
      className={cn("flex flex-col gap-2", className)}
    >
      <dl
        aria-busy={waiting || undefined}
        className="flex flex-col gap-2 rounded-md bg-background p-3"
      >
        <Row label={side === "BUY" ? "You pay" : "You receive"}>
          {quote === null ? (
            missing
          ) : (
            <Flash watch={quote.amount_cr} className={VALUE}>
              <Amount value={quote.amount_cr} />
            </Flash>
          )}
        </Row>
        <Row label="Shares">
          {quote === null ? (
            missing
          ) : (
            <Flash watch={quote.shares} className={VALUE}>
              {formatAmount(quote.shares)}
            </Flash>
          )}
        </Row>
        <Row label="Average price">
          {quote === null ? (
            missing
          ) : (
            <Flash watch={averagePrice} className={cn(VALUE, "gap-1")}>
              <TokenIcon />
              <span aria-hidden>{averagePrice}</span>
              <span className="sr-only">{averagePrice} tokens</span>
              <span className="type-body-sm text-muted-foreground">per share</span>
            </Flash>
          )}
        </Row>
        <Row label="New probability">
          {quote === null ? (
            missing
          ) : (
            <Flash watch={probability} className={VALUE}>
              {probability} YES
            </Flash>
          )}
        </Row>
      </dl>
      <p className="type-body-sm text-muted-foreground">
        Each share pays 1 token if {outcome} wins.
      </p>
    </div>
  );
}
