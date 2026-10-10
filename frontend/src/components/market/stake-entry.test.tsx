import { fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import type { BuySlider, StakePreset } from "@/lib/api/schema";
import { marketViewer } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";

import { StakeEntry } from "./stake-entry";

/**
 * The stake entry with a parent that holds the text of its field, as the trade ticket
 * does. `onChange` sees every text the entry hands on.
 */
function Entry({
  start = "",
  slider = { min: 1, max: 100, step: 1 },
  presets = marketViewer().stake_presets,
  left = "100",
  limit = "100",
  refusal,
  disabled,
  onChange,
}: {
  start?: string;
  slider?: BuySlider | null;
  presets?: readonly StakePreset[];
  left?: string;
  limit?: string;
  refusal?: string | null;
  disabled?: boolean;
  onChange?: (value: string) => void;
}) {
  const [value, setValue] = useState(start);
  return (
    <StakeEntry
      value={value}
      onChange={(next) => {
        setValue(next);
        onChange?.(next);
      }}
      slider={slider}
      presets={presets}
      stakeLeftCr={left}
      stakeLimitCr={limit}
      refusal={refusal}
      disabled={disabled}
    />
  );
}

const group = () => screen.getByRole("group", { name: "Stake" });
const field = () => screen.getByRole<HTMLInputElement>("textbox", { name: "Stake in tokens" });
const thumb = () => screen.getByRole("slider", { name: "Stake in whole tokens" });
const chip = (amount: string) => screen.getByRole("button", { name: `${amount} tokens` });
const part = (slot: string) => document.querySelector<HTMLElement>(`[data-slot=${slot}]`)!;
const line = () => part("stake-left");

/** Fails unless each element comes after the one before it in the document. */
function expectInOrder(elements: Element[]): void {
  for (let index = 1; index < elements.length; index += 1) {
    const follows =
      elements[index - 1]!.compareDocumentPosition(elements[index]!) &
      Node.DOCUMENT_POSITION_FOLLOWING;
    expect(follows, `element ${index} follows element ${index - 1}`).toBeTruthy();
  }
}

describe("StakeEntry", () => {
  describe("what it shows", () => {
    test("top to bottom: the label, the slider beside the amount field, the presets, the stake-left line", () => {
      renderWithProviders(<Entry left="74.123456" />);

      const label = within(group()).getByText("Stake");
      expect(label).toHaveClass("type-caption", "text-muted-foreground");
      expectInOrder([label, thumb(), field(), chip("25"), chip("100"), line()]);
      expect(line()).toHaveTextContent("74.12 of 100 tokens left in this market");
      // 8 px between its parts.
      expect(part("stake-entry")).toHaveClass("flex", "flex-col", "gap-2");
    });

    test("the slider and the field share one row, 48 px tall and 12 px apart; the field is 112 px wide", () => {
      renderWithProviders(<Entry />);

      const row = part("stake-field").parentElement!;
      expect(row).toHaveClass("flex", "h-12", "items-center", "gap-3");
      expect(row).toContainElement(thumb());
      expect(part("stake-field")).toHaveClass("w-28", "shrink-0");
      // The slider takes what the field leaves.
      expect(part("slider").parentElement).toHaveClass("min-w-0", "flex-1");
    });

    test("the field has the token icon inside its left end and its text right aligned, in the number style", () => {
      renderWithProviders(<Entry start="40" />);

      const icon = part("stake-field").querySelector("[data-slot=token-icon]")!;
      expect(icon).toHaveClass("absolute", "left-3", "pointer-events-none");
      expect(icon).toHaveAttribute("aria-hidden", "true");
      expect(field()).toHaveClass("pl-9", "text-right", "type-number", "h-12");
    });

    test("opens the decimal keypad, and is a text field: nothing rounds or steps what it holds", () => {
      renderWithProviders(<Entry />);

      expect(field()).toHaveAttribute("inputmode", "decimal");
      expect(field()).toHaveAttribute("type", "text");
      expect(field()).toHaveAttribute("autocomplete", "off");
      expect(field()).not.toHaveAttribute("min");
      expect(field()).not.toHaveAttribute("max");
      expect(field()).not.toHaveAttribute("step");
    });

    test("without a range there is no slider, and the field takes the whole row", () => {
      renderWithProviders(<Entry slider={null} />);

      expect(screen.queryByRole("slider")).not.toBeInTheDocument();
      expect(part("stake-field")).toHaveClass("min-w-0", "flex-1");
      expect(part("stake-field")).not.toHaveClass("w-28");
      // The field and the presets are there as ever.
      expect(field()).toBeEnabled();
      expect(chip("25")).toBeInTheDocument();
    });

    test("is never green or red", () => {
      renderWithProviders(<Entry start="40" />);

      const classes = [part("slider"), part("slider-track"), part("slider-range"), thumb()]
        .map((element) => element.className)
        .join(" ");
      expect(classes).not.toMatch(/\b(yes|no)\b/);
    });
  });

  describe("the names a screen reader hears (DESIGN.md §15)", () => {
    test("the three controls form a group named Stake; the presets are a group named Quick amounts", () => {
      renderWithProviders(<Entry />);

      expect(group()).toContainElement(thumb());
      expect(group()).toContainElement(field());
      const presets = within(group()).getByRole("group", { name: "Quick amounts" });
      expect(within(presets).getAllByRole("button")).toHaveLength(3);
      expect(within(presets).getByRole("button", { name: "25 tokens" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
    });

    test("the stake-left line describes the field and the slider", () => {
      renderWithProviders(<Entry left="60" />);

      expect(field()).toHaveAccessibleDescription("60 of 100 tokens left in this market");
      expect(thumb()).toHaveAccessibleDescription("60 of 100 tokens left in this market");
      expect(field()).not.toHaveAttribute("aria-invalid");
    });

    test("the slider says its value in tokens, and the word follows the figure", () => {
      const { unmount } = renderWithProviders(<Entry start="40" />);
      expect(thumb()).toHaveAttribute("aria-valuetext", "40 tokens");
      unmount();

      renderWithProviders(<Entry start="1" />);
      expect(thumb()).toHaveAttribute("aria-valuetext", "1 token");
    });

    test("the slider says not set while the field holds an amount it cannot show", () => {
      renderWithProviders(<Entry start="37.5" />);

      expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
    });
  });

  describe("typing", () => {
    test("changes the field, and hands on the characters as they stand", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry onChange={onChange} />);

      await user.type(field(), "37.5");

      expect(field()).toHaveValue("37.5");
      expect(onChange.mock.calls.map(([text]) => text)).toEqual(["3", "37", "37.", "37.5"]);
    });

    test("a comma becomes a point as it is typed, and any other character is not taken", async () => {
      const { user } = renderWithProviders(<Entry />);

      await user.type(field(), "1 2,5 tokens-");

      expect(field()).toHaveValue("12.5");
    });

    test("takes one decimal point: a second one is not taken", async () => {
      const { user } = renderWithProviders(<Entry />);

      await user.type(field(), "1.2.3,4");

      expect(field()).toHaveValue("1.234");
    });

    test.each(["007", ".5", "5.", "0.100000000000000000000000000001", "9007199254740993"])(
      "keeps %j as it was typed: nothing is read as a number, completed or rounded",
      async (typed) => {
        const onChange = vi.fn();
        const { user } = renderWithProviders(<Entry onChange={onChange} />);

        await user.type(field(), typed);

        expect(field()).toHaveValue(typed);
        expect(onChange).toHaveBeenLastCalledWith(typed);
      },
    );

    test("measures the amount against nothing: one beyond the slider and the stake that is left is handed on like any other", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(
        <Entry slider={{ min: 1, max: 50, step: 1 }} left="50" onChange={onChange} />,
      );

      await user.type(field(), "5000");

      expect(field()).toHaveValue("5000");
      expect(onChange).toHaveBeenLastCalledWith("5000");
      // Nothing of the entry's own says no: that is the server's to say.
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(field()).not.toHaveAttribute("aria-invalid");
    });
  });

  describe("the slider", () => {
    test("runs over the range the API sent, from min to max in steps of step", () => {
      renderWithProviders(<Entry slider={{ min: 5, max: 85, step: 5 }} start="40" />);

      expect(thumb()).toHaveAttribute("aria-valuemin", "5");
      expect(thumb()).toHaveAttribute("aria-valuemax", "85");
      expect(thumb()).toHaveAttribute("aria-valuenow", "40");
    });

    test("moving it writes the step it stands on into the field as plain digits", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry start="40" onChange={onChange} />);

      thumb().focus();
      await user.keyboard("{ArrowRight}");

      expect(field()).toHaveValue("41");
      expect(onChange).toHaveBeenLastCalledWith("41");

      await user.keyboard("{End}");
      expect(field()).toHaveValue("100");
      await user.keyboard("{Home}");
      expect(field()).toHaveValue("1");
    });

    test("an arrow key moves it one step of the range", async () => {
      const { user } = renderWithProviders(
        <Entry slider={{ min: 5, max: 100, step: 5 }} start="40" />,
      );

      thumb().focus();
      await user.keyboard("{ArrowRight}");
      expect(field()).toHaveValue("45");
      await user.keyboard("{ArrowLeft}{ArrowLeft}");
      expect(field()).toHaveValue("35");
    });

    test("stands on a step, with the track filled up to it, while the field holds exactly the digits of that step", async () => {
      const { user } = renderWithProviders(<Entry />);

      await user.type(field(), "40");

      expect(thumb()).toHaveAttribute("aria-valuenow", "40");
      expect(part("slider")).toHaveAttribute("data-shown", "true");
      expect(thumb()).toHaveClass("bg-foreground");
      expect(part("slider-range")).not.toHaveClass("hidden");
    });

    test("opens on an empty field as an outline at the start of its range, over an empty track", () => {
      renderWithProviders(<Entry />);

      expect(field()).toHaveValue("");
      expect(part("slider")).toHaveAttribute("data-shown", "false");
      expect(thumb()).toHaveAttribute("aria-valuenow", "1");
      expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
      expect(thumb()).toHaveClass("bg-card", "border-2", "border-border-strong");
      expect(part("slider-range")).toHaveClass("hidden");
    });

    test.each([
      ["37.5", "an amount between two steps"],
      ["150", "an amount beyond the slider"],
      ["040", "the digits of no step, though the figure is one"],
      ["40.0", "a step written with decimals"],
      ["40.", "an amount that is not finished"],
    ])(
      "with %j in the field (%s) the thumb stays where it is, as an outline",
      async (text) => {
        const { user } = renderWithProviders(<Entry start="40" />);
        expect(part("slider")).toHaveAttribute("data-shown", "true");

        await user.clear(field());
        await user.type(field(), text);

        expect(field()).toHaveValue(text);
        expect(part("slider")).toHaveAttribute("data-shown", "false");
        expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
        expect(part("slider-range")).toHaveClass("hidden");
        expect(thumb()).toHaveClass("bg-card", "border-2", "border-border-strong");
      },
    );

    test("stays on the last step it showed while the field holds something else", async () => {
      const { user } = renderWithProviders(<Entry start="40" />);

      await user.type(field(), ".5");

      expect(field()).toHaveValue("40.5");
      expect(thumb()).toHaveAttribute("aria-valuenow", "40");
      expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
    });

    test("a step is found by its digits alone: with steps of 5, 42 is none and 45 is one", async () => {
      const { user } = renderWithProviders(<Entry slider={{ min: 5, max: 100, step: 5 }} />);

      await user.type(field(), "42");
      expect(part("slider")).toHaveAttribute("data-shown", "false");

      await user.clear(field());
      await user.type(field(), "45");
      expect(part("slider")).toHaveAttribute("data-shown", "true");
      expect(thumb()).toHaveAttribute("aria-valuenow", "45");
    });

    test("moving it from an amount it could not show is a choice like any other", async () => {
      const { user } = renderWithProviders(<Entry start="40" />);
      await user.type(field(), ".5");

      thumb().focus();
      await user.keyboard("{ArrowRight}");

      expect(field()).toHaveValue("41");
      expect(part("slider")).toHaveAttribute("data-shown", "true");
    });

    test("a key that would move it nowhere still chooses the step it rests on", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry onChange={onChange} />);
      // An empty field: the outline rests on the first step, and Home leads nowhere else.
      thumb().focus();

      await user.keyboard("{Home}");

      expect(field()).toHaveValue("1");
      expect(onChange).toHaveBeenCalledExactlyOnceWith("1");
      expect(part("slider")).toHaveAttribute("data-shown", "true");
    });

    test("a press that moves it nowhere still chooses the step it rests on", () => {
      const onChange = vi.fn();
      renderWithProviders(<Entry onChange={onChange} />);

      // The press ends where the thumb already stands: the slider itself reports nothing.
      fireEvent.pointerUp(part("slider"));

      expect(field()).toHaveValue("1");
      expect(onChange).toHaveBeenCalledExactlyOnceWith("1");
    });

    test("a press or a key on a slider that shows its amount writes nothing again", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry start="40" onChange={onChange} />);

      fireEvent.pointerUp(part("slider"));
      thumb().focus();
      await user.keyboard("{Tab}");

      expect(onChange).not.toHaveBeenCalled();
      expect(field()).toHaveValue("40");
    });

    test("a key that moves no slider chooses nothing", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry onChange={onChange} />);

      thumb().focus();
      await user.keyboard("a{Enter}{Shift}");

      expect(onChange).not.toHaveBeenCalled();
      expect(field()).toHaveValue("");
    });

    test("when its range shrinks under it, the outline rests on the nearest end", () => {
      const { rerender } = renderWithProviders(
        <StakeEntry
          value="90"
          onChange={() => {}}
          slider={{ min: 1, max: 100, step: 1 }}
          presets={[]}
          stakeLeftCr="100"
          stakeLimitCr="100"
        />,
      );
      expect(thumb()).toHaveAttribute("aria-valuenow", "90");

      // A trade was made: the stake that is left covers 50 at most.
      rerender(
        <StakeEntry
          value="90"
          onChange={() => {}}
          slider={{ min: 1, max: 50, step: 1 }}
          presets={[]}
          stakeLeftCr="50"
          stakeLimitCr="100"
        />,
      );

      expect(thumb()).toHaveAttribute("aria-valuemax", "50");
      expect(thumb()).toHaveAttribute("aria-valuenow", "50");
      expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
      // The field is the user's: it keeps what it held.
      expect(field()).toHaveValue("90");
    });

    test("a drag along it is not a drag of the sheet it stands in", () => {
      renderWithProviders(<Entry />);

      expect(part("slider").parentElement).toHaveAttribute("data-vaul-no-drag");
    });
  });

  describe("the presets", () => {
    test("a tap writes the preset's amount into the field, as the string that arrived", async () => {
      const onChange = vi.fn();
      const presets = [
        { amount_cr: "12.5", allowed: true },
        { amount_cr: "1250.000001", allowed: true },
      ];
      const { user } = renderWithProviders(<Entry presets={presets} onChange={onChange} />);

      await user.click(chip("1,250.00"));

      expect(field()).toHaveValue("1250.000001");
      expect(onChange).toHaveBeenLastCalledWith("1250.000001");
    });

    test("a preset is selected while the field holds exactly its amount, however it got there", async () => {
      const { user } = renderWithProviders(<Entry />);

      await user.click(chip("50"));
      expect(chip("50")).toHaveAttribute("aria-pressed", "true");
      expect(chip("25")).toHaveAttribute("aria-pressed", "false");

      // Typed: the same characters select the same chip.
      await user.clear(field());
      await user.type(field(), "25");
      expect(chip("25")).toHaveAttribute("aria-pressed", "true");
      expect(chip("50")).toHaveAttribute("aria-pressed", "false");

      // Set with the slider: 25, then one step on.
      thumb().focus();
      await user.keyboard("{ArrowRight}");
      expect(field()).toHaveValue("26");
      expect(chip("25")).toHaveAttribute("aria-pressed", "false");
      await user.keyboard("{ArrowLeft}");
      expect(chip("25")).toHaveAttribute("aria-pressed", "true");
    });

    test("the slider stands on a preset's amount when that is one of its steps", async () => {
      const { user } = renderWithProviders(<Entry />);

      await user.click(chip("50"));

      expect(thumb()).toHaveAttribute("aria-valuenow", "50");
      expect(thumb()).toHaveAttribute("aria-valuetext", "50 tokens");
      expect(part("slider")).toHaveAttribute("data-shown", "true");
    });

    test("a preset the server does not allow is disabled, whatever the slider reaches", () => {
      renderWithProviders(
        <Entry
          slider={{ min: 1, max: 100, step: 1 }}
          presets={[
            { amount_cr: "25", allowed: false },
            { amount_cr: "50", allowed: true },
          ]}
        />,
      );

      expect(chip("25")).toBeDisabled();
      expect(chip("50")).toBeEnabled();
    });
  });

  describe("the refusal", () => {
    test("is the API's message under the stake-left line, with an alert icon, and is announced", () => {
      renderWithProviders(<Entry start="150" refusal="This would exceed a limit." />);

      const refusal = within(group()).getByRole("alert");
      expect(refusal).toHaveTextContent("This would exceed a limit.");
      expect(refusal).toHaveClass("type-body-sm", "text-no-text");
      expect(refusal.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      expectInOrder([line(), refusal]);
    });

    test("marks the field: it is invalid, which turns its border to the error colour", () => {
      renderWithProviders(<Entry start="150" refusal="This would exceed a limit." />);

      expect(field()).toHaveAttribute("aria-invalid", "true");
      expect(field().className).toContain("aria-invalid:border-no");
    });

    test("describes the field and the slider, after the stake-left line", () => {
      renderWithProviders(<Entry start="150" left="40" refusal="This would exceed a limit." />);

      const description = "40 of 100 tokens left in this market This would exceed a limit.";
      expect(field()).toHaveAccessibleDescription(description);
      expect(thumb()).toHaveAccessibleDescription(description);
    });

    test("without one nothing is announced and the field is not marked", () => {
      renderWithProviders(<Entry start="25" refusal={null} />);

      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(field()).not.toHaveAttribute("aria-invalid");
    });
  });

  describe("disabled", () => {
    test("switches off the slider, the field and every preset, and keeps what the field holds on screen", async () => {
      const onChange = vi.fn();
      const { user } = renderWithProviders(<Entry start="25" disabled onChange={onChange} />);

      expect(field()).toBeDisabled();
      expect(field()).toHaveValue("25");
      expect(part("slider")).toHaveAttribute("data-disabled");
      for (const amount of ["25", "50", "100"]) expect(chip(amount)).toBeDisabled();
      // What was set stays readable: the chip and the thumb still show it.
      expect(chip("25")).toHaveAttribute("aria-pressed", "true");
      expect(thumb()).toHaveAttribute("aria-valuenow", "25");

      thumb().focus();
      await user.keyboard("{ArrowRight}{End}");
      fireEvent.pointerUp(part("slider"));
      expect(onChange).not.toHaveBeenCalled();
    });

    test("a disabled slider that shows no amount chooses none on a press", () => {
      const onChange = vi.fn();
      renderWithProviders(<Entry disabled onChange={onChange} />);

      fireEvent.pointerUp(part("slider"));

      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
