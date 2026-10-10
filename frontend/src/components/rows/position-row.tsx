"use client";

import { StatusBadge } from "@/components/market/status-badge";
import { Amount } from "@/components/money";
import { Link } from "@/components/shell/link";
import type { Holding, PortfolioEntry } from "@/lib/api/schema";
import { formatShares, isZero } from "@/lib/format";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, rowPressClasses, RowText } from "./list-row";

/**
 * What is held of a market, in words: "183.29 YES shares" for each holding whose
 * `shares` is not `0`, and `none` when there is no such holding.
 */
export function holdingsText(holdings: readonly Holding[], none: string): string {
  const held = holdings.filter((holding) => !isZero(holding.shares));
  if (held.length === 0) return none;
  return held
    .map((holding) => formatShares(holding.shares, { outcome: holding.outcome }))
    .join(", ");
}

/**
 * One position of the Portfolio (DESIGN.md §12, §13): the question with the holdings
 * below, and what the position is worth. A market is settled when its `status` is
 * RESOLVED or VOID: then the row shows "Paid out" with `payout_cr` or "Refunded" with
 * `refund_cr` in place of `value_cr`, and the status badge. Every figure is the API's;
 * nothing is added up or compared here. The row is one link to its market.
 */
export function PositionRow({ entry }: { entry: PortfolioEntry }) {
  const { market } = entry;
  const paidOut = market.status === "RESOLVED" && entry.payout_cr !== null;
  const refunded = market.status === "VOID" && entry.refund_cr !== null;
  const settled = market.status === "RESOLVED" || market.status === "VOID";

  return (
    <RowItem>
      <Link href={`/markets/${market.id}`} className={cn(rowClasses, rowPressClasses)}>
        <RowText
          wrap
          title={market.question}
          below={holdingsText(entry.holdings, "No shares held")}
        />
        <RowFigure
          below={
            settled ? <StatusBadge status={market.status} outcome={market.outcome} /> : undefined
          }
        >
          {paidOut || refunded ? (
            <span className="inline-flex items-center gap-2">
              <span className="type-caption text-muted-foreground">
                {paidOut ? "Paid out" : "Refunded"}
              </span>
              <Amount value={(paidOut ? entry.payout_cr : entry.refund_cr) ?? entry.value_cr} />
            </span>
          ) : (
            <Amount value={entry.value_cr} />
          )}
        </RowFigure>
      </Link>
    </RowItem>
  );
}
