"use client";

import { useMemo } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, type XAxisTickContentProps, YAxis } from "recharts";

import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import type { HistoryPoint, MarketDetailResponse } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatDate, formatDateTime, formatPercent, spokenPercent } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { buildSeries, chartEnd, type SeriesPoint } from "./chart-series";

/** The one series. Its colour is the token `--chart-1`, which is `--yes`. */
const CHART = { p_yes: { label: "YES", color: "var(--chart-1)" } } satisfies ChartConfig;

/* DESIGN.md §12: a fixed axis from 0% to 100%, with gridlines at 25, 50 and 75%. */
const AXIS = [0, 1] as const;
const AXIS_LABELS = [0, 0.25, 0.5, 0.75, 1];
const GRIDLINES = [0.25, 0.5, 0.75];

/** How often the right edge of an open market's chart moves on. */
const CLOCK_STEP_MS = 60_000;

/** A label of the fixed axis: a whole percentage, whatever `price_display_decimals` is. */
const axisLabel = (fraction: number) => formatPercent(fraction, 0);

const iso = (at: number) => new Date(at).toISOString();

export type ProbabilityChartProps = {
  /** `points` of `GET /markets/{market_id}/history`, as sent: oldest first. */
  points: readonly HistoryPoint[];
  /** The market the history belongs to: its price now, and until when to draw it. */
  market: Pick<MarketDetailResponse, "p_yes" | "status" | "close_at" | "closed_at">;
  className?: string;
};

/**
 * The probability chart (DESIGN.md §12): `p_yes` over time as a step line, since each
 * trade holds its price until the next. A 2 px line in `--yes` over a flat fill of the
 * same colour at 12% opacity, on a fixed axis from 0% to 100%. 200 px tall on a phone,
 * 280 px from 768 px.
 *
 * It draws what `marketHistoryQuery` holds, so a stream event that appended a point
 * extends the line, and nothing is animated in from zero. Touching or hovering shows a
 * vertical guide and the percentage and time of the nearest point; the arrow keys do the
 * same for a keyboard.
 *
 * The chart is labelled with a summary, and the same two figures stand under it as text
 * (DESIGN.md §15): what the market opened at, and what it stands at now. For a market
 * whose trading is over that second figure is what it closed at.
 */
export function ProbabilityChart({ points, market, className }: ProbabilityChartProps) {
  const { timezone, price_display_decimals } = useConfig();
  const now = useNow(CLOCK_STEP_MS);
  // The chart is drawn afresh only when a point arrived or its right edge moved on.
  const until = chartEnd(market, now);
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

  // One day shows nothing by its date alone: then the two ends carry their time as well.
  const sameDay = formatDate(iso(series.start), timezone) === formatDate(iso(series.end), timezone);
  const edgeLabel = (at: number) =>
    sameDay ? formatDateTime(iso(at), timezone) : formatDate(iso(at), timezone);

  return (
    <figure data-slot="probability-chart" className={cn("flex flex-col gap-2", className)}>
      <ChartContainer config={CHART} className="h-50 w-full md:h-70">
        <AreaChart
          data={series.points}
          margin={{ top: 8, right: 0, bottom: 0, left: 0 }}
          aria-label={summary}
        >
          <CartesianGrid vertical={false} horizontalValues={GRIDLINES} />
          <XAxis
            dataKey="at"
            type="number"
            domain={[series.start, series.end]}
            // The first and the last date only.
            ticks={[series.start, series.end]}
            interval={0}
            tickLine={false}
            axisLine={false}
            height={24}
            tick={(tick: XAxisTickContentProps) => (
              <text
                x={tick.x}
                y={tick.y}
                dy="1em"
                textAnchor={tick.index === 0 ? "start" : "end"}
                className="fill-muted-foreground"
              >
                {edgeLabel(Number(tick.payload.value))}
              </text>
            )}
          />
          <YAxis
            dataKey="p_yes"
            type="number"
            domain={[...AXIS]}
            ticks={AXIS_LABELS}
            interval={0}
            orientation="right"
            width="auto"
            tickLine={false}
            axisLine={false}
            tickMargin={8}
            tickFormatter={axisLabel}
          />
          <ChartTooltip
            isAnimationActive={false}
            cursor={{ strokeWidth: 1 }}
            content={
              <ChartTooltipContent
                hideLabel
                hideIndicator
                formatter={(_value, _name, item) => {
                  const point = item.payload as SeriesPoint;
                  return (
                    <div className="flex flex-col gap-0.5">
                      <span className="type-number text-foreground">
                        {formatPercent(point.p_yes, price_display_decimals)} YES
                      </span>
                      <time dateTime={iso(point.at)} className="text-muted-foreground">
                        {formatDateTime(iso(point.at), timezone)}
                      </time>
                    </div>
                  );
                }}
              />
            }
          />
          <Area
            dataKey="p_yes"
            name="YES"
            type="stepAfter"
            stroke="var(--color-p_yes)"
            strokeWidth={2}
            fill="var(--color-p_yes)"
            fillOpacity={0.12}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ChartContainer>
      <figcaption className="type-caption text-muted-foreground">
        Opened at {formatPercent(opened.p_yes, price_display_decimals)}, {latest}{" "}
        {formatPercent(market.p_yes, price_display_decimals)}.
      </figcaption>
    </figure>
  );
}
