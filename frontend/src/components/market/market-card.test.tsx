import { screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { config, MARKET_ID, marketSummary } from "@/test/fixtures";
import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { MarketCard } from "./market-card";
import { isProposal, StatusBadge, statusName } from "./status-badge";

const QUESTION = "Will the Polybahn run without interruption during the exam session?";

describe("MarketCard", () => {
  test("shows the question, the YES percentage with its chance label, and the volume as sent", () => {
    renderWithProviders(
      <MarketCard market={marketSummary({ p_yes: 0.548, volume_cr: "1250.5" })} />,
    );

    expect(screen.getByRole("heading", { name: QUESTION })).toBeInTheDocument();
    expect(screen.getByText("54.8%")).toBeInTheDocument();
    expect(screen.getByText("chance")).toBeInTheDocument();
    expect(screen.getByText("YES 54.8 percent")).toHaveClass("sr-only");
    expect(screen.getByText("1,250.5")).toBeInTheDocument();
    expect(screen.getByText("Vol.")).toBeInTheDocument();
  });

  test("formats the percentage with the decimals of /config", () => {
    renderWithProviders(<MarketCard market={marketSummary({ p_yes: 0.5483 })} />, {
      config: config({ price_display_decimals: 2 }),
    });

    expect(screen.getByText("54.83%")).toBeInTheDocument();
  });

  test("is one link to the market, with Yes and No as links of their own that open the trade sheet", async () => {
    const { user } = renderWithProviders(<MarketCard market={marketSummary()} />);

    expect(screen.getByRole("link", { name: QUESTION })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}`,
    );
    expect(screen.getByRole("link", { name: `Yes: ${QUESTION}` })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}?buy=YES`,
    );
    expect(screen.getByRole("link", { name: `No: ${QUESTION}` })).toHaveAttribute(
      "href",
      `/markets/${MARKET_ID}?buy=NO`,
    );

    await user.click(screen.getByRole("link", { name: `No: ${QUESTION}` }));
    expect(navigation.href).toBe(`/markets/${MARKET_ID}?buy=NO`);
  });

  test("uses the soft tints for Yes and No, never the full fills", () => {
    renderWithProviders(<MarketCard market={marketSummary()} />);

    const yes = screen.getByRole("link", { name: `Yes: ${QUESTION}` });
    const no = screen.getByRole("link", { name: `No: ${QUESTION}` });
    expect(yes).toHaveClass("bg-yes-soft", "text-yes-text");
    expect(no).toHaveClass("bg-no-soft", "text-no-text");
    expect(yes).not.toHaveClass("bg-yes");
    expect(no).not.toHaveClass("bg-no");
  });

  test("shows the buttons only while trading_open is true, and no badge then", () => {
    renderWithProviders(<MarketCard market={marketSummary({ trading_open: true })} />);

    expect(screen.getAllByRole("link")).toHaveLength(3);
    expect(screen.queryByText("Open")).not.toBeInTheDocument();
  });

  test.each([
    ["HALTED", null, "Paused"],
    ["CLOSED", null, "Closed"],
    ["RESOLVED", "YES", "Resolved YES"],
    ["RESOLVED", "NO", "Resolved NO"],
    ["VOID", "VOID", "Voided"],
  ] as const)("a %s market shows its badge in place of the buttons", (status, outcome, badge) => {
    renderWithProviders(
      <MarketCard market={marketSummary({ status, outcome, trading_open: false })} />,
    );

    expect(screen.getByText(badge)).toBeInTheDocument();
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  test("follows trading_open, not the clock: a market past its close time that the API still calls open keeps its buttons", () => {
    renderWithProviders(
      <MarketCard
        market={marketSummary({ close_at: "2020-01-01T00:00:00Z", trading_open: true })}
      />,
    );

    expect(screen.getByRole("link", { name: `Yes: ${QUESTION}` })).toBeInTheDocument();
  });

  test("replaces the percentage by the outcome once the market is resolved", () => {
    renderWithProviders(
      <MarketCard
        market={marketSummary({
          status: "RESOLVED",
          outcome: "NO",
          trading_open: false,
          p_yes: 0.2,
        })}
      />,
    );

    expect(screen.getByText("NO")).toHaveClass("text-no");
    expect(screen.queryByText("chance")).not.toBeInTheDocument();
    expect(screen.queryByText("20.0%")).not.toBeInTheDocument();
  });

  test("says nothing about how its market is priced: no tier, no liquidity figure, no loss bound", () => {
    vi.useFakeTimers({ now: new Date("2026-10-11T10:00:00Z"), toFake: ["Date"] });
    try {
      const { container } = renderWithProviders(<MarketCard market={marketSummary()} />);

      // All a card says: the question, the chance (once for the eye, once for a screen
      // reader), Yes and No, the volume (likewise twice) and the deadline.
      expect(container.textContent).toBe(
        `${QUESTION}54.8%chanceYES 54.8 percentYesNo1,250.51,250.5 tokensVol.Closes in 3 days`,
      );
      expect(container.textContent).not.toMatch(/tier|liquidity|loss|budget/i);
    } finally {
      vi.useRealTimers();
    }
  });

  test("shows the deadline relative when it is near, and the date alone when trading is over", () => {
    vi.useFakeTimers({ now: new Date("2026-10-11T10:00:00Z"), toFake: ["Date"] });
    try {
      const { unmount } = renderWithProviders(
        <MarketCard market={marketSummary({ close_at: "2026-10-14T16:00:00Z" })} />,
      );
      expect(screen.getByText("Closes in 3 days")).toBeInTheDocument();
      unmount();

      renderWithProviders(
        <MarketCard
          market={marketSummary({
            close_at: "2026-10-14T16:00:00Z",
            status: "CLOSED",
            trading_open: false,
          })}
        />,
      );
      expect(screen.getByText("14 Oct 2026")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("StatusBadge", () => {
  test.each([
    ["OPEN", null, "Open", "bg-raised text-foreground"],
    ["CLOSED", null, "Closed", "bg-raised text-muted-foreground"],
    ["HALTED", null, "Paused", "border-border-strong text-foreground"],
    ["RESOLVED", "YES", "Resolved YES", "bg-yes-soft text-yes-text"],
    ["RESOLVED", "NO", "Resolved NO", "bg-no-soft text-no-text"],
    ["VOID", "VOID", "Voided", "bg-raised text-muted-foreground"],
    ["PROPOSED", null, "In review", "bg-raised text-foreground"],
    ["REJECTED", null, "Not approved", "bg-raised text-muted-foreground"],
    ["EXPIRED", null, "Expired", "bg-raised text-muted-foreground"],
  ] as const)("%s (%s) reads %s", (status, outcome, label, classes) => {
    renderWithProviders(<StatusBadge status={status} outcome={outcome} />);

    const badge = screen.getByText(label);
    expect(badge).toHaveClass(...classes.split(" "), "uppercase", "type-caption");
  });

  test("green and red are for a resolved outcome only", () => {
    for (const status of [
      "OPEN",
      "CLOSED",
      "HALTED",
      "VOID",
      "PROPOSED",
      "REJECTED",
      "EXPIRED",
    ] as const) {
      const { container, unmount } = renderWithProviders(<StatusBadge status={status} />);
      expect(container.innerHTML).not.toMatch(/yes|bg-no|text-no/);
      unmount();
    }
  });

  test("an expired proposal has the quiet look of a finished state: no icon, no outline, muted text", () => {
    const { container } = renderWithProviders(<StatusBadge status="EXPIRED" />);

    const badge = screen.getByText("Expired");
    expect(badge).toHaveAttribute("data-slot", "badge");
    expect(badge).toHaveAttribute("data-variant", "muted");
    expect(badge).not.toHaveClass("border");
    expect(container.querySelector("svg")).not.toBeInTheDocument();
    // The look of the other states that are over: closed, voided, not approved.
    for (const over of ["CLOSED", "VOID", "REJECTED"] as const) {
      const other = renderWithProviders(<StatusBadge status={over} />);
      expect(other.container.querySelector("[data-slot=badge]")).toHaveAttribute(
        "data-variant",
        "muted",
      );
      other.unmount();
    }
  });

  test("an outcome does not colour a status that has none", () => {
    renderWithProviders(<StatusBadge status="EXPIRED" outcome="YES" />);

    expect(screen.getByText("Expired")).toHaveClass("bg-raised", "text-muted-foreground");
    expect(screen.queryByText(/YES/)).not.toBeInTheDocument();
  });

  test("a paused market carries a pause icon beside the word", () => {
    const { container } = renderWithProviders(<StatusBadge status="HALTED" />);

    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  test("the word of each status is the one its badge shows", () => {
    for (const status of [
      "PROPOSED",
      "REJECTED",
      "EXPIRED",
      "OPEN",
      "HALTED",
      "CLOSED",
      "RESOLVED",
      "VOID",
    ] as const) {
      const { container, unmount } = renderWithProviders(<StatusBadge status={status} />);
      expect(container).toHaveTextContent(statusName(status));
      unmount();
    }
    expect(statusName("PROPOSED")).toBe("In review");
    expect(statusName("HALTED")).toBe("Paused");
    expect(statusName("VOID")).toBe("Voided");
    expect(statusName("EXPIRED")).toBe("Expired");
  });
});

describe("isProposal", () => {
  test.each(["PROPOSED", "REJECTED", "EXPIRED"] as const)(
    "a %s market was never opened for trading",
    (status) => {
      expect(isProposal(status)).toBe(true);
    },
  );

  test.each(["OPEN", "HALTED", "CLOSED", "RESOLVED", "VOID"] as const)(
    "a %s market was opened for trading",
    (status) => {
      expect(isProposal(status)).toBe(false);
    },
  );
});
