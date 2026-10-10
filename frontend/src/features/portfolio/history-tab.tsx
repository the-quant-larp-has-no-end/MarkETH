"use client";

import { useInfiniteQuery } from "@tanstack/react-query";

import { EmptyState, LoadMore, QueryState } from "@/components/feedback";
import { RowList, RowSkeletons, TradeRow } from "@/components/rows";
import { tradesQuery } from "@/lib/api/queries";

import { LoadingRows } from "./loading-rows";

/**
 * The History tab (DESIGN.md §13): the viewer's trades from `GET /me/trades`, newest
 * first as the API sends them. More load with `next_cursor` when the user reaches the
 * end of the list; the screen names no page size. A row opens its market.
 *
 * It is rendered only while its tab is open, so the trades are asked for then.
 */
export function HistoryTab() {
  const trades = useInfiniteQuery(tradesQuery);

  return (
    <QueryState query={trades} skeleton={<LoadingRows />}>
      {({ pages }) => {
        const items = pages.flatMap((page) => page.items);
        if (items.length === 0) return <EmptyState>No trades yet.</EmptyState>;

        return (
          <>
            <RowList>
              {items.map((trade) => (
                <TradeRow key={trade.id} trade={trade} />
              ))}
            </RowList>
            <LoadMore query={trades} skeleton={<RowSkeletons count={1} />} />
          </>
        );
      }}
    </QueryState>
  );
}
