import { afterEach, describe, expect, test, vi } from "vitest";

import { history } from "@/test/fixtures";

import { buildSeries } from "./chart-series";

const at = (iso: string) => Date.parse(iso);
const MINUTE = 60_000;

afterEach(() => {
  vi.useRealTimers();
});

describe("buildSeries", () => {
  test("keeps the history's points in the order and with the prices the API sent", () => {
    const series = buildSeries(history().points, "2026-10-03T17:45:00Z")!;

    expect(series.points).toEqual([
      { at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 },
      { at: at("2026-10-02T09:30:00Z"), p_yes: 0.524 },
      { at: at("2026-10-03T17:45:00Z"), p_yes: 0.548 },
    ]);
  });

  test("starts at the opening point", () => {
    const series = buildSeries(history().points, "2026-10-11T10:00:00Z")!;

    expect(series.start).toBe(at("2026-10-01T12:00:00Z"));
    expect(series.points[0]).toEqual({ at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 });
  });

  test("ends at until, the right edge the server names: the point there repeats the last price", () => {
    const until = "2026-10-11T10:00:00Z";

    const series = buildSeries(history().points, until)!;

    expect(series.end).toBe(at(until));
    expect(series.points).toHaveLength(4);
    expect(series.points.at(-1)).toEqual({ at: at(until), p_yes: 0.548 });
  });

  test("reads until as the instant it is, whatever offset it is written with", () => {
    const series = buildSeries(history().points, "2026-10-11T12:00:00+02:00")!;

    expect(series.end).toBe(at("2026-10-11T10:00:00Z"));
  });

  test("reads no clock of the device: the same answer gives the same chart at any time", () => {
    vi.useFakeTimers({ now: new Date("2026-10-04T10:00:00Z"), toFake: ["Date"] });
    const early = buildSeries(history().points, history().until)!;

    // A year on, and on a device whose clock is wrong.
    vi.setSystemTime(new Date("2031-01-01T00:00:00Z"));
    const late = buildSeries(history().points, history().until)!;

    expect(late).toEqual(early);
    expect(late.end).toBe(at("2026-10-04T10:00:00Z"));
  });

  test("a market nobody has traded yet is a flat line from its opening point, not an empty box", () => {
    const opening = [{ t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 }];
    const until = "2026-10-04T12:00:00Z";

    const series = buildSeries(opening, until)!;

    expect(series.points).toEqual([
      { at: at("2026-10-01T12:00:00Z"), p_yes: 0.5 },
      { at: at(until), p_yes: 0.5 },
    ]);
  });

  test("a market read in the minute it was opened still has a line: the chart is at least a minute wide", () => {
    const opening = [{ t: "2026-10-01T12:00:20Z", p_yes: 0.5, version: 0 }];
    const start = at("2026-10-01T12:00:20Z");

    const series = buildSeries(opening, "2026-10-01T12:00:21Z")!;

    expect(series.start).toBe(start);
    expect(series.end).toBe(start + MINUTE);
    expect(series.points).toEqual([
      { at: start, p_yes: 0.5 },
      { at: start + MINUTE, p_yes: 0.5 },
    ]);
  });

  test("never ends before its last point: one that arrived after the history was read moves the edge to itself", () => {
    // The stream brought a trade of 5 October; the history was read on the 4th.
    const points = [...history().points, { t: "2026-10-05T08:00:00Z", p_yes: 0.61, version: 8 }];

    const series = buildSeries(points, "2026-10-04T10:00:00Z")!;

    expect(series.end).toBe(at("2026-10-05T08:00:00Z"));
    expect(series.points).toHaveLength(4);
    expect(series.points.at(-1)).toEqual({ at: at("2026-10-05T08:00:00Z"), p_yes: 0.61 });
  });

  test("an until that cannot be read leaves the chart at its last point", () => {
    const series = buildSeries(history().points, "soon")!;

    expect(series.end).toBe(at("2026-10-03T17:45:00Z"));
    expect(series.points).toHaveLength(3);
  });

  test("a point is drawn no earlier than the one before it", () => {
    const series = buildSeries(
      [
        { t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 },
        { t: "2026-10-03T12:00:00Z", p_yes: 0.6, version: 1 },
        // The stream stamps its first event with the time of the connection.
        { t: "2026-10-02T12:00:00Z", p_yes: 0.55, version: 2 },
      ],
      "2026-10-03T12:00:00Z",
    )!;

    expect(series.points.map((point) => point.at)).toEqual([
      at("2026-10-01T12:00:00Z"),
      at("2026-10-03T12:00:00Z"),
      at("2026-10-03T12:00:00Z"),
    ]);
    expect(series.points.map((point) => point.p_yes)).toEqual([0.5, 0.6, 0.55]);
  });

  test("leaves out a point whose time cannot be read, and is null without any point", () => {
    expect(buildSeries([], "2026-10-11T10:00:00Z")).toBeNull();
    expect(buildSeries([{ t: "soon", p_yes: 0.5, version: 0 }], "2026-10-11T10:00:00Z")).toBeNull();
  });

  test("invents no price: every p_yes of the series is one the API sent", () => {
    const sent = new Set(history().points.map((point) => point.p_yes));

    const series = buildSeries(history().points, "2026-10-11T10:00:00Z")!;

    for (const point of series.points) expect(sent.has(point.p_yes)).toBe(true);
  });
});
