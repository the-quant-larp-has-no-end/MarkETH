import { Skeleton } from "@/components/ui/skeleton";

/** The chart while its history loads: a block of its height, 200 px and 280 px from 768 px. */
export function ChartSkeleton() {
  return <Skeleton data-slot="chart-skeleton" className="h-50 w-full md:h-70" />;
}

/**
 * Market detail while the market loads (DESIGN.md §13): blocks in the shape of the
 * question, the Price, the chart, the outcome buttons and the rules.
 */
export function MarketDetailSkeleton() {
  return (
    <div data-slot="market-detail-skeleton" role="status" className="flex flex-col gap-6">
      <span className="sr-only">Loading market</span>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <Skeleton className="h-7 w-full" />
          <Skeleton className="h-7 w-2/3" />
        </div>
        <Skeleton className="h-6 w-16 rounded-sm" />
        <Skeleton className="h-13 w-44" />
        <Skeleton className="h-5 w-52" />
      </div>

      <ChartSkeleton />

      <div className="grid grid-cols-2 gap-2">
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </div>

      <div className="flex flex-col gap-4 rounded-lg border bg-card p-4">
        <Skeleton className="h-6 w-20" />
        {[0, 1, 2].map((row) => (
          <div key={row} className="flex flex-col gap-2">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-5 w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}
