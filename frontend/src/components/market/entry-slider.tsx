"use client";

import { type KeyboardEvent, useMemo, useRef, useState } from "react";

import { Slider } from "@/components/ui/slider";
import type { BuySlider, SellSlider } from "@/lib/api/schema";

/** Where a slider runs, as the API sends it: `slider` of `viewer.buy`, or `slider` of `viewer.sell`. */
export type SliderRange = BuySlider | SellSlider;

/**
 * The steps of the slider, each under its digits: `"40"` is the step 40. The three
 * integers of a range are sent so that a slider can be drawn, and this is the slider
 * reading them: from `min` to `max`, `step` apart.
 */
function stepsByDigits({ min, max, step }: SliderRange): ReadonlyMap<string, number> {
  const steps = new Map<string, number>();
  // A step that is no distance would never reach the end: such a slider has one place.
  const apart = step > 0 ? step : max - min + 1;
  for (let place = min; place <= max; place += apart) steps.set(String(place), place);
  return steps;
}

/** The keys that move a slider. One that moves it nowhere still chooses where it stands. */
const SLIDER_KEYS = new Set([
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
]);

/**
 * The slider of an entry of the trade ticket (DESIGN.md §12), beside the field it fills:
 * over the whole shares of `viewer.buy` when buying, over the whole shares of an
 * entry of `viewer.sell` when selling. It takes the width the field leaves.
 *
 * Moving it writes the step it stands on into the field as plain digits. The thumb stands
 * on a step while the field holds exactly the digits of that step: a test of two strings
 * for equality. With any other text it stays where it is, drawn as an outline over an
 * empty track.
 */
export function EntrySlider({
  range,
  value,
  onChange,
  label,
  valueText,
  disabled,
  describedBy,
}: {
  range: SliderRange;
  /** The text of the field beside it. */
  value: string;
  onChange: (value: string) => void;
  /** Names the slider for a screen reader: "Whole shares". */
  label: string;
  /** What a screen reader hears for the digits of a step: "40 shares". */
  valueText: (digits: string) => string;
  disabled: boolean;
  /** The ids of what describes the slider: its help text, its error. */
  describedBy: string | undefined;
}) {
  const { min, max, step } = range;
  // The same range in another answer of the API is the same slider.
  const steps = useMemo(() => stepsByDigits({ min, max, step }), [min, max, step]);
  // The step whose digits the field holds, if it holds those of one.
  const held = steps.get(value);
  // Where the thumb stays while the field holds anything else: the step it stood on last,
  // or the nearest end of a range that has changed since.
  const [rest, setRest] = useState(min);
  if (held !== undefined && held !== rest) setRest(held);
  const place = held ?? Math.min(Math.max(rest, min), max);
  const shown = held !== undefined;

  // A press or a key that leaves the thumb on the step it rests on moves nothing, so the
  // slider reports nothing. While it shows nothing, that step is chosen all the same.
  const chooseRest = () => {
    if (!shown && !disabled) onChange(String(place));
  };
  // A press is the slider's own when it began on the slider. A pointer that was pressed
  // elsewhere, to select the text of the field for one, and is let go here chooses nothing.
  const pressed = useRef(false);

  return (
    <div
      // A drag along the slider is not a drag of the sheet it stands in.
      data-vaul-no-drag=""
      className="min-w-0 flex-1"
      onPointerDown={() => {
        pressed.current = true;
      }}
      onPointerUp={() => {
        if (pressed.current) chooseRest();
        pressed.current = false;
      }}
      onPointerCancel={() => {
        pressed.current = false;
      }}
      onKeyUp={(event: KeyboardEvent) => {
        if (SLIDER_KEYS.has(event.key)) chooseRest();
      }}
    >
      <Slider
        label={label}
        value={place}
        onChange={(chosen) => onChange(String(chosen))}
        min={min}
        max={max}
        step={step}
        valueText={shown ? valueText(String(place)) : "not set"}
        shown={shown}
        disabled={disabled}
        aria-describedby={describedBy}
      />
    </div>
  );
}
