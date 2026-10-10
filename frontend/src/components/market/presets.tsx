"use client";

import type { ReactNode } from "react";

import { Amount } from "@/components/money";
import { Toggle } from "@/components/ui/toggle";
import type { Outcome, SellPortion, SellPreset, StakePreset } from "@/lib/api/schema";
import { formatAmount, formatShares, formatTokens } from "@/lib/format";
import { cn } from "@/lib/utils";

/** One chip of a preset row: 48 px tall, `--radius-md`, a 1 px `--border-strong` outline. */
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
        "disabled:border-subtle disabled:text-subtle disabled:data-[state=on]:opacity-50",
      )}
    >
      {children}
    </Toggle>
  );
}

/**
 * The stake presets of a buy (DESIGN.md §12): a row of equal chips from
 * `viewer.stake_presets`, each with the token icon and its `amount_cr`. A preset with
 * `allowed: false` is disabled: that flag is the server's, and the frontend never
 * decides it from the balance or the limit. The chosen `amount_cr` is passed on as the
 * string it arrived as.
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
  /** The `amount_cr` of the chosen preset, or null while none is chosen. */
  value: string | null;
  onChange: (amount_cr: string) => void;
  /** Switches the whole row off, for example while a trade waits for its answer. */
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Stake"
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
 * The line under the stake presets and in "Your position" (DESIGN.md §12):
 * "{stake_left_cr} of {stake_limit_cr} tokens left in this market". Both amounts come
 * from the market's `viewer`, or from the `details` of a `LIMIT_EXCEEDED` answer.
 */
export function StakeLeftLine({
  stakeLeftCr,
  stakeLimitCr,
  className,
}: {
  stakeLeftCr: string;
  stakeLimitCr: string;
  className?: string;
}) {
  return (
    <p data-slot="stake-left" className={cn("type-body-sm text-muted-foreground", className)}>
      {formatAmount(stakeLeftCr)} of {formatTokens(stakeLimitCr)} left in this market
    </p>
  );
}
