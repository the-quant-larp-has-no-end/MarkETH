import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { sizeCharts } from "@/test/charts";
import { config, history, marketDetail } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { ProbabilityChart } from "./probability-chart";

type Point = { x: number; y: number };

const chart = () => screen.getByRole("application");
const line = () => document.querySelector(".recharts-area-curve")!;
const fill = () => document.querySelector(".recharts-area-area")!;
const gridlines = () => [...document.querySelectorAll(".recharts-cartesian-grid-horizontal line")];

/** The corners of a path made of straight pieces: `M0,92L33,92L33,88`. */
function corners(path: Element): Point[] {
  return [...path.getAttribute("d")!.matchAll(/[ML]([\d.]+),([\d.]+)/g)].map((match) => ({
    x: Number(match[1]),
    y: Number(match[2]),
  }));
}

/** A finger or a pointer over the plot, `x` px from its left edge. The chart answers a frame later. */
function point(x: number) {
  fireEvent.mouseMove(document.querySelector(".recharts-wrapper")!, { clientX: x, clientY: 60 });
}

const OPENING = [{ t: "2026-10-01T12:00:00Z", p_yes: 0.5, version: 0 }];

/** `until` of the history: the server's now, ten days after the market opened. */
const UNTIL = "2026-10-11T10:00:00Z";

