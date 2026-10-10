import { screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { config, marketViewer, marketViewerWithPosition, quote } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { OutcomeButtons, OutcomeToggle } from "./outcome-buttons";
import { SellPresets, StakeLeftLine, StakePresets } from "./presets";
import { QuotePanel } from "./quote-panel";

describe("OutcomeButtons", () => {
  test("show each outcome with the price the API sent for it, YES on the left", async () => {
    const onSelect = vi.fn();
    // Two prices that do not add up to one: neither is worked out from the other.
    const { user } = renderWithProviders(
      <OutcomeButtons pYes={0.548} pNo={0.4} onSelect={onSelect} />,
    );

    const [yes, no] = screen.getAllByRole("button");
    expect(yes).toHaveTextContent("YES 54.8%");
    expect(no).toHaveTextContent("NO 40.0%");
    expect(yes).toHaveClass("bg-yes", "text-ink");
    expect(no).toHaveClass("bg-no", "text-ink");

    await user.click(no!);
    expect(onSelect).toHaveBeenCalledWith("NO");
  });

  test("are disabled when the viewer cannot trade", () => {
    renderWithProviders(<OutcomeButtons pYes={0.5} pNo={0.5} disabled onSelect={() => {}} />);

    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });
});

describe("OutcomeToggle", () => {
  test("has two segments with aria-pressed; the selected one is filled in its colour", () => {
    renderWithProviders(<OutcomeToggle value="YES" onChange={() => {}} />);

    const yes = screen.getByRole("button", { name: "YES" });
    const no = screen.getByRole("button", { name: "NO" });
    expect(screen.getByRole("group", { name: "Outcome" })).toContainElement(yes);
    expect(yes).toHaveAttribute("aria-pressed", "true");
    expect(no).toHaveAttribute("aria-pressed", "false");
    expect(yes.className).toContain("data-[state=on]:bg-yes");
    expect(no.className).toContain("data-[state=on]:bg-no");
    expect(yes.className).toContain("data-[state=on]:text-ink");
  });

  test("changes the outcome, and cannot be switched off by tapping the selected segment", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(<OutcomeToggle value="YES" onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "YES" }));
    expect(onChange).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "NO" }));
    expect(onChange).toHaveBeenCalledWith("NO");
  });

  test("offers only the outcomes it is told to: selling what is not held is not offered", () => {
    renderWithProviders(<OutcomeToggle value="YES" onChange={() => {}} disabled={{ NO: true }} />);

    expect(screen.getByRole("button", { name: "YES" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "NO" })).toBeDisabled();
  });

  test("can start with neither outcome chosen, and is then named and described by what stands around it", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <>
        <span id="outcome-label">Outcome of the market</span>
        <OutcomeToggle
          value={null}
          onChange={onChange}
          labelledBy="outcome-label"
          describedBy="outcome-error"
        />
        <p id="outcome-error">Choose the outcome.</p>
      </>,
    );

    const group = screen.getByRole("group", { name: "Outcome of the market" });
    expect(group).toHaveAccessibleDescription("Choose the outcome.");
    expect(group).not.toHaveAttribute("aria-label");
    for (const segment of within(group).getAllByRole("button")) {
      expect(segment).toHaveAttribute("aria-pressed", "false");
    }

    await user.click(within(group).getByRole("button", { name: "NO" }));
    expect(onChange).toHaveBeenCalledWith("NO");
  });
});

