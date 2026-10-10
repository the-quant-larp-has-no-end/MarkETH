"use client";

import { XIcon } from "lucide-react";
import { type CSSProperties, useEffect, useRef } from "react";

import { Toggle } from "@/components/ui/toggle";
import { keepInSight } from "@/lib/keep-in-sight";
import { cn } from "@/lib/utils";

const chipClasses = "h-11 shrink-0 rounded-sm border px-3 type-body-sm whitespace-nowrap";

export type Chip<Value extends string> = { value: Value; label: string };

/**
 * A group of single-choice chips (DESIGN.md §12). Each chip is 44 px tall. Default: a
 * 1 px `--border-strong` outline and a `--muted-foreground` label. Selected: a `--raised`
 * fill, a `--foreground` label and `aria-pressed="true"`. One chip is always selected.
 *
 * The chips are laid out in one of two ways:
 *
 * - `strip` (the status filter of Markets): each chip as wide as its label, in one row
 *   that scrolls sideways when it is wider than the screen. The selected chip is kept in
 *   sight: a row can open on a chip that lies outside its strip (a shared address, the
 *   back button), and the strip then scrolls to it. Only the strip moves, never the page.
 * - `grid` (the sort of the Leaderboard below 768 px): equal columns across the width,
 *   each label centred in its chip. A grid never scrolls.
 *
 * Nothing else stands in the row of the chips: a control that belongs beside them has a
 * row of its own.
 */
export function FilterChips<Value extends string>({
  label,
  value,
  onChange,
  chips,
  layout = "strip",
  className,
}: {
  /** What the row chooses, for a screen reader: "Status", "Sort by". */
  label: string;
  value: Value;
  onChange: (value: Value) => void;
  chips: readonly Chip<Value>[];
  layout?: "strip" | "grid";
  className?: string;
}) {
  const group = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (layout !== "strip") return;
    keepInSight(group.current, group.current?.querySelector('[aria-pressed="true"]'));
  }, [value, layout]);

  return (
    <div
      ref={group}
      role="group"
      aria-label={label}
      data-slot="filter-chips"
      data-layout={layout}
      style={layout === "grid" ? ({ "--chips": chips.length } as CSSProperties) : undefined}
      className={cn(
        layout === "strip"
          ? // The padding keeps a chip's focus outline inside the scrolling box.
            "-m-1 flex gap-2 overflow-x-auto p-1"
          : "grid grid-cols-[repeat(var(--chips),minmax(0,1fr))] gap-2",
        className,
      )}
    >
      {chips.map((chip) => (
        <Toggle
          key={chip.value}
          pressed={chip.value === value}
          onPressedChange={(pressed) => {
            if (pressed) onChange(chip.value);
          }}
          className={cn(
            chipClasses,
            layout === "grid" && "min-w-0 px-1",
            "data-[state=off]:border-border-strong data-[state=off]:text-muted-foreground data-[state=off]:hover:text-foreground",
            "data-[state=on]:border-raised data-[state=on]:bg-raised data-[state=on]:text-foreground",
          )}
        >
          {layout === "grid" ? <span className="truncate">{chip.label}</span> : chip.label}
        </Toggle>
      ))}
    </div>
  );
}

/**
 * A chip that states a filter which is set (DESIGN.md §12): it carries a close cross and
 * removes that filter when tapped. "BSc Computer Science", "Win rate from 50%". Such
 * chips stand in a list that wraps onto as many lines as it needs.
 *
 * It is never wider than the column it stands in: a label that is longer (a programme
 * can be) is cut with an ellipsis, and the whole of it is the chip's name and its `title`.
 */
export function RemovableChip({
  children,
  onRemove,
  className,
}: {
  /** The filter in words. */
  children: string;
  onRemove: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      data-slot="removable-chip"
      aria-label={`Remove filter: ${children}`}
      title={children}
      onClick={onRemove}
      className={cn(
        chipClasses,
        "inline-flex max-w-full items-center gap-2 border-raised bg-raised text-foreground",
        "transition-opacity duration-(--duration-fast) hover:opacity-90 active:opacity-90",
        className,
      )}
    >
      <span className="min-w-0 truncate">{children}</span>
      <XIcon aria-hidden className="size-4 shrink-0" />
    </button>
  );
}
