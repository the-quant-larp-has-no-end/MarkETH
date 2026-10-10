"use client";

import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";

import { TokenIcon } from "@/components/brand";
import { InlineError } from "@/components/feedback";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import type { BuySlider, StakePreset } from "@/lib/api/schema";
import { formatTokens, typedDecimal } from "@/lib/format";
import { cn } from "@/lib/utils";

import { StakeLeftLine, StakePresets } from "./presets";

/**
 * The steps of the slider, each under its digits: `"40"` is the step 40. The three
 * integers of `viewer.buy_slider` are sent so that a slider can be drawn, and this is the
 * slider reading them: from `min` to `max`, `step` apart.
 */
function stepsByDigits({ min, max, step }: BuySlider): ReadonlyMap<string, number> {
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

export type StakeEntryProps = {
  /**
   * What the amount field holds: the characters that were typed, the `amount_cr` of a
   * preset, or the digits of a step of the slider. It is what a buy sends as `amount_cr`.
   */
  value: string;
  onChange: (value: string) => void;
  /** `viewer.buy_slider`: where the slider runs, or null for no slider. */
  slider: BuySlider | null;
  /** `viewer.stake_presets`, in the API's order. */
  presets: readonly StakePreset[];
  /** `stake_left_cr` and `stake_limit_cr` of the viewer, or of a `LIMIT_EXCEEDED` answer. */
  stakeLeftCr: string;
  stakeLimitCr: string;
  /** The API's message while the server refuses the amount. */
  refusal?: string | null;
  /** Switches all three controls off: the viewer cannot trade, or a trade waits. */
  disabled?: boolean;
  className?: string;
};

/**
 * The stake entry of a buy (DESIGN.md §12): a slider, an amount field and a row of presets
 * that set one amount. The field always shows what will be sent. Moving the slider writes
 * the step it stands on into the field as plain digits, a tap on a preset writes its
 * `amount_cr` as the string that arrived, and typing changes the field and nothing else.
 *
 * Both then show what the field holds, by a test of two strings for equality: a preset is
 * selected while the field holds exactly its `amount_cr`, and the thumb stands on a step
 * while the field holds exactly the digits of that step. With any other text the thumb
 * stays where it is, drawn as an outline over an empty track.
 *
 * Nothing here reads the text as a number or measures it against anything: not the ends
 * of the slider, not the stake that is left, not the balance. Whether an amount can be
 * traded is the server's answer to the quote, and `refusal` is where it says no.
 */
export function StakeEntry({
  value,
  onChange,
  slider,
  presets,
  stakeLeftCr,
  stakeLimitCr,
  refusal = null,
  disabled = false,
  className,
}: StakeEntryProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const leftId = `${id}-left`;
  const refusalId = `${id}-refusal`;
  const refused = refusal !== null && refusal !== "";
  // The stake-left line is the help of both controls, and the refusal their error.
  const describedBy = refused ? `${leftId} ${refusalId}` : leftId;

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      data-slot="stake-entry"
      className={cn("flex flex-col gap-2", className)}
    >
      <span id={labelId} className="type-caption text-muted-foreground">
        Stake
      </span>

      <div className="flex h-12 items-center gap-3">
        {slider !== null && (
          <StakeSlider
            range={slider}
            value={value}
            onChange={onChange}
            disabled={disabled}
            describedBy={describedBy}
          />
        )}

        <div
          data-slot="stake-field"
          className={cn("relative", slider === null ? "min-w-0 flex-1" : "w-28 shrink-0")}
        >
          <TokenIcon
            className={cn(
              "pointer-events-none absolute top-1/2 left-3 -translate-y-1/2",
              disabled && "opacity-50",
            )}
          />
          <Input
            type="text"
            inputMode="decimal"
            autoComplete="off"
            aria-label="Stake in tokens"
            aria-describedby={describedBy}
            aria-invalid={refused || undefined}
            value={value}
            onChange={(event) => onChange(typedDecimal(event.target.value))}
            disabled={disabled}
            // The `number` style at the size of every field: Barlow 500, tabular figures.
            // A refusal arrives while the amount is being typed, so the field has the focus
            // then: its border is `--no` all the same, as every refused field's is.
            className="pl-9 text-right font-display font-medium tabular-nums"
          />
        </div>
      </div>

      <StakePresets presets={presets} value={value} onChange={onChange} disabled={disabled} />

      <StakeLeftLine id={leftId} stakeLeftCr={stakeLeftCr} stakeLimitCr={stakeLimitCr} />

      {refused && <InlineError id={refusalId}>{refusal}</InlineError>}
    </div>
  );
}

/**
 * The slider of the stake entry, over the whole tokens of `viewer.buy_slider`. It takes the
 * width the amount field leaves.
 */
function StakeSlider({
  range,
  value,
  onChange,
  disabled,
  describedBy,
}: {
  range: BuySlider;
  /** The text of the amount field. */
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  describedBy: string;
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
  // slider reports nothing. While it shows no amount, that step is chosen all the same.
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
        label="Stake in whole tokens"
        value={place}
        onChange={(chosen) => onChange(String(chosen))}
        min={min}
        max={max}
        step={step}
        valueText={shown ? formatTokens(String(place)) : "not set"}
        shown={shown}
        disabled={disabled}
        aria-describedby={describedBy}
      />
    </div>
  );
}
