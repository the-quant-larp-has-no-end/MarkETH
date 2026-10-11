"use client";

import type { ComponentProps, ReactNode } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  type DataKey,
  XAxis,
  type XAxisTickContentProps,
  YAxis,
} from "recharts";

import { useConfig } from "@/lib/api/session";
import { formatDate, formatDateTime } from "@/lib/format";

import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "./chart";

const iso = (at: number) => new Date(at).toISOString();

/** Shared market/portfolio chart: step line, flat fill, date edges and accessible tooltip. */
export function TimeSeriesChart<T extends { at: number }>({
  series,
  config,
  dataKey,
  name,
  summary,
  domain = ["auto", "auto"],
  ticks,
  gridlines,
  axisLabel,
  renderValue,
}: {
  series: { points: T[]; start: number; end: number };
  config: ChartConfig;
  dataKey: DataKey<T> & string;
  name: string;
  summary: string;
  domain?: ComponentProps<typeof YAxis>["domain"];
  ticks?: number[];
  gridlines?: number[];
  axisLabel: (value: number) => string;
  renderValue: (point: T) => ReactNode;
}) {
  const { timezone } = useConfig();
  const sameDay = formatDate(iso(series.start), timezone) === formatDate(iso(series.end), timezone);
  const edgeLabel = (at: number) =>
    sameDay ? formatDateTime(iso(at), timezone) : formatDate(iso(at), timezone);
  const color = `var(--color-${dataKey})`;

  return (
    <ChartContainer config={config} className="h-50 w-full md:h-70">
      <AreaChart
        data={series.points}
        margin={{ top: 8, right: 0, bottom: 0, left: 0 }}
        aria-label={summary}
      >
        <CartesianGrid vertical={false} horizontalValues={gridlines} />
        <XAxis
          dataKey="at"
          type="number"
          domain={[series.start, series.end]}
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
          dataKey={dataKey}
          type="number"
          domain={domain}
          ticks={ticks}
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
                const point = item.payload as T;
                return (
                  <div className="flex flex-col gap-0.5">
                    <span className="type-number text-foreground">{renderValue(point)}</span>
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
          dataKey={dataKey}
          name={name}
          type="stepAfter"
          stroke={color}
          strokeWidth={2}
          fill={color}
          fillOpacity={0.12}
          dot={false}
          activeDot={{ r: 4, strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ChartContainer>
  );
}
