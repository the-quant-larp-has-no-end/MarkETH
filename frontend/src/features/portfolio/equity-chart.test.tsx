import { screen, within } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import { sizeCharts } from "@/test/charts";
import { portfolio } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { EquityChart } from "./equity-chart";

beforeEach(() => sizeCharts());

test("uses the market chart's responsive step line and fill with a token-value axis", () => {
  renderWithProviders(<EquityChart portfolio={portfolio()} />);
  const chart = screen.getByRole("application", { name: /Total equity over time in tokens/ });
  expect(document.querySelector("[data-slot=chart]")).toHaveClass("h-50", "md:h-70");
  expect(chart.querySelector(".recharts-area-curve")).toHaveAttribute("stroke-width", "2");
  expect(chart.querySelector(".recharts-area-curve")).toHaveAttribute(
    "stroke",
    "var(--color-plot_value)",
  );
  expect(chart.querySelector(".recharts-area-area")).toHaveAttribute("fill-opacity", "0.12");
  expect(chart.querySelector(".recharts-yAxis-tick-labels")).not.toHaveTextContent("%");
  const dates = [...chart.querySelectorAll(".recharts-xAxis-tick-labels text")];
  expect(dates.map((date) => date.textContent)).toEqual(["1 Oct 2026", "11 Oct 2026"]);
  expect(screen.getByText("Started at 200 tokens, now 200.22 tokens.")).toBeInTheDocument();
});

test("a keyboard tooltip formats the exact amount, independently of its plotting coordinate", async () => {
  const { user } = renderWithProviders(
    <EquityChart
      portfolio={portfolio({
        total_equity_cr: "123456789012345678901234.999999",
        equity_history: [
          {
            t: "2026-10-01T12:00:00Z",
            total_equity_cr: "123456789012345678901234.999999",
            plot_value: 123456,
          },
        ],
      })}
    />,
  );
  screen.getByRole("application").focus();
  await user.keyboard("{ArrowRight}");
  const readout = document.querySelector(".recharts-tooltip-wrapper") as HTMLElement;
  expect(
    await within(readout).findByText("123,456,789,012,345,678,901,234.99 tokens"),
  ).toBeInTheDocument();
});

test("a portfolio with zero equity still gets a flat line", () => {
  renderWithProviders(
    <EquityChart
      portfolio={portfolio({
        total_equity_cr: "0",
        equity_history: [{ t: "2026-10-01T12:00:00Z", total_equity_cr: "0", plot_value: 0 }],
      })}
    />,
  );
  const line = document.querySelector(".recharts-area-curve")!;
  expect(line.getAttribute("d")).toMatch(/^M.+L/);
  expect(screen.getByRole("application")).toHaveAccessibleName(/now 0 tokens/);
});

test("missing history has a readable empty state", () => {
  renderWithProviders(<EquityChart portfolio={portfolio({ equity_history: [] })} />);
  expect(screen.getByText("No equity history yet.")).toBeInTheDocument();
  expect(screen.queryByRole("application")).not.toBeInTheDocument();
});
