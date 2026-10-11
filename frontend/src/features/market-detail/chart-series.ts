import { holdUntil } from "@/components/ui/time-series";
import type { HistoryPoint } from "@/lib/api/schema";

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
 * The step line of the probability chart (DESIGN.md §12): the history's points in the
 * order the API sends them, starting at the opening point, each holding its price until
 * the next. The last one holds until `until`, so the line reaches the chart's right edge
 * and a market nobody has traded yet shows a flat line.
 *
 * `until` is `HistoryResponse.until`: the server's now while the market can still trade,
 * otherwise the moment trading ended. No clock of the device is read here. A point that
 * lies after it (an event of the stream that arrived since the history was read) moves
 * the edge to itself: the line never ends before its last point.
 *
 * No price is worked out here: every `p_yes` is one the API sent, and the point at the
 * right edge repeats the last of them. Null when there is no point at all.
 */
export function buildSeries(history: readonly HistoryPoint[], until: string): Series | null {
  return holdUntil(
    history.map((point) => ({ at: Date.parse(point.t), p_yes: point.p_yes })),
    until,
  );
}
