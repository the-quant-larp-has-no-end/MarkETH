"use client";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

export type Segment<Value extends string> = {
  value: Value;
  label: string;
  disabled?: boolean;
};

/**
 * The segmented control (DESIGN.md §12): switches one form between two modes, Buy and
 * Sell. 48 px tall, fully rounded, a `--background` track. The selected segment is a
 * `--foreground` fill with a `--primary-foreground` label; the other has a
 * `--muted-foreground` label. One segment is always selected.
 */
export function SegmentedControl<Value extends string>({
  label,
  value,
  onChange,
  segments,
  disabled = false,
  className,
}: {
  /** What the control chooses, for a screen reader: "Trade side". */
  label: string;
  value: Value;
  onChange: (value: Value) => void;
  segments: readonly Segment<Value>[];
  disabled?: boolean;
  className?: string;
}) {
  return (
    <ToggleGroup
      type="single"
      aria-label={label}
      data-slot="segmented-control"
      value={value}
      // Tapping the selected segment again would leave nothing selected: ignore it.
      onValueChange={(next) => {
        if (next !== "") onChange(next as Value);
      }}
      disabled={disabled}
      className={cn("h-12 w-full rounded-full bg-background", className)}
    >
      {segments.map((segment) => (
        <ToggleGroupItem
          key={segment.value}
          value={segment.value}
          disabled={segment.disabled}
          className={cn(
            "h-full flex-1 rounded-full type-label",
            "data-[state=off]:text-muted-foreground data-[state=off]:hover:text-foreground",
            "data-[state=on]:bg-foreground data-[state=on]:text-primary-foreground",
            "disabled:data-[state=off]:text-subtle disabled:data-[state=on]:opacity-50",
          )}
        >
          {segment.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
