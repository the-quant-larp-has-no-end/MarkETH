import { describe, expect, test } from "vitest";

import { history, marketDetail } from "@/test/fixtures";

import { buildSeries, chartEnd } from "./chart-series";

const at = (iso: string) => Date.parse(iso);
const MINUTE = 60_000;

describe("buildSeries", () => {
  test("keeps the history's points in the order and with the prices the API sent", () => {
    const series = buildSeries(history().points, null)!;

    expect(series.points).toEqual([
      { at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 },
      { at: at("2026-10-02T09:30:00Z"), p_yes: 0.524 },
      { at: at("2026-10-03T17:45:00Z"), p_yes: 0.548 },
    ]);
  });

  test("starts at the opening point", () => {
    const series = buildSeries(history().points, at("2026-10-11T10:00:00Z"))!;

    expect(series.start).toBe(at("2026-10-01T12:00:00Z"));
    expect(series.points[0]).toEqual({ at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 });
  });

  test("holds the last price until the right edge: the point there repeats it", () => {
    const until = at("2026-10-11T10:00:00Z");

    const series = buildSeries(history().points, until)!;

    expect(series.end).toBe(until);
    expect(series.points).toHaveLength(4);
    expect(series.points.at(-1)).toEqual({ at: until, p_yes: 0.548 });
  });

  test("a market nobody has traded yet is a flat line from its opening point, not an empty box", () => {
    const opening = [{ t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 }];
    const until = at("2026-10-04T12:00:00Z");

    const series = buildSeries(opening, until)!;

    expect(series.points).toEqual([
      { at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 },
      { at: until, p_yes: 0.5 },
    ]);
  });

  test("a market opened this very minute still has a line: the chart is at least a minute wide", () => {
    const opening = [{ t: "2026-10-01T12:00:20Z", p_yes: 0.5, version: 0 }];
    const start = at("2026-10-01T12:00:20Z");

    // The clock of the chart moves in whole minutes, so "now" can lie before the opening.
    const series = buildSeries(opening, at("2026-10-01T12:00:00Z"))!;

    expect(series.start).toBe(start);
    expect(series.end).toBe(start + MINUTE);
    expect(series.points).toEqual([
      { at: start, p_yes: 0.5 },
      { at: start + MINUTE, p_yes: 0.5 },
    ]);
  });

  test("never ends before its last point, whatever the clock says", () => {
    const series = buildSeries(history().points, at("2026-10-02T00:00:00Z"))!;

    expect(series.end).toBe(at("2026-10-03T17:45:00Z"));
    expect(series.points).toHaveLength(3);
  });

  test("without a clock it ends at its last point", () => {
    const series = buildSeries(history().points, null)!;

    expect(series.end).toBe(at("2026-10-03T17:45:00Z"));
  });

  test("a point is drawn no earlier than the one before it", () => {
    const series = buildSeries(
      [
        { t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 },
        { t: "2026-10-03T12:00:00Z", p_yes: 0.6, version: 1 },
        // The stream stamps its first event with the time of the connection.
        { t: "2026-10-02T12:00:00Z", p_yes: 0.55, version: 2 },
      ],
      null,
    )!;

    expect(series.points.map((point) => point.at)).toEqual([
      at("2026-10-01T12:00:00Z"),
      at("2026-10-03T12:00:00Z"),
      at("2026-10-03T12:00:00Z"),
    ]);
    expect(series.points.map((point) => point.p_yes)).toEqual([0.5, 0.6, 0.55]);
  });

  test("leaves out a point whose time cannot be read, and is null without any point", () => {
    expect(buildSeries([], at("2026-10-11T10:00:00Z"))).toBeNull();
    expect(buildSeries([{ t: "soon", p_yes: 0.5, version: 0 }], null)).toBeNull();
  });

  test("invents no price: every p_yes of the series is one the API sent", () => {
    const sent = new Set(history().points.map((point) => point.p_yes));

    const series = buildSeries(history().points, at("2026-10-11T10:00:00Z"))!;

    for (const point of series.points) expect(sent.has(point.p_yes)).toBe(true);
  });
});

describe("chartEnd", () => {
  const now = at("2026-10-11T10:00:00Z");

  test.each(["OPEN", "HALTED"] as const)("a %s market is drawn until now", (status) => {
    expect(chartEnd(marketDetail({ status }), now)).toBe(now);
    expect(chartEnd(marketDetail({ status }), null)).toBeNull();
  });

  test.each(["CLOSED", "RESOLVED", "VOID"] as const)(
    "a %s market is drawn until trading stopped, not until now",
    (status) => {
      const market = marketDetail({ status, close_at: "2026-10-14T16:00:00Z", closed_at: null });

      expect(chartEnd(market, at("2026-12-01T00:00:00Z"))).toBe(at("2026-10-14T16:00:00Z"));
    },
  );

  test("a market VIS closed before its deadline ends when it was closed", () => {
    const market = marketDetail({
      status: "CLOSED",
      close_at: "2026-10-14T16:00:00Z",
      closed_at: "2026-10-12T08:00:00Z",
    });

    expect(chartEnd(market, now)).toBe(at("2026-10-12T08:00:00Z"));
  });

  test("a market that was closed by its deadline and settled later ends at the deadline", () => {
    const market = marketDetail({
      status: "RESOLVED",
      close_at: "2026-10-14T16:00:00Z",
      // Settling marks the market as closed at the time of the settlement.
      closed_at: "2026-10-20T09:30:00Z",
    });

    expect(chartEnd(market, now)).toBe(at("2026-10-14T16:00:00Z"));
  });
});
