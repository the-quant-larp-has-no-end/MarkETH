"use client";

import { XIcon } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { Toggle } from "@/components/ui/toggle";
import { cn } from "@/lib/utils";

const chipClasses = "h-11 shrink-0 rounded-sm border px-3 type-body-sm whitespace-nowrap";

export type Chip<Value extends string> = { value: Value; label: string };

/**
 * A row of single-choice chips that scrolls sideways (DESIGN.md §12): the status filter
 * of Markets, the sort of the Leaderboard. Each chip is 44 px tall. Default: a 1 px
 * `--border-strong` outline and a `--muted-foreground` label. Selected: a `--raised`
 * fill, a `--foreground` label and `aria-pressed="true"`. One chip is always selected.
 *
 * The selected chip is kept in sight. On a phone the chips may not fit beside the controls
 * at the row's end, and a row can open on a chip that lies outside its strip (a shared
 * address, the back button): the strip then scrolls to it. Only the strip moves, never
 * the page.
 */
export function FilterChips<Value extends string>({
  label,
  value,
  onChange,
  chips,
  className,
  children,
}: {
  /** What the row chooses, for a screen reader: "Status", "Sort by". */
  label: string;
  value: Value;
  onChange: (value: Value) => void;
  chips: readonly Chip<Value>[];
  className?: string;
  /** Controls that sit at the right end of the row. */
  children?: ReactNode;
}) {
  const strip = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const box = strip.current;
    const pressed = box?.querySelector('[aria-pressed="true"]');
    if (box === null || pressed === null || pressed === undefined) return;
    const chip = pressed.getBoundingClientRect();
    const view = box.getBoundingClientRect();
    if (chip.left < view.left) box.scrollLeft -= view.left - chip.left;
    else if (chip.right > view.right) box.scrollLeft += chip.right - view.right;
  }, [value]);

  return (
    <div data-slot="filter-chips" className={cn("flex items-center gap-2", className)}>
      <div
        ref={strip}
        role="group"
        aria-label={label}
        // The padding keeps a chip's focus outline inside the scrolling box.
        className="-m-1 flex min-w-0 flex-1 gap-2 overflow-x-auto p-1"
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
              "data-[state=off]:border-border-strong data-[state=off]:text-muted-foreground data-[state=off]:hover:text-foreground",
              "data-[state=on]:border-raised data-[state=on]:bg-raised data-[state=on]:text-foreground",
            )}
          >
            {chip.label}
          </Toggle>
        ))}
      </div>
      {children}
    </div>
  );
}

/**
 * A chip that states a filter which is set (DESIGN.md §12): it carries a close cross and
 * removes that filter when tapped. "BSc Computer Science", "Win rate from 50%".
 *
 * It is never wider than the row it stands in: a label that is longer (a programme can
 * be) is cut with an ellipsis, and the whole of it is the chip's name and its `title`.
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
