import { act, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { REDUCED_MOTION_QUERY } from "@/lib/design";
import { config, marketSummary } from "@/test/fixtures";
import { setMediaQuery } from "@/test/media";
import { renderWithProviders } from "@/test/render";

import { Price } from "./price";

const flash = () => document.querySelector("[data-slot=flash]")!;

describe("Price", () => {
  test("is white for a live market, with the chance label: colour belongs to the controls", () => {
    renderWithProviders(<Price market={marketSummary({ p_yes: 0.548 })} />);

    const figure = screen.getByText("54.8%").parentElement!;
    expect(figure).toHaveClass("text-foreground", "type-number-lg");
    expect(figure.className).not.toMatch(/yes|text-no/);
    expect(screen.getByText("chance")).toHaveClass("type-caption", "text-muted-foreground");
  });

  test("is the display size on Market detail, with the label beside it", () => {
    renderWithProviders(<Price market={marketSummary()} size="detail" />);

    expect(screen.getByText("54.8%").parentElement).toHaveClass("type-display");
    expect(document.querySelector("[data-slot=price]")).toHaveClass("items-baseline");
  });

  test("reads as YES and the number in percent", () => {
    renderWithProviders(<Price market={marketSummary({ p_yes: 0.548 })} />);

    expect(screen.getByText("YES 54.8 percent")).toHaveClass("sr-only");
    expect(screen.getByText("54.8%").parentElement).toHaveAttribute("aria-hidden", "true");
  });

  test("uses the decimals of /config", () => {
    renderWithProviders(<Price market={marketSummary({ p_yes: 0.5 })} />, {
      config: config({ price_display_decimals: 0 }),
    });

    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  test.each([
    ["YES", "text-yes"],
    ["NO", "text-no"],
  ] as const)("a market resolved %s shows the outcome in its colour", (outcome, colour) => {
    renderWithProviders(
      <Price market={marketSummary({ status: "RESOLVED", outcome, p_yes: 0.9 })} size="detail" />,
    );

    const figure = document.querySelector("[data-slot=price]")!;
    expect(figure).toHaveTextContent(`Resolved ${outcome}`);
    expect(figure).toHaveClass(colour, "type-display");
    expect(screen.queryByText("chance")).not.toBeInTheDocument();
  });

  test("a voided market keeps its last percentage, in white", () => {
    renderWithProviders(
      <Price market={marketSummary({ status: "VOID", outcome: "VOID", p_yes: 0.31 })} />,
    );

    expect(screen.getByText("31.0%").parentElement).toHaveClass("text-foreground");
  });

  test("flashes when the price changes, not when it first appears", () => {
    setMediaQuery(REDUCED_MOTION_QUERY, false);
    const { rerender } = renderWithProviders(<Price market={marketSummary({ p_yes: 0.548 })} />);
    expect(flash().className).not.toContain("animate-price-flash");

    rerender(<Price market={marketSummary({ p_yes: 0.559 })} />);

    expect(screen.getByText("55.9%")).toBeInTheDocument();
    expect(flash().className).toContain("motion-safe:animate-price-flash");
  });

  test("does not flash for a new event that shows the same figure", () => {
    const { rerender } = renderWithProviders(<Price market={marketSummary({ p_yes: 0.548 })} />);

    rerender(<Price market={marketSummary({ p_yes: 0.548, version: 8 })} />);

    expect(flash().className).not.toContain("animate-price-flash");
  });

  test("a live price is announced politely, at most once per ten seconds", async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = renderWithProviders(
        <Price market={marketSummary({ p_yes: 0.548 })} live />,
      );
      const region = document.querySelector("[aria-live=polite]")!;
      expect(region).toHaveTextContent("YES 54.8 percent");

      // The first change is announced at once.
      rerender(<Price market={marketSummary({ p_yes: 0.55 })} live />);
      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(region).toHaveTextContent("YES 55.0 percent");

      // Changes inside the next ten seconds wait, and only the latest is spoken.
      rerender(<Price market={marketSummary({ p_yes: 0.56 })} live />);
      rerender(<Price market={marketSummary({ p_yes: 0.57 })} live />);
      await act(() => vi.advanceTimersByTimeAsync(9_000));
      expect(region).toHaveTextContent("YES 55.0 percent");
      // The figure on screen does not wait.
      expect(screen.getByText("57.0%")).toBeInTheDocument();

      await act(() => vi.advanceTimersByTimeAsync(1_100));
      expect(region).toHaveTextContent("YES 57.0 percent");
    } finally {
      vi.useRealTimers();
    }
  });
});
