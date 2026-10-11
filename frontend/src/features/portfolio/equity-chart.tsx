"use client";

import { useMemo } from "react";

import type { ChartConfig } from "@/components/ui/chart";
import { holdUntil } from "@/components/ui/time-series";
import { TimeSeriesChart } from "@/components/ui/time-series-chart";
import type { PortfolioResponse } from "@/lib/api/schema";
import { formatTokens } from "@/lib/format";

const CHART = {
  plot_value: { label: "Total equity", color: "var(--chart-1)" },
} satisfies ChartConfig;

// Only the server's approximate plotting coordinates pass through this formatter.
const axisLabel = (value: number) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(value);

export function EquityChart({
  portfolio,
}: {
  portfolio: Pick<PortfolioResponse, "equity_history" | "until" | "total_equity_cr">;
}) {
  const { equity_history: points, until, total_equity_cr: current } = portfolio;
  const series = useMemo(
    () =>
      holdUntil(
        points.map((point) => ({ ...point, at: Date.parse(point.t) })),
        until,
      ),
    [points, until],
  );
  const opening = points[0];
  if (series === null || opening === undefined) {
    return <p className="type-body-sm text-muted-foreground">No equity history yet.</p>;
  }

  return (
    <figure data-slot="equity-chart" className="flex flex-col gap-2">
      <TimeSeriesChart
        series={series}
        config={CHART}
        dataKey="plot_value"
        name="Total equity"
        domain={[0, "auto"]}
        summary={`Total equity over time in tokens, now ${formatTokens(current)}, started at ${formatTokens(opening.total_equity_cr)}`}
        axisLabel={axisLabel}
        renderValue={(point) => formatTokens(point.total_equity_cr)}
      />
      <figcaption className="type-caption text-muted-foreground">
        Started at {formatTokens(opening.total_equity_cr)}, now {formatTokens(current)}.
      </figcaption>
    </figure>
  );
}
