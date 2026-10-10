"use client";

import { Amount } from "@/components/money";
import { Link } from "@/components/shell/link";
import { DateTime } from "@/components/time";
import type { TradeItem } from "@/lib/api/schema";
import { formatShares } from "@/lib/format";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, rowPressClasses, RowText } from "./list-row";

/**
 * One trade of the Portfolio's history (DESIGN.md §12): the market's question on one
 * line with "Bought YES" or "Sold NO" and the time below, the amount, and the shares
 * below it. The row is one link to its market.
 */
export function TradeRow({ trade }: { trade: TradeItem }) {
  return (
    <RowItem>
      <Link href={`/markets/${trade.market_id}`} className={cn(rowClasses, rowPressClasses)}>
        <RowText
          title={trade.market_question}
          below={
            <>
              {trade.side === "BUY" ? "Bought" : "Sold"} {trade.outcome}
              {" · "}
              <DateTime value={trade.created_at} />
            </>
          }
        />
        <RowFigure below={formatShares(trade.shares)}>
          <Amount value={trade.amount_cr} />
        </RowFigure>
      </Link>
    </RowItem>
  );
}
