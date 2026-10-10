import type { HistoryPoint, MarketDetailResponse } from "@/lib/api/schema";

/** One point of the chart: when, in epoch milliseconds, and the API's `p_yes` as sent. */
export type SeriesPoint = { at: number; p_yes: number };

export type Series = {
  /** The points of `GET /markets/{market_id}/history`, and the last of them held to `end`. */
  points: SeriesPoint[];
  /** The left edge: the opening point. */
  start: number;
  /** The right edge: until when the last price is drawn. */
  end: number;
};

/**
 * The narrowest chart: a market with nothing but its opening point, opened this very
 * minute, still gets a line and not a dot (DESIGN.md §12).
 */
const MIN_SPAN_MS = 60_000;

/**
 * Until when a market's last price is drawn. While it can still be traded (OPEN, or
 * paused) that is now; once trading is over it is the moment trading stopped: `close_at`,
 * or `closed_at` when VIS closed the market before that.
 *
 * `now` is null where no clock is known; the chart then ends at its last point.
 */
export function chartEnd(
  market: Pick<MarketDetailResponse, "status" | "close_at" | "closed_at">,
  now: number | null,
): number | null {
  if (market.status === "OPEN" || market.status === "HALTED") return now;
  const deadline = Date.parse(market.close_at);
  const closed = market.closed_at === null ? Number.NaN : Date.parse(market.closed_at);
  if (Number.isNaN(deadline)) return Number.isNaN(closed) ? null : closed;
  return Number.isNaN(closed) ? deadline : Math.min(deadline, closed);
}

/**
 * The step line of the probability chart (DESIGN.md §12): the history's points in the
 * order the API sends them, starting at the opening point, each holding its price until
 * the next. The last one holds until `until`, so the line reaches the chart's right edge
 * and a market nobody has traded yet shows a flat line.
 *
 * No price is worked out here: every `p_yes` is one the API sent, and the point at the
 * right edge repeats the last of them. Null when there is no point at all.
 */
export function buildSeries(history: readonly HistoryPoint[], until: number | null): Series | null {
  const points: SeriesPoint[] = [];
  for (const point of history) {
    const at = Date.parse(point.t);
    if (Number.isNaN(at)) continue;
    // A line never runs backwards: a point is drawn no earlier than the one before it.
    const previous = points.at(-1);
    points.push({
      at: previous === undefined ? at : Math.max(at, previous.at),
      p_yes: point.p_yes,
    });
  }

  const first = points[0];
  const last = points.at(-1);
  if (first === undefined || last === undefined) return null;

  let end = until === null ? last.at : Math.max(until, last.at);
  if (end - first.at < MIN_SPAN_MS) end = first.at + MIN_SPAN_MS;
  if (end > last.at) points.push({ at: end, p_yes: last.p_yes });

  return { points, start: first.at, end };
}