describe("StakePresets", () => {
  test("show one chip per preset of the market, each with its amount as sent", () => {
    renderWithProviders(
      <StakePresets presets={marketViewer().stake_presets} value={null} onChange={() => {}} />,
    );

    const chips = within(screen.getByRole("group", { name: "Stake" })).getAllByRole("button");
    expect(chips.map((chip) => chip.textContent)).toEqual([
      "2525 tokens",
      "5050 tokens",
      "100100 tokens",
    ]);
    for (const chip of chips) expect(chip).toHaveAttribute("aria-pressed", "false");
  });

  test("a preset is disabled by allowed alone: the frontend looks at no balance and no limit", async () => {
    const onChange = vi.fn();
    const presets = [
      // The server says no to the smallest and yes to the largest: that is what counts.
      { amount_cr: "25", allowed: false },
      { amount_cr: "50", allowed: true },
      { amount_cr: "100", allowed: true },
    ];
    const { user } = renderWithProviders(
      <StakePresets presets={presets} value={null} onChange={onChange} />,
    );
    const [small, , large] = screen.getAllByRole("button");

    expect(small).toBeDisabled();
    expect(large).toBeEnabled();

    await user.click(large!);
    expect(onChange).toHaveBeenCalledWith("100");
  });

  test("a chip that cannot be chosen looks it: figure and outline in subtle, the token icon dimmed", () => {
    renderWithProviders(
      <StakePresets
        presets={[
          { amount_cr: "25", allowed: false },
          { amount_cr: "50", allowed: true },
        ]}
        value={null}
        onChange={() => {}}
      />,
    );

    const [refused] = screen.getAllByRole("button");
    // Each names the state as well, so that it wins over the look of "not chosen".
    expect(refused).toHaveClass(
      "disabled:data-[state=off]:text-subtle",
      "disabled:data-[state=off]:border-subtle",
      "disabled:data-[state=off]:[&_[data-slot=token-icon]]:opacity-50",
    );
    expect(refused!.className).not.toMatch(/(^| )disabled:text-subtle( |$)/);
  });

  test("the chosen chip is pressed and filled white", () => {
    renderWithProviders(
      <StakePresets presets={marketViewer().stake_presets} value="50" onChange={() => {}} />,
    );

    const [, chosen] = screen.getAllByRole("button");
    expect(chosen).toHaveAttribute("aria-pressed", "true");
    expect(chosen!.className).toContain("data-[state=on]:bg-foreground");
  });

  test("the whole row can be switched off while a trade waits for its answer", () => {
    renderWithProviders(
      <StakePresets
        presets={marketViewer().stake_presets}
        value="50"
        onChange={() => {}}
        disabled
      />,
    );

    for (const chip of screen.getAllByRole("button")) expect(chip).toBeDisabled();
  });
});

describe("SellPresets", () => {
  const presets = marketViewerWithPosition().sell_presets;

  test("offer Half and All of the selected outcome, each with the shares the server worked out", async () => {
    const onChange = vi.fn();
    const { user } = renderWithProviders(
      <SellPresets presets={presets} outcome="YES" value={null} onChange={onChange} />,
    );

    const [half, all] = screen.getAllByRole("button");
    expect(half).toHaveTextContent("Half");
    expect(half).toHaveTextContent("45.82 shares");
    expect(all).toHaveTextContent("All");
    expect(all).toHaveTextContent("91.64 shares");

    await user.click(half!);
    // The preset is handed on whole: its shares string is sent as it arrived.
    expect(onChange).toHaveBeenCalledWith({
      outcome: "YES",
      portion: "HALF",
      shares: "45.8225",
      allowed: true,
    });
  });

  test("a preset the server does not allow is disabled", () => {
    renderWithProviders(
      <SellPresets presets={presets} outcome="NO" value={null} onChange={() => {}} />,
    );

    for (const chip of screen.getAllByRole("button")) expect(chip).toBeDisabled();
  });

  test("the chosen portion is pressed", () => {
    renderWithProviders(
      <SellPresets presets={presets} outcome="YES" value="ALL" onChange={() => {}} />,
    );

    const [half, all] = screen.getAllByRole("button");
    expect(half).toHaveAttribute("aria-pressed", "false");
    expect(all).toHaveAttribute("aria-pressed", "true");
  });
});

