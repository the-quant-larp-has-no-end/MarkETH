"use client";

import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import type { Outcome } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

const OUTCOMES = ["YES", "NO"] as const satisfies readonly Outcome[];

/**
 * The outcome buttons of Market detail (DESIGN.md §12): two equal buttons side by side,
 * YES on the left and NO on the right, each with its price: "YES 54.8%". They open the
 * trade sheet with that outcome selected. Both prices are the API's; `p_no` is not
 * worked out from `p_yes`.
 */
export function OutcomeButtons({
  pYes,
  pNo,
  disabled = false,
  onSelect,
  className,
}: {
  /** `p_yes` of the market. */
  pYes: number;
  /** `p_no` of the market. */
  pNo: number;
  /** From `viewer.can_trade`. */
  disabled?: boolean;
  onSelect: (outcome: Outcome) => void;
  className?: string;
}) {
  const { price_display_decimals } = useConfig();

  return (
    <div data-slot="outcome-buttons" className={cn("grid grid-cols-2 gap-2", className)}>
      <Button variant="yes" width="full" disabled={disabled} onClick={() => onSelect("YES")}>
        YES {formatPercent(pYes, price_display_decimals)}
      </Button>
      <Button variant="no" width="full" disabled={disabled} onClick={() => onSelect("NO")}>
        NO {formatPercent(pNo, price_display_decimals)}
      </Button>
    </div>
  );
}

/**
 * The outcome toggle of the trade sheet (DESIGN.md §12): two segments, each with
 * `aria-pressed`. The selected one is filled (`--yes` or `--no`, `--ink` label); the
 * other is `--background` with a `--muted-foreground` label.
 *
 * `value` may be null: neither segment is on until one is chosen. That is for where an
 * outcome must never be a default, as when a market is resolved.
 *
 * `disabled` switches off both, or one: when selling, an outcome is offered only when
 * one of its sell presets is allowed.
 */
export function OutcomeToggle({
  value,
  onChange,
  disabled = false,
  labelledBy,
  describedBy,
  className,
}: {
  value: Outcome | null;
  onChange: (outcome: Outcome) => void;
  disabled?: boolean | Partial<Record<Outcome, boolean>>;
  /** The id of a label that is on screen. Without one the group is named "Outcome". */
  labelledBy?: string;
  /** The id of what describes the group: an error under it. */
  describedBy?: string;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={labelledBy === undefined ? "Outcome" : undefined}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      data-slot="outcome-toggle"
      className={cn("grid grid-cols-2 overflow-hidden rounded-md bg-background", className)}
    >
      {OUTCOMES.map((outcome) => (
        <Toggle
          key={outcome}
          pressed={value === outcome}
          // A segment that is on stays on when it is tapped again.
          onPressedChange={(pressed) => {
            if (pressed) onChange(outcome);
          }}
          disabled={typeof disabled === "boolean" ? disabled : disabled[outcome] === true}
          className={cn(
            "h-12 rounded-md type-label",
            "data-[state=off]:text-muted-foreground data-[state=off]:hover:text-foreground",
            "data-[state=on]:text-ink",
            outcome === "YES" ? "data-[state=on]:bg-yes" : "data-[state=on]:bg-no",
            "disabled:data-[state=off]:text-subtle disabled:data-[state=on]:opacity-50",
          )}
        >
          {outcome}
        </Toggle>
      ))}
    </div>
  );
}
