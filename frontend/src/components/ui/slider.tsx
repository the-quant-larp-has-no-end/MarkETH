"use client";

import * as React from "react";
import { Slider as SliderPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

export type SliderProps = {
  /** The step the thumb stands on: a whole number from `min` to `max`. */
  value: number;
  /** The thumb was moved onto another step. */
  onChange: (value: number) => void;
  /** The ends of the slider and the distance between two steps, as the API sends them. */
  min: number;
  max: number;
  step: number;
  /** Names the slider for a screen reader: "Stake in whole tokens". */
  label: string;
  /** What a screen reader hears as its value: "40 tokens", or "not set". */
  valueText?: string;
  /**
   * False while the slider cannot show what is set, because the field beside it holds a
   * text that is none of its steps. The thumb then stays where it is, drawn as an outline,
   * and no part of the track is filled: the slider claims nothing.
   */
  shown?: boolean;
  disabled?: boolean;
  /** The ids of what describes the slider: its help text, its error. */
  "aria-describedby"?: string;
  className?: string;
};

/**
 * The slider of DESIGN.md §12 (Stake entry): one thumb on a 4 px `--border-strong` track
 * with rounded ends. The part up to the thumb is `--foreground`; the thumb is a 24 px
 * `--foreground` disc inside a 44 px target. Arrow keys move it one step, Home and End
 * take it to its ends. It is never green or red.
 *
 * It only says where the thumb stands. It decides nothing about an amount: its ends are
 * drawn, not checked against anything, and moving it posts nothing (DESIGN.md §14).
 */
function Slider({
  value,
  onChange,
  min,
  max,
  step,
  label,
  valueText,
  shown = true,
  disabled = false,
  "aria-describedby": describedBy,
  className,
}: SliderProps) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      data-shown={shown}
      value={[value]}
      onValueChange={([next]) => {
        if (next !== undefined) onChange(next);
      }}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      className={cn(
        // 44 px tall: a tap anywhere on that height moves the thumb there.
        "relative flex h-11 w-full touch-none items-center select-none",
        "data-disabled:pointer-events-none data-disabled:opacity-50",
        className,
      )}
    >
      <SliderPrimitive.Track
        data-slot="slider-track"
        className="relative h-1 w-full grow overflow-hidden rounded-full bg-border-strong"
      >
        <SliderPrimitive.Range
          data-slot="slider-range"
          className={cn("absolute h-full bg-foreground", !shown && "hidden")}
        />
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        data-slot="slider-thumb"
        aria-label={label}
        aria-valuetext={valueText}
        aria-describedby={describedBy}
        className={cn(
          "relative block size-6 rounded-full transition-colors duration-(--duration-fast)",
          // The target around the disc: 44 px. It is measured from inside the outline, so
          // the outlined thumb reaches 2 px further to stay as large.
          "after:absolute after:rounded-full",
          shown
            ? "bg-foreground after:-inset-2.5"
            : "border-2 border-border-strong bg-card after:-inset-3",
        )}
      />
    </SliderPrimitive.Root>
  );
}

export { Slider };