describe("StakeLeftLine", () => {
  test("writes both amounts as sent, with the word following the limit", () => {
    const { rerender } = renderWithProviders(
      <StakeLeftLine stakeLeftCr="74.123456" stakeLimitCr="100" />,
    );
    expect(screen.getByText("74.12 of 100 tokens left in this market")).toBeInTheDocument();

    rerender(<StakeLeftLine stakeLeftCr="0" stakeLimitCr="1" />);
    expect(screen.getByText("0 of 1 token left in this market")).toBeInTheDocument();
  });
});

describe("QuotePanel", () => {
  const row = (label: string) => screen.getByText(label).parentElement!;

  test("shows the quote's own figures when buying", () => {
    renderWithProviders(
      <QuotePanel
        side="BUY"
        outcome="YES"
        quote={quote({
          amount_cr: "25",
          shares: "45.123456",
          average_price: 0.554,
          p_yes_after: 0.559,
        })}
      />,
    );

    expect(row("You pay")).toHaveTextContent("25 tokens");
    expect(row("Shares")).toHaveTextContent("45.12");
    expect(row("Average price")).toHaveTextContent("0.554");
    expect(row("Average price")).toHaveTextContent("per share");
    expect(row("New probability")).toHaveTextContent("55.9% YES");
    expect(screen.getByText("Each share pays 1 token if YES wins.")).toBeInTheDocument();
  });

  test("says You receive when selling, and names the selected outcome below", () => {
    renderWithProviders(
      <QuotePanel
        side="SELL"
        outcome="NO"
        quote={quote({ side: "SELL", amount_cr: "23.874512" })}
      />,
    );

    expect(row("You receive")).toHaveTextContent("23.87");
    expect(screen.queryByText("You pay")).not.toBeInTheDocument();
    expect(screen.getByText("Each share pays 1 token if NO wins.")).toBeInTheDocument();
  });

  test("an average price has two decimals more than a percentage", () => {
    renderWithProviders(
      <QuotePanel side="BUY" outcome="YES" quote={quote({ average_price: 0.5 })} />,
      {
        config: config({ price_display_decimals: 0 }),
      },
    );

    expect(row("Average price")).toHaveTextContent("0.50");
  });

  test("while the quote is on its way the labels stay and the values are skeleton bars", () => {
    renderWithProviders(<QuotePanel side="BUY" outcome="YES" quote={null} />);

    for (const label of ["You pay", "Shares", "Average price", "New probability"]) {
      expect(row(label).querySelector("[data-slot=skeleton]")).toBeInTheDocument();
    }
    expect(document.querySelector("dl")).toHaveAttribute("aria-busy", "true");
  });

  test("with nothing to quote and nothing on its way, the rows stand without figures and do not pulse", () => {
    renderWithProviders(<QuotePanel side="SELL" outcome="NO" quote={null} idle />);

    for (const label of ["You receive", "Shares", "Average price", "New probability"]) {
      expect(within(row(label)).getByText("none yet")).toHaveClass("sr-only");
      expect(row(label)).toHaveTextContent("–");
      expect(row(label).querySelector("[data-slot=skeleton]")).not.toBeInTheDocument();
    }
    expect(document.querySelector("dl")).not.toHaveAttribute("aria-busy");
    expect(screen.getByText("Each share pays 1 token if NO wins.")).toBeInTheDocument();
  });

  test("a row is as tall with a figure as without one, so the sheet does not jump", () => {
    const { rerender } = renderWithProviders(
      <QuotePanel side="BUY" outcome="YES" quote={null} idle />,
    );
    const heights = () =>
      ["You pay", "Shares", "Average price", "New probability"].map((label) =>
        row(label)
          .className.split(" ")
          .filter((name) => /^(min-)?h-/.test(name)),
      );
    const idle = heights();

    rerender(<QuotePanel side="BUY" outcome="YES" quote={null} />);
    expect(heights()).toEqual(idle);
    rerender(<QuotePanel side="BUY" outcome="YES" quote={quote()} />);
    expect(heights()).toEqual(idle);
    expect(idle).toEqual([["h-6"], ["h-6"], ["h-6"], ["h-6"]]);
  });
});