beforeEach(() => {
  sizeCharts();
  // The clock of the device, which is wrong by years: the chart does not read it.
  vi.useFakeTimers({ now: new Date("2031-03-01T00:00:00Z"), toFake: ["Date"] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ProbabilityChart", () => {
  describe("the line", () => {
    test("is a step line: each trade holds its price until the next", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const path = corners(line());
      expect(path.length).toBeGreaterThan(4);
      for (let index = 1; index < path.length; index += 1) {
        const from = path[index - 1]!;
        const to = path[index]!;
        // Every piece runs level or straight up and down, never at a slant.
        expect(from.x === to.x || from.y === to.y).toBe(true);
      }
      // The price is held first, and only then does the line move to the next one.
      expect(path[0]!.y).toBe(path[1]!.y);
      expect(path[1]!.x).toBe(path[2]!.x);
    });

    test("is 2 px in the YES colour, which is a token", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(line()).toHaveAttribute("stroke-width", "2");
      expect(line()).toHaveAttribute("stroke", "var(--color-p_yes)");
      expect(line()).toHaveAttribute("fill", "none");
      expect(document.querySelector("[data-slot=chart] style")!.textContent).toContain(
        "--color-p_yes: var(--chart-1);",
      );
    });

    test("has a flat fill of the same colour at 12% opacity under it, and no gradient", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(fill()).toHaveAttribute("fill", "var(--color-p_yes)");
      expect(fill()).toHaveAttribute("fill-opacity", "0.12");
      expect(fill()).toHaveAttribute("stroke", "none");
      expect(chart().querySelector("linearGradient, radialGradient, stop")).toBeNull();
      expect(chart().innerHTML).not.toMatch(/url\(#(?!recharts\d+-clip)/);
    });

    test("starts at the opening point the API includes", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const half = Number(gridlines()[1]!.getAttribute("y1"));
      // At the left edge of the plot, on the 50% line.
      expect(corners(line())[0]).toEqual({ x: 0, y: half });
    });

    test("runs to the right edge of the plot: the last price holds up to until", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const path = corners(line());
      const edge = Number(gridlines()[0]!.getAttribute("x2"));
      expect(path.at(-1)!.x).toBe(edge);
      expect(path.at(-1)!.y).toBe(path.at(-2)!.y);
    });

    test("a market nobody has traded yet shows a flat line at its opening price, not an empty box", () => {
      renderWithProviders(
        <ProbabilityChart points={OPENING} until={UNTIL} market={marketDetail({ p_yes: 0.5 })} />,
      );

      const path = corners(line());
      const half = Number(gridlines()[1]!.getAttribute("y1"));
      const edge = Number(gridlines()[0]!.getAttribute("x2"));
      expect(path[0]).toEqual({ x: 0, y: half });
      expect(path.at(-1)).toEqual({ x: edge, y: half });
      expect(new Set(path.map((corner) => corner.y)).size).toBe(1);
    });

    test("a point that arrives extends the line and leaves what was drawn where it was", () => {
      const market = marketDetail();
      const { rerender } = renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={market} />,
      );
      const before = corners(line());

      rerender(
        <ProbabilityChart
          points={[...history().points, { t: "2026-10-05T08:00:00Z", p_yes: 0.61, version: 8 }]}
          until={UNTIL}
          market={{ ...market, p_yes: 0.61 }}
        />,
      );

      const after = corners(line());
      expect(after.length).toBeGreaterThan(before.length);
      // Everything up to the last trade that was already there is unchanged.
      expect(after.slice(0, before.length - 2)).toEqual(before.slice(0, before.length - 2));
    });

    test("a point that arrives after until moves the right edge to itself: the line never ends before its last point", () => {
      const lastDate = () =>
        [...chart().querySelectorAll(".recharts-xAxis-tick-labels text")].at(-1)?.textContent;
      const { rerender } = renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );
      expect(lastDate()).toBe("11 Oct 2026");

      // The stream brought a trade two days after the history was read.
      rerender(
        <ProbabilityChart
          points={[...history().points, { t: "2026-10-13T08:00:00Z", p_yes: 0.61, version: 8 }]}
          until={UNTIL}
          market={marketDetail({ p_yes: 0.61 })}
        />,
      );

      expect(lastDate()).toBe("13 Oct 2026");
      const path = corners(line());
      const edge = Number(gridlines()[0]!.getAttribute("x2"));
      expect(path.at(-1)!.x).toBe(edge);
    });

    test("a market whose trading is over is drawn until the moment the server names, which is when trading ended", () => {
      renderWithProviders(
        <ProbabilityChart
          points={history().points}
          until="2026-10-05T16:00:00Z"
          market={marketDetail({
            status: "CLOSED",
            // Neither of the market's own times is read for the chart.
            close_at: "2026-10-09T16:00:00Z",
            closed_at: "2026-10-08T08:00:00Z",
          })}
        />,
      );

      expect(screen.getByText("5 Oct 2026")).toBeInTheDocument();
      expect(screen.queryByText("8 Oct 2026")).not.toBeInTheDocument();
      expect(screen.queryByText("9 Oct 2026")).not.toBeInTheDocument();
    });

    test("ends at until whatever the clock of the device says, and nothing moves it on", async () => {
      const dates = () =>
        [...chart().querySelectorAll(".recharts-xAxis-tick-labels text")].map(
          (label) => label.textContent,
        );
      vi.useRealTimers();
      vi.useFakeTimers({ now: new Date("2026-10-04T10:00:30Z") });
      const { rerender } = renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );
      const before = line().getAttribute("d");
      expect(dates()).toEqual(["1 Oct 2026", "11 Oct 2026"]);

      // Hours pass on the device, and the chart is drawn again.
      await vi.advanceTimersByTimeAsync(5 * 60 * 60 * 1000);
      rerender(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(dates()).toEqual(["1 Oct 2026", "11 Oct 2026"]);
      expect(line().getAttribute("d")).toBe(before);
    });

    test("another until is another right edge", () => {
      const lastDate = () =>
        [...chart().querySelectorAll(".recharts-xAxis-tick-labels text")].at(-1)?.textContent;
      const { rerender } = renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );
      expect(lastDate()).toBe("11 Oct 2026");

      // The history was read again: the server's now has moved on.
      rerender(
        <ProbabilityChart
          points={history().points}
          until="2026-10-13T09:00:00Z"
          market={marketDetail()}
        />,
      );

      expect(lastDate()).toBe("13 Oct 2026");
    });
  });

  describe("the axes", () => {
    test("the y axis is fixed from 0% to 100%, whatever the prices are", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const labels = [...chart().querySelectorAll(".recharts-yAxis-tick-labels text")].map(
        (label) => label.textContent,
      );
      expect(labels).toEqual(["0%", "25%", "50%", "75%", "100%"]);
    });

    test("has gridlines at 25, 50 and 75%, and no others", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(gridlines()).toHaveLength(3);
      expect(document.querySelector(".recharts-cartesian-grid-vertical")).toBeNull();
      // Evenly apart: a quarter of the axis each.
      const [quarter, half, threeQuarters] = gridlines().map((gridline) =>
        Number(gridline.getAttribute("y1")),
      );
      expect(quarter! - half!).toBe(half! - threeQuarters!);
    });

    test("the gridlines and the labels take their colours from the tokens", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const frame = document.querySelector("[data-slot=chart]")!;
      expect(frame).toHaveClass(
        "type-caption",
        "[&_.recharts-cartesian-grid_line]:stroke-border",
        "[&_.recharts-cartesian-axis-tick-value]:fill-muted-foreground",
      );
    });

    test("the x axis shows the first and the last date only, in the timezone of /config", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const labels = [...chart().querySelectorAll(".recharts-xAxis-tick-labels text")];
      expect(labels.map((label) => label.textContent)).toEqual(["1 Oct 2026", "11 Oct 2026"]);
      // The first stands at the left edge and the last at the right, both inside the plot.
      expect(labels[0]).toHaveAttribute("text-anchor", "start");
      expect(labels[1]).toHaveAttribute("text-anchor", "end");
    });

    test("a chart of one day shows the time with the date at both ends", () => {
      renderWithProviders(
        <ProbabilityChart points={OPENING} until="2026-10-01T15:30:00Z" market={marketDetail()} />,
      );

      const labels = [...chart().querySelectorAll(".recharts-xAxis-tick-labels text")];
      expect(labels.map((label) => label.textContent)).toEqual([
        "1 Oct 2026, 14:00",
        "1 Oct 2026, 17:30",
      ]);
    });

    test("is 200 px tall on a phone and 280 px from 768 px", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(document.querySelector("[data-slot=chart]")).toHaveClass("h-50", "md:h-70", "w-full");
    });
  });

  describe("reading a point", () => {
    test("touching the chart shows a vertical guide and the percentage and time of the nearest point", async () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );
      expect(document.querySelector(".recharts-tooltip-cursor")).toBeNull();

      point(34);

      const readout = document.querySelector(".recharts-tooltip-wrapper") as HTMLElement;
      expect(await within(readout).findByText("52.4% YES")).toBeInTheDocument();
      expect(within(readout).getByText("2 Oct 2026, 11:30")).toBeInTheDocument();
      expect(readout.querySelector("time")).toHaveAttribute("datetime", "2026-10-02T09:30:00.000Z");
      // The guide is one upright line.
      const guide = corners(document.querySelector(".recharts-tooltip-cursor")!);
      expect(guide).toHaveLength(2);
      expect(guide[0]!.x).toBe(guide[1]!.x);
    });

    test("the readout puts the value first, on the card surface, with no colour of its own", async () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      point(34);

      const readout = document.querySelector(".recharts-tooltip-wrapper")!;
      const value = await within(readout as HTMLElement).findByText("52.4% YES");
      expect(value).toHaveClass("type-number", "text-foreground");
      expect(value.compareDocumentPosition(readout.querySelector("time")!)).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
      expect(readout.firstElementChild).toHaveClass("bg-card", "border", "rounded-md");
    });

    test("the arrow keys read the points for a keyboard, and the chart shows its focus", async () => {
      const { user } = renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );
      expect(chart()).toHaveAttribute("tabindex", "0");

      await user.tab();
      expect(chart()).toHaveFocus();
      await user.keyboard("{ArrowRight}");

      const readout = document.querySelector(".recharts-tooltip-wrapper") as HTMLElement;
      expect(within(readout).getByText(/% YES$/)).toBeInTheDocument();
      expect(document.querySelector("[data-slot=chart]")!.className).toContain(
        "[&_.recharts-surface:focus-visible]:outline-ring",
      );
    });

    test("formats the percentage with the decimals of /config", async () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
        {
          config: config({ price_display_decimals: 2 }),
        },
      );

      point(34);

      expect(await screen.findByText("52.40% YES")).toBeInTheDocument();
      expect(screen.getByText("Opened at 50.00%, now 54.80%.")).toBeInTheDocument();
      // The fixed axis keeps its whole percentages.
      expect(screen.getByText("50%")).toBeInTheDocument();
    });
  });

  describe("without sight of it", () => {
    test("is labelled with a summary: what it shows, the price now and the opening price", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      expect(
        screen.getByRole("application", {
          name: "YES probability over time, now 54.8 percent, opened at 50.0 percent",
        }),
      ).toBeInTheDocument();
    });

    test("the same information stands under it as text", () => {
      renderWithProviders(
        <ProbabilityChart points={history().points} until={UNTIL} market={marketDetail()} />,
      );

      const caption = screen.getByText("Opened at 50.0%, now 54.8%.");
      expect(caption.tagName).toBe("FIGCAPTION");
      expect(caption).toBeVisible();
      expect(caption.closest("figure")).toContainElement(chart());
    });

    test.each(["CLOSED", "RESOLVED", "VOID"] as const)(
      "a %s market stands still: the summary says what it closed at",
      (status) => {
        renderWithProviders(
          <ProbabilityChart
            points={history().points}
            until="2026-10-05T16:00:00Z"
            market={marketDetail({ status })}
          />,
        );

        expect(chart()).toHaveAccessibleName(
          "YES probability over time, closed at 54.8 percent, opened at 50.0 percent",
        );
        expect(screen.getByText("Opened at 50.0%, closed at 54.8%.")).toBeInTheDocument();
      },
    );

    test("a paused market can still move: its figure is the price now", () => {
      renderWithProviders(
        <ProbabilityChart
          points={history().points}
          until={UNTIL}
          market={marketDetail({ status: "HALTED" })}
        />,
      );

      expect(screen.getByText("Opened at 50.0%, now 54.8%.")).toBeInTheDocument();
    });

    test("now is the market's own price, as the API sent it", () => {
      // A trade was answered a moment ago; its point has not reached the history yet.
      renderWithProviders(
        <ProbabilityChart
          points={history().points}
          until={UNTIL}
          market={marketDetail({ p_yes: 0.559 })}
        />,
      );

      expect(screen.getByText("Opened at 50.0%, now 55.9%.")).toBeInTheDocument();
      expect(chart()).toHaveAccessibleName(
        "YES probability over time, now 55.9 percent, opened at 50.0 percent",
      );
    });
  });

  test("with no point at all it says so and draws nothing", () => {
    renderWithProviders(<ProbabilityChart points={[]} until={UNTIL} market={marketDetail()} />);

    expect(screen.getByText("No trades to chart yet.")).toBeInTheDocument();
    expect(screen.queryByRole("application")).not.toBeInTheDocument();
  });
});
