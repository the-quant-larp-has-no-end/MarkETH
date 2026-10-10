import { screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { navigation } from "@/test/navigation";
import { renderWithProviders } from "@/test/render";

import { FilterChips, RemovableChip } from "./filter-chips";
import { LinkTabs } from "./link-tabs";
import { SegmentedControl } from "./segmented-control";

describe("SegmentedControl", () => {
  const segments = [
    { value: "BUY", label: "Buy" },
    { value: "SELL", label: "Sell" },
  ] as const;

  test("marks the selected segment and changes on tap", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <SegmentedControl label="Trade side" value="BUY" onChange={onChange} segments={segments} />,
    );

    const group = screen.getByRole("radiogroup", { name: "Trade side" });
    const buy = within(group).getByRole("radio", { name: "Buy" });
    const sell = within(group).getByRole("radio", { name: "Sell" });
    expect(buy).toBeChecked();
    expect(sell).not.toBeChecked();
    expect(group).toHaveClass("h-12", "rounded-full", "bg-background");

    await user.click(sell);
    expect(onChange).toHaveBeenCalledWith("SELL");
  });

  test("always keeps one segment selected", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <SegmentedControl label="Trade side" value="BUY" onChange={onChange} segments={segments} />,
    );

    await user.click(screen.getByRole("radio", { name: "Buy" }));

    expect(onChange).not.toHaveBeenCalled();
  });

  test("a segment can be switched off: Sell while there is nothing to sell", () => {
    renderWithProviders(
      <SegmentedControl
        label="Trade side"
        value="BUY"
        onChange={() => {}}
        segments={[
          { value: "BUY", label: "Buy" },
          { value: "SELL", label: "Sell", disabled: true },
        ]}
      />,
    );

    expect(screen.getByRole("radio", { name: "Sell" })).toBeDisabled();
  });
});

describe("FilterChips", () => {
  const chips = [
    { value: "open", label: "Open" },
    { value: "closed", label: "Closed" },
    { value: "all", label: "All" },
  ] as const;

  test("is a group of chips of which one is pressed", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <FilterChips label="Status" value="open" onChange={onChange} chips={chips} />,
    );

    const group = screen.getByRole("group", { name: "Status" });
    expect(within(group).getByRole("button", { name: "Open" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(group).getByRole("button", { name: "Closed" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    for (const chip of within(group).getAllByRole("button")) expect(chip).toHaveClass("h-11");

    await user.click(within(group).getByRole("button", { name: "All" }));
    expect(onChange).toHaveBeenCalledWith("all");
  });

  test("the pressed chip stays pressed when it is tapped again", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <FilterChips label="Status" value="open" onChange={onChange} chips={chips} />,
    );

    await user.click(screen.getByRole("button", { name: "Open" }));

    expect(onChange).not.toHaveBeenCalled();
  });

  test("can carry controls at the end of its row", () => {
    renderWithProviders(
      <FilterChips label="Sort by" value="open" onChange={() => {}} chips={chips}>
        <button type="button">Filter</button>
      </FilterChips>,
    );

    expect(screen.getByRole("button", { name: "Filter" })).toBeInTheDocument();
    expect(
      within(screen.getByRole("group", { name: "Sort by" })).queryByRole("button", {
        name: "Filter",
      }),
    ).not.toBeInTheDocument();
  });
});

describe("FilterChips on a narrow row", () => {
  const chips = [
    { value: "credits", label: "Tokens" },
    { value: "win_rate", label: "Win rate" },
    { value: "trades", label: "Trades" },
    { value: "payout", label: "Payout" },
  ] as const;

  /** The strip ends at 200 px; the pressed chip lies where the test says. */
  function layOut(pressed: { left: number; right: number }) {
    const box = (left: number, right: number) =>
      ({ left, right, top: 0, bottom: 44, width: right - left, height: 44 }) as DOMRect;
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (
      this: Element,
    ) {
      if (this.getAttribute("aria-pressed") === "true") return box(pressed.left, pressed.right);
      if (this.getAttribute("role") === "group") return box(16, 200);
      return box(0, 0);
    });
  }

  test("a row that opens on a chip beyond its strip scrolls the strip to it, and never the page", () => {
    layOut({ left: 300, right: 380 });
    const intoView = vi.spyOn(Element.prototype, "scrollIntoView");

    renderWithProviders(
      <FilterChips label="Sort by" value="payout" onChange={() => {}} chips={chips} />,
    );

    expect(screen.getByRole("group", { name: "Sort by" }).scrollLeft).toBe(180);
    expect(intoView).not.toHaveBeenCalled();
  });

  test("a chip that lies before the strip is brought back in, and one in sight moves nothing", () => {
    layOut({ left: -40, right: 40 });
    const { rerender } = renderWithProviders(
      <FilterChips label="Sort by" value="credits" onChange={() => {}} chips={chips} />,
    );
    const strip = screen.getByRole("group", { name: "Sort by" });
    expect(strip.scrollLeft).toBe(-56);

    strip.scrollLeft = 0;
    layOut({ left: 60, right: 140 });
    rerender(<FilterChips label="Sort by" value="win_rate" onChange={() => {}} chips={chips} />);

    expect(strip.scrollLeft).toBe(0);
  });
});

describe("RemovableChip", () => {
  test("is never wider than its row: a long label is cut, and the whole of it is its name", () => {
    const programme = "MSc Spatial Development and Infrastructure Systems";
    renderWithProviders(<RemovableChip onRemove={() => {}}>{programme}</RemovableChip>);

    const chip = screen.getByRole("button", { name: `Remove filter: ${programme}` });
    expect(chip).toHaveClass("max-w-full");
    expect(chip).toHaveAttribute("title", programme);
    expect(within(chip).getByText(programme)).toHaveClass("truncate", "min-w-0");
  });

  test("states a filter and removes it on tap", async () => {
    const onRemove = vi.fn();
    const { user } = renderWithProviders(
      <RemovableChip onRemove={onRemove}>Win rate from 50%</RemovableChip>,
    );

    const chip = screen.getByRole("button", { name: "Remove filter: Win rate from 50%" });
    expect(chip).toHaveTextContent("Win rate from 50%");

    await user.click(chip);
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});

describe("LinkTabs", () => {
  const tabs = [
    { label: "Markets", href: "/admin", matches: (path: string) => path === "/admin" },
    { label: "Codes", href: "/admin/codes" },
    { label: "Risk", href: "/admin/risk" },
  ];

  test("each tab is a link to its route, and the current one is marked", async () => {
    navigation.go("/admin/codes");
    const { user } = renderWithProviders(
      <LinkTabs label="Admin" tabs={tabs}>
        <a href="/barista">Scan vouchers</a>
      </LinkTabs>,
    );

    const nav = screen.getByRole("navigation", { name: "Admin" });
    expect(within(nav).getByRole("link", { name: "Codes" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Markets" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "Scan vouchers" })).toBeInTheDocument();

    await user.click(within(nav).getByRole("link", { name: "Risk" }));

    expect(navigation.pathname).toBe("/admin/risk");
    expect(within(nav).getByRole("link", { name: "Risk" })).toHaveAttribute("aria-current", "page");
  });
});
