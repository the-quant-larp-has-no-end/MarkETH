"use client";

import type { ReactNode } from "react";

import { Amount } from "@/components/money";
import { Toggle } from "@/components/ui/toggle";
import type { Outcome, SellPortion, SellPreset, StakePreset } from "@/lib/api/schema";
import { formatAmount, formatShares, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * One chip of a preset row: 48 px tall, `--radius-md`, a 1 px `--border-strong` outline.
 * A chip that cannot be chosen looks it: its outline and its figure in `--subtle`, its
 * token icon at half strength. Those classes name the state too, or they would lose to
 * the ones for "not chosen".
 */
function PresetChip({
  selected,
  disabled,
  onSelect,
  children,
}: {
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <Toggle
      pressed={selected}
      onPressedChange={(pressed) => {
        if (pressed) onSelect();
      }}
      disabled={disabled}
      className={cn(
        "min-h-12 min-w-0 flex-col gap-0 rounded-md border px-2 py-1",
        "data-[state=off]:border-border-strong data-[state=off]:text-foreground data-[state=off]:hover:bg-raised data-[state=off]:active:bg-raised",
        "data-[state=on]:border-foreground data-[state=on]:bg-foreground data-[state=on]:text-primary-foreground",
        "disabled:data-[state=off]:border-subtle disabled:data-[state=off]:text-subtle disabled:data-[state=on]:opacity-50",
        "disabled:data-[state=off]:[&_[data-slot=token-icon]]:opacity-50",
      )}
    >
      {children}
    </Toggle>
  );
}

/**
 * The presets of the stake entry (DESIGN.md §12): a row of equal chips from
 * `viewer.stake_presets`, each with the token icon and its `amount_cr`. They are shortcuts:
 * a tap hands on the preset's `amount_cr`, as the string it arrived as, for the amount
 * field to hold. A chip is selected while `value`, the text of that field, is exactly its
 * `amount_cr`: a test of two strings for equality, nothing is read as a number.
 *
 * A preset with `allowed: false` is disabled: that flag is the server's, and the frontend
 * never decides it from the balance or the limit.
 */
export function StakePresets({
  presets,
  value,
  onChange,
  disabled = false,
  className,
}: {
  /** `viewer.stake_presets`, in the API's order. */
  presets: readonly StakePreset[];
  /** What the amount field holds. */
  value: string;
  onChange: (amount_cr: string) => void;
  /** Switches the whole row off, for example while a trade waits for its answer. */
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Quick amounts"
      data-slot="stake-presets"
      className={cn("grid auto-cols-fr grid-flow-col gap-2", className)}
    >
      {presets.map((preset) => (
        <PresetChip
          key={preset.amount_cr}
          selected={preset.amount_cr === value}
          disabled={disabled || !preset.allowed}
          onSelect={() => onChange(preset.amount_cr)}
        >
          <Amount value={preset.amount_cr} />
        </PresetChip>
      ))}
    </div>
  );
}

const PORTIONS: Record<SellPortion, string> = { HALF: "Half", ALL: "All" };

/**
 * The presets of a sell (DESIGN.md §13, Trade sheet): two chips, "Half" and "All", from
 * the entries of `viewer.sell_presets` for the selected outcome. Each shows its `shares`
 * and is disabled when `allowed` is false. The server worked out half and all of the
 * holding; the frontend halves nothing, and sends the chosen preset's `shares` string as
 * it arrived.
 */
export function SellPresets({
  presets,
  outcome,
  value,
  onChange,
  disabled = false,
  className,
}: {
  /** `viewer.sell_presets`: all four entries. */
  presets: readonly SellPreset[];
  /** The outcome whose two entries are offered. */
  outcome: Outcome;
  /** The portion of the chosen preset, or null while none is chosen. */
  value: SellPortion | null;
  onChange: (preset: SellPreset) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Shares to sell"
      data-slot="sell-presets"
      className={cn("grid auto-cols-fr grid-flow-col gap-2", className)}
    >
      {presets
        .filter((preset) => preset.outcome === outcome)
        .map((preset) => (
          <PresetChip
            key={preset.portion}
            selected={preset.portion === value}
            disabled={disabled || !preset.allowed}
            onSelect={() => onChange(preset)}
          >
            <span className="type-label">{PORTIONS[preset.portion]}</span>
            <span className="type-caption">{formatShares(preset.shares)}</span>
          </PresetChip>
        ))}
    </div>
  );
}

/**
 * The stake-left line of the stake entry and of "Your position" (DESIGN.md §12):
 * "{stake_left_cr} of {stake_limit_cr} tokens left in this market". Both amounts come
 * from the market's `viewer`, or from the `details` of a `LIMIT_EXCEEDED` answer.
 */
export function StakeLeftLine({
  id,
  stakeLeftCr,
  stakeLimitCr,
  className,
}: {
  /** For what the line describes: the stake entry names it in `aria-describedby`. */
  id?: string;
  stakeLeftCr: string;
  stakeLimitCr: string;
  className?: string;
}) {
  return (
    <p
      id={id}
      data-slot="stake-left"
      className={cn("type-body-sm text-muted-foreground", className)}
    >
      {formatAmount(stakeLeftCr)} of {formatTokens(stakeLimitCr)} left in this market
    </p>
  );
}
