import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { Slider } from "./slider";

/**
 * The stake slider of a viewer who may put up to 100 tokens into a market. Its ends are
 * multiples of its step, as the API sends them: from 1 in steps of 1, or from 5 in steps
 * of 5.
 */
function Stake({
  start = 40,
  step = 1,
  shown,
  disabled,
  onChange,
}: {
  start?: number;
  step?: number;
  shown?: boolean;
  disabled?: boolean;
  onChange?: (value: number) => void;
}) {
  const [value, setValue] = useState(start);
  return (
    <>
      <p id="stake-left">60 of 100 tokens left in this market</p>
      <Slider
        label="Stake in whole tokens"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange?.(next);
        }}
        min={step}
        max={100}
        step={step}
        valueText={shown === false ? "not set" : `${value} tokens`}
        shown={shown}
        disabled={disabled}
        aria-describedby="stake-left"
      />
    </>
  );
}

const thumb = () => screen.getByRole("slider", { name: "Stake in whole tokens" });
const part = (slot: string) => document.querySelector(`[data-slot=${slot}]`);

describe("Slider", () => {
  test("is a slider with its ends, its value in words and what describes it", () => {
    render(<Stake />);

    expect(thumb()).toHaveAttribute("aria-valuemin", "1");
    expect(thumb()).toHaveAttribute("aria-valuemax", "100");
    expect(thumb()).toHaveAttribute("aria-valuenow", "40");
    expect(thumb()).toHaveAttribute("aria-valuetext", "40 tokens");
    expect(thumb()).toHaveAccessibleDescription("60 of 100 tokens left in this market");
  });

  test("a 4 px track with rounded ends, filled up to a 24 px disc that sits in a 44 px target", () => {
    render(<Stake />);

    expect(part("slider")).toHaveClass("h-11", "w-full", "touch-none");
    expect(part("slider-track")).toHaveClass("h-1", "rounded-full", "bg-border-strong");
    expect(part("slider-range")).toHaveClass("bg-foreground");
    expect(part("slider-range")).not.toHaveClass("hidden");
    expect(thumb()).toHaveClass("size-6", "rounded-full", "bg-foreground", "after:-inset-2.5");
  });

  test("is never green or red", () => {
    render(<Stake />);

    const classes = [part("slider"), part("slider-track"), part("slider-range"), thumb()]
      .map((element) => element?.className ?? "")
      .join(" ");
    expect(classes).not.toMatch(/\b(yes|no)\b/);
  });

  test("the arrow keys move it one step, Home and End take it to its ends", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Stake step={5} start={40} onChange={onChange} />);

    thumb().focus();
    await user.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith(45);
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(onChange).toHaveBeenLastCalledWith(35);
    await user.keyboard("{End}");
    expect(onChange).toHaveBeenLastCalledWith(100);
    await user.keyboard("{Home}");
    expect(onChange).toHaveBeenLastCalledWith(5);
    expect(thumb()).toHaveAttribute("aria-valuenow", "5");
    expect(thumb()).toHaveAttribute("aria-valuetext", "5 tokens");
  });

  test("while it cannot show what is set, the thumb is an outline and no part of the track is filled", () => {
    render(<Stake shown={false} />);

    expect(part("slider")).toHaveAttribute("data-shown", "false");
    expect(part("slider-range")).toHaveClass("hidden");
    expect(thumb()).toHaveClass("bg-card", "border-2", "border-border-strong");
    expect(thumb()).not.toHaveClass("bg-foreground");
    // The thumb stays where it is, and says that it claims nothing.
    expect(thumb()).toHaveAttribute("aria-valuenow", "40");
    expect(thumb()).toHaveAttribute("aria-valuetext", "not set");
  });

  test("moving it from there is a choice like any other", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Stake shown={false} onChange={onChange} />);

    thumb().focus();
    await user.keyboard("{ArrowRight}");

    expect(onChange).toHaveBeenCalledWith(41);
  });

  test("a disabled slider takes no key", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Stake disabled onChange={onChange} />);

    expect(part("slider")).toHaveAttribute("data-disabled");
    expect(part("slider")).toHaveClass("data-disabled:pointer-events-none");
    thumb().focus();
    await user.keyboard("{ArrowRight}{End}");

    expect(onChange).not.toHaveBeenCalled();
  });
});
