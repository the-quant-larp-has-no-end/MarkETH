"use client";

import { useMemo } from "react";

import type { ChartConfig } from "@/components/ui/chart";
import { TimeSeriesChart } from "@/components/ui/time-series-chart";
import type { HistoryPoint, MarketDetailResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatPercent, spokenPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

import { buildSeries } from "./chart-series";

/** The one series. Its colour is the token `--chart-1`, which is `--yes`. */
const CHART = { p_yes: { label: "YES", color: "var(--chart-1)" } } satisfies ChartConfig;

/* DESIGN.md §12: a fixed axis from 0% to 100%, with gridlines at 25, 50 and 75%. */
const AXIS = [0, 1];
const AXIS_LABELS = [0, 0.25, 0.5, 0.75, 1];
const GRIDLINES = [0.25, 0.5, 0.75];

/** A label of the fixed axis: a whole percentage, whatever `price_display_decimals` is. */
const axisLabel = (fraction: number) => formatPercent(fraction, 0);

export type ProbabilityChartProps = {
  /** `points` of `GET /markets/{market_id}/history`, as sent: oldest first. */
  points: readonly HistoryPoint[];
  /**
   * `until` of the same answer: the right edge of the chart. It is the server's now while
   * the market can still trade, otherwise the moment trading ended.
   */
  until: string;
  /** The market the history belongs to: its price now, and whether it can still move. */
  market: Pick<MarketDetailResponse, "p_yes" | "status">;
  className?: string;
};

/**
 * The probability chart (DESIGN.md §12): `p_yes` over time as a step line, since each
 * trade holds its price until the next. A 2 px line in `--yes` over a flat fill of the
 * same colour at 12% opacity, on a fixed axis from 0% to 100%. 200 px tall on a phone,
 * 280 px from 768 px.
 *
 * It draws what `marketHistoryQuery` holds, so a stream event that appended a point
 * extends the line, and nothing is animated in from zero. The last price holds as a flat
 * line up to `until`, and the last date of the x axis is that moment: the chart ends
 * where the server says it does and reads no clock of the device. Touching or hovering
 * shows a vertical guide and the percentage and time of the nearest point; the arrow keys
 * do the same for a keyboard.
 *
 * The chart is labelled with a summary, and the same two figures stand under it as text
 * (DESIGN.md §15): what the market opened at, and what it stands at now. For a market
 * whose trading is over that second figure is what it closed at.
 */
export function ProbabilityChart({ points, until, market, className }: ProbabilityChartProps) {
  const { price_display_decimals } = useConfig();
  // The chart is drawn afresh only when a point arrived or its right edge moved on.
  const series = useMemo(() => buildSeries(points, until), [points, until]);
  const opened = points[0];

  if (series === null || opened === undefined) {
    return (
      <p data-slot="probability-chart" className="type-body-sm text-muted-foreground">
        No trades to chart yet.
      </p>
    );
  }

  // The market's own price is the last word: "now" while it can still move.
  const latest = market.status === "OPEN" || market.status === "HALTED" ? "now" : "closed at";
  const summary = `YES probability over time, ${latest} ${spokenPercent(market.p_yes, price_display_decimals)}, opened at ${spokenPercent(opened.p_yes, price_display_decimals)}`;

  return (
    <figure data-slot="probability-chart" className={cn("flex flex-col gap-2", className)}>
      <TimeSeriesChart
        series={series}
        config={CHART}
        dataKey="p_yes"
        name="YES"
        summary={summary}
        domain={AXIS}
        ticks={AXIS_LABELS}
        gridlines={GRIDLINES}
        axisLabel={axisLabel}
        renderValue={(point) => `${formatPercent(point.p_yes, price_display_decimals)} YES`}
      />
      <figcaption className="type-caption text-muted-foreground">
        Opened at {formatPercent(opened.p_yes, price_display_decimals)}, {latest}{" "}
        {formatPercent(market.p_yes, price_display_decimals)}.
      </figcaption>
    </figure>
  );
}
