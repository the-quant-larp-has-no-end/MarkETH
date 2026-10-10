"use client";

import { useId } from "react";

import { InlineError } from "@/components/feedback";
import { Input } from "@/components/ui/input";
import type { Outcome, SellPreset, SellSlider } from "@/lib/api/schema";
import { formatShares, typedDecimal } from "@/lib/format";
import { cn } from "@/lib/utils";

import { EntrySlider } from "./entry-slider";
import { SellPresets } from "./presets";

export type SharesEntryProps = {
  /**
   * What the shares field holds: the characters that were typed, the `shares` of a sell
   * preset, or the digits of a step of the slider. It is what a sell sends as `shares`.
   */
  value: string;
  onChange: (value: string) => void;
  /** The outcome that is sold: its two presets are offered. */
  outcome: Outcome;
  /** `slider` of the entry of `viewer.sell` for the outcome: where the slider runs, or null. */
  slider: SellSlider | null;
  /** `viewer.sell_presets`: all four entries. */
  presets: readonly SellPreset[];
  /** The API's message while the server refuses the number. */
  refusal?: string | null;
  /** Switches all three controls off: the viewer cannot trade, or a trade waits. */
  disabled?: boolean;
  className?: string;
};

/**
 * The shares entry of a sell (DESIGN.md §12): a slider, a shares field and the chips
 * "Half" and "All", which set one number of shares. It is the stake entry of a buy, for
 * shares. The field always shows what will be sent. Moving the slider writes the step it
 * stands on into the field as plain digits, a tap on a chip writes its `shares` as the
 * string that arrived, and typing changes the field and nothing else.
 *
 * Both then show what the field holds, by a test of two strings for equality: a chip is
 * selected while the field holds exactly its `shares`, and the thumb stands on a step while
 * the field holds exactly the digits of that step.
 *
 * Nothing here reads the text as a number or measures it against the holding: the ends of
 * the slider and the two chips are the server's, and whether a number of shares can be
 * sold is its answer to the quote. `refusal` is where it says no.
 */
export function SharesEntry({
  value,
  onChange,
  outcome,
  slider,
  presets,
  refusal = null,
  disabled = false,
  className,
}: SharesEntryProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const refusalId = `${id}-refusal`;
  const refused = refusal !== null && refusal !== "";
  const describedBy = refused ? refusalId : undefined;

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      data-slot="shares-entry"
      className={cn("flex flex-col gap-2", className)}
    >
      <span id={labelId} className="type-caption text-muted-foreground">
        Shares to sell
      </span>

      <div className="flex h-12 items-center gap-3">
        {slider !== null && (
          <EntrySlider
            range={slider}
            value={value}
            onChange={onChange}
            label="Whole shares"
            valueText={formatShares}
            disabled={disabled}
            describedBy={describedBy}
          />
        )}

        <div
          data-slot="shares-field"
          className={cn(slider === null ? "min-w-0 flex-1" : "w-28 shrink-0")}
        >
          <Input
            type="text"
            inputMode="decimal"
            autoComplete="off"
            aria-label="Number of shares"
            aria-describedby={describedBy}
            aria-invalid={refused || undefined}
            value={value}
            onChange={(event) => onChange(typedDecimal(event.target.value))}
            disabled={disabled}
            // The `number` style at the size of every field: Barlow 500, tabular figures.
            className="text-right font-display font-medium tabular-nums"
          />
        </div>
      </div>

      <SellPresets
        presets={presets}
        outcome={outcome}
        value={value}
        onChange={onChange}
        disabled={disabled}
      />

      {refused && <InlineError id={refusalId}>{refusal}</InlineError>}
    </div>
  );
}
