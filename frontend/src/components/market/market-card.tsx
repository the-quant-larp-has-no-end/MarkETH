"use client";

import { Amount } from "@/components/money";
import { Link } from "@/components/shell/link";
import { Skeleton } from "@/components/ui/skeleton";
import type { MarketSummary } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { Deadline } from "./deadline";
import { Price } from "./price";
import { StatusBadge } from "./status-badge";

const softButton =
  "inline-flex h-11 items-center justify-center rounded-md type-label transition-opacity duration-(--duration-fast) hover:opacity-90 active:opacity-90";

/**
 * A market in a list (DESIGN.md §12): Polymarket's card. The question with the price
 * beside it, two soft outcome buttons, and a footer with the volume and the deadline.
 * The whole card is one link to the market; "Yes" and "No" are links of their own that
 * open it with the trade sheet on that outcome. They use the soft tints, never the full
 * fills, so a long list stays calm, and they are shown only while `trading_open` is
 * true; otherwise the status badge stands in their place.
 */
export function MarketCard({ market, className }: { market: MarketSummary; className?: string }) {
  const href = `/markets/${market.id}`;

  return (
    <article
      data-slot="market-card"
      className={cn(
        "relative flex flex-col gap-3 rounded-lg border bg-card p-4 text-card-foreground",
        "transition-colors duration-(--duration-fast)",
        "has-[[data-slot=market-link]:hover]:bg-raised has-[[data-slot=market-link]:active]:bg-raised",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <h2 className="line-clamp-3 min-w-0 type-h3">
          {/* The link's hit area is stretched over the card; the buttons sit above it. */}
          <Link
            href={href}
            data-slot="market-link"
            className="outline-none after:absolute after:inset-0 after:rounded-lg focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-ring"
          >
            {market.question}
          </Link>
        </h2>
        <Price market={market} className="shrink-0" />
      </div>

      {market.trading_open ? (
        <div className="relative z-10 grid grid-cols-2 gap-2">
          <Link
            href={`${href}?buy=YES`}
            aria-label={`Yes: ${market.question}`}
            className={cn(softButton, "bg-yes-soft text-yes-text")}
          >
            Yes
          </Link>
          <Link
            href={`${href}?buy=NO`}
            aria-label={`No: ${market.question}`}
            className={cn(softButton, "bg-no-soft text-no-text")}
          >
            No
          </Link>
        </div>
      ) : (
        <StatusBadge status={market.status} outcome={market.outcome} />
      )}

      <div className="flex items-center justify-between gap-3 type-body-sm text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <Amount value={market.volume_cr} size="sm" />
          Vol.
        </span>
        <Deadline closeAt={market.close_at} tradingOpen={market.trading_open} />
      </div>
    </article>
  );
}

/** A market card that is still loading: the same frame, with blocks where its parts will be. */
export function MarketCardSkeleton({ className }: { className?: string }) {
  return (
    <div
      data-slot="market-card-skeleton"
      aria-hidden
      className={cn("flex flex-col gap-3 rounded-lg border bg-card p-4", className)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </div>
        <Skeleton className="h-10 w-16 shrink-0" />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Skeleton className="h-11" />
        <Skeleton className="h-11" />
      </div>
      <div className="flex items-center justify-between gap-3">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-4 w-28" />
      </div>
    </div>
  );
}
