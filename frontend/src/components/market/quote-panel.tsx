"use client";

import type { ReactNode } from "react";

import { TokenIcon } from "@/components/brand";
import { Flash } from "@/components/feedback";
import { Amount } from "@/components/money";
import { Skeleton } from "@/components/ui/skeleton";
import type { Outcome, QuoteResponse, TradeSide } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatAmount, formatAveragePrice, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-h-6 items-center justify-between gap-3">
      <dt className="type-body-sm text-muted-foreground">{label}</dt>
      <dd className="flex items-center justify-end type-number">{children}</dd>
    </div>
  );
}

/**
 * The quote panel of the trade sheet (DESIGN.md §12): a `--background` inset block with
 * what the trade would do right now. Every value is a field of the quote, shown as sent:
 * "You pay" is the quote's `amount_cr`, not the chip that was tapped.
 *
 * Without a quote (`null`) the values are skeleton bars and the labels stay put, so the
 * sheet does not jump. A value that changes flashes, which is how a `QUOTE_STALE` answer
 * shows what moved.
 */
export function QuotePanel({
  side,
  outcome,
  quote,
  className,
}: {
  /** What the user is doing, which names the first row. */
  side: TradeSide;
  /** The selected outcome, for the line under the panel. */
  outcome: Outcome;
  /** The quote for the current input, or null while it is on its way. */
  quote: QuoteResponse | null;
  className?: string;
}) {
  const { price_display_decimals } = useConfig();
  const bar = <Skeleton className="h-4 w-20" />;
  const averagePrice =
    quote === null ? "" : formatAveragePrice(quote.average_price, price_display_decimals);
  const probability =
    quote === null ? "" : formatPercent(quote.p_yes_after, price_display_decimals);

  return (
    <div data-slot="quote-panel" className={cn("flex flex-col gap-2", className)}>
      <dl aria-busy={quote === null} className="flex flex-col gap-2 rounded-md bg-background p-3">
        <Row label={side === "BUY" ? "You pay" : "You receive"}>
          {quote === null ? (
            bar
          ) : (
            <Flash watch={quote.amount_cr}>
              <Amount value={quote.amount_cr} />
            </Flash>
          )}
        </Row>
        <Row label="Shares">
          {quote === null ? bar : <Flash watch={quote.shares}>{formatAmount(quote.shares)}</Flash>}
        </Row>
        <Row label="Average price">
          {quote === null ? (
            bar
          ) : (
            <Flash watch={averagePrice}>
              <span className="inline-flex items-center gap-1">
                <TokenIcon />
                <span aria-hidden>{averagePrice}</span>
                <span className="sr-only">{averagePrice} tokens</span>
                <span className="type-body-sm text-muted-foreground">per share</span>
              </span>
            </Flash>
          )}
        </Row>
        <Row label="New probability">
          {quote === null ? bar : <Flash watch={probability}>{probability} YES</Flash>}
        </Row>
      </dl>
      <p className="type-body-sm text-muted-foreground">
        Each share pays 1 token if {outcome} wins.
      </p>
    </div>
  );
}
