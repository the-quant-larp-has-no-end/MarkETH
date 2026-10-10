"use client";

import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { QueryState } from "@/components/feedback";
import { NotFoundScreen, Screen } from "@/components/shell";
import { Skeleton } from "@/components/ui/skeleton";
import type { ApiError } from "@/lib/api/errors";
import { marketQuery } from "@/lib/api/queries";
import type { MarketDetailResponse } from "@/lib/api/schema";

import { MarketActions } from "./market-actions";
import { MarketStatusBadge } from "./market-status";
import { MarketFacts, MarketRules, Resolution } from "./market-view";

export type AdminMarketScreenProps = {
  /** The `[id]` of the route `/admin/markets/[id]`. */
  marketId: string;
};

/**
 * A market the API does not know is the "not found" screen (DESIGN.md §13). So is an id
 * that cannot be one: the API refuses it as a request it cannot read.
 */
function isUnknownMarket(error: ApiError | null): boolean {
  return error?.code === "NOT_FOUND" || error?.code === "VALIDATION_ERROR";
}

/** Blocks in the shape of the question, the rules and the details. */
function MarketSkeleton() {
  return (
    <div data-slot="market-skeleton" role="status" className="flex flex-col gap-6">
      <span className="sr-only">Loading</span>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-7 w-full" />
        <Skeleton className="h-7 w-2/3" />
        <Skeleton className="h-6 w-20" />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
        <div className="flex flex-col gap-3">
          <Skeleton className="h-6 w-24" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 md:flex">
        <Skeleton className="h-12 md:w-28" />
        <Skeleton className="h-12 md:w-28" />
      </div>
    </div>
  );
}

function AdminMarket({ market }: { market: MarketDetailResponse }) {
  const question = useRef<HTMLHeadingElement>(null);

  return (
    <>
      <header className="flex flex-col items-start gap-3">
        {/* It takes the focus after an action removed its own button (MarketActions). */}
        <h1 ref={question} tabIndex={-1} className="type-h2 wrap-break-word">
          {market.question}
        </h1>
        {/* Announced when an action changed it. */}
        <span role="status">
          <MarketStatusBadge status={market.status} outcome={market.outcome} />
        </span>
      </header>
      <div className="grid gap-6 lg:grid-cols-2">
        <MarketRules market={market} />
        <div className="flex flex-col gap-6">
          {market.resolution !== null && <Resolution resolution={market.resolution} />}
          <MarketFacts market={market} />
        </div>
      </div>
      <MarketActions market={market} returnFocusTo={question} />
    </>
  );
}

/**
 * Admin: one market and its actions (`/admin/markets/[id]`, DESIGN.md §13). An inner
 * screen whose back arrow leads to Admin. From `GET /markets/{market_id}`: the question,
 * which is the screen's `h1`, the status badge, the rules as on Market detail, the
 * resolution when there is one, the facts only an admin needs, then the action bar.
 *
 * The answer of every action is the market, so the screen updates from it. Nothing here
 * is live: the market is read when the screen opens and when the window gets the focus
 * back.
 */
export function AdminMarketScreen({ marketId }: AdminMarketScreenProps) {
  const market = useQuery(marketQuery(marketId));

  if (market.data === undefined && isUnknownMarket(market.error)) return <NotFoundScreen />;

  return (
    <Screen top="inner" backTo="/admin">
      {market.data === undefined && <h1 className="sr-only">Market</h1>}
      <QueryState query={market} skeleton={<MarketSkeleton />}>
        {(data) => <AdminMarket market={data} />}
      </QueryState>
    </Screen>
  );
}
