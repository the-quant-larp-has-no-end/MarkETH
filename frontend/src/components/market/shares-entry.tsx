"use client";

import { useId } from "react";

import { InlineError } from "@/components/feedback";
import { Input } from "@/components/ui/input";
import type { BuyPreset, BuySlider, Outcome, SellPreset, SellSlider } from "@/lib/api/schema";
import { formatShares, typedDecimal, typedWhole } from "@/lib/format";
import { cn } from "@/lib/utils";

import { EntrySlider } from "./entry-slider";
import { SellPresets, SharePresets, StakeLeftLine } from "./presets";

export type SharesEntryProps = {
  /**
   * What the shares field holds: the characters that were typed, the `shares` of a
   * shortcut, or the digits of a step of the slider. A quote and trade send it as `shares`.
   */
  value: string;
  onChange: (value: string) => void;
  /** The API's message while the server refuses the number. */
  refusal?: string | null;
  /** Switches all three controls off: the viewer cannot trade, or a trade waits. */
  disabled?: boolean;
  className?: string;
} & (
  | {
      side: "BUY";
      /** The selected outcome's controls from `viewer.buy`. */
      slider: BuySlider | null;
      presets: readonly BuyPreset[];
      stakeLeftCr: string;
      stakeLimitCr: string;
    }
  | {
      side: "SELL";
      outcome: Outcome;
      slider: SellSlider | null;
      presets: readonly SellPreset[];
    }
);

/**
 * The shares entry (DESIGN.md §12): a slider, a shares field and shortcuts that set
 * one number of shares. A buy takes whole shares, a sale takes decimals. The field
 * always shows what will be sent. Moving the slider writes the step it
 * stands on into the field as plain digits, a tap on a chip writes its `shares` as the
 * string that arrived, and typing changes the field and nothing else.
 *
 * Both then show what the field holds, by a test of two strings for equality: a chip is
 * selected while the field holds exactly its `shares`, and the thumb stands on a step while
 * the field holds exactly the digits of that step.
 *
 * Nothing here reads the text as a number or measures it against a balance, limit or
 * holding. The slider and shortcuts are the server's, and its quote decides whether
 * the entered shares can be traded. `refusal` is where it says no.
 */
export function SharesEntry(props: SharesEntryProps) {
  const { value, onChange, slider, refusal = null, disabled = false, className } = props;
  const buying = props.side === "BUY";
  const id = useId();
  const labelId = `${id}-label`;
  const leftId = `${id}-left`;
  const refusalId = `${id}-refusal`;
  const refused = refusal !== null && refusal !== "";
  const describedBy =
    [buying ? leftId : null, refused ? refusalId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div
      role="group"
      aria-labelledby={labelId}
      data-slot="shares-entry"
      className={cn("flex flex-col gap-2", className)}
    >
      <span id={labelId} className="type-caption text-muted-foreground">
        {buying ? "Shares" : "Shares to sell"}
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
            inputMode={buying ? "numeric" : "decimal"}
            autoComplete="off"
            aria-label="Number of shares"
            aria-describedby={describedBy}
            aria-invalid={refused || undefined}
            value={value}
            onChange={(event) => onChange((buying ? typedWhole : typedDecimal)(event.target.value))}
            disabled={disabled}
            // The `number` style at the size of every field: Barlow 500, tabular figures.
            className="text-right font-display font-medium tabular-nums"
          />
        </div>
      </div>

      {props.side === "BUY" ? (
        <>
          <SharePresets
            presets={props.presets}
            value={value}
            onChange={onChange}
            disabled={disabled}
          />
          <StakeLeftLine
            id={leftId}
            stakeLeftCr={props.stakeLeftCr}
            stakeLimitCr={props.stakeLimitCr}
          />
        </>
      ) : (
        <SellPresets
          presets={props.presets}
          outcome={props.outcome}
          value={value}
          onChange={onChange}
          disabled={disabled}
        />
      )}

      {refused && <InlineError id={refusalId}>{refusal}</InlineError>}
    </div>
  );
}
