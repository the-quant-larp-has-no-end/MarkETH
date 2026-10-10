"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { FilterChips } from "@/components/controls";
import { EmptyState, QueryState } from "@/components/feedback";
import { MarketCard, MarketCardSkeleton, useAskAtClose } from "@/components/market";
import { Link, Screen } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { marketsQuery } from "@/lib/api/queries";

import { DEFAULT_FILTER, STATUS_CHIPS, type StatusFilter, statusesOf } from "./status-filter";

/** DESIGN.md §13: six skeleton cards while the list loads. */
const SKELETON_CARDS = 6;

/** One column on a phone, two from 768 px, three from 1024 px (DESIGN.md §6); 12 px between cards. */
const GRID = "grid gap-3 md:grid-cols-2 lg:grid-cols-3";

/** Cards of one row are equally tall, with the footer of each at its bottom. */
const CARD = "h-full [&>*:last-child]:mt-auto";

function ProposeLink({ width }: { width: "responsive" | "fit" }) {
  return (
    <Button variant="secondary" width={width} asChild>
      <Link href="/propose">Propose a market</Link>
    </Button>
  );
}

function MarketsSkeleton() {
  return (
    <div role="status" className={GRID}>
      <span className="sr-only">Loading markets</span>
      {Array.from({ length: SKELETON_CARDS }, (_, index) => (
        <MarketCardSkeleton key={index} />
      ))}
    </div>
  );
}

/**
 * Markets (`/`): DESIGN.md §13. The home page, as on Polymarket: the status chips, the
 * market cards in the order the API returns them, and the way to propose one.
 *
 * Each chip sends its statuses to `GET /markets` (`statusesOf`). The list is not live: it
 * is read when the screen opens, when a chip is chosen and when the window gets the focus
 * back, and a pull to refresh is the browser's own reload. One thing happens to it without
 * anybody acting: a market reaches its `close_at`. So the list is read again at the
 * earliest close time among the cards it shows as open for trading, and after the answer
 * at the earliest one that is left (`useAskAtClose`). Whether a card keeps its "Yes" and
 * "No" is `trading_open` of the answer.
 *
 * Nothing here sorts, filters or counts markets, and every figure on a card is the API's.
 */
export function MarketsScreen() {
  const [filter, setFilter] = useState<StatusFilter>(DEFAULT_FILTER);
  const markets = useQuery(marketsQuery(statusesOf(filter)));
  const { refetch } = markets;

  const closeTimes = (markets.data?.items ?? [])
    .filter((market) => market.trading_open)
    .map((market) => market.close_at);
  useAskAtClose(closeTimes, () => void refetch({ cancelRefetch: false }));

  return (
    <Screen top="main" title="Markets">
      <FilterChips label="Status" value={filter} onChange={setFilter} chips={STATUS_CHIPS} />

      <QueryState query={markets} skeleton={<MarketsSkeleton />}>
        {({ items }) =>
          items.length === 0 ? (
            <EmptyState illustration="dome" action={<ProposeLink width="fit" />}>
              No markets yet. Quieter than the HG on a Sunday.
            </EmptyState>
          ) : (
            <>
              <ul data-slot="market-list" className={GRID}>
                {items.map((market) => (
                  <li key={market.id} className="min-w-0">
                    <MarketCard market={market} className={CARD} />
                  </li>
                ))}
              </ul>
              <div className="flex">
                <ProposeLink width="responsive" />
              </div>
            </>
          )
        }
      </QueryState>
    </Screen>
  );
}
