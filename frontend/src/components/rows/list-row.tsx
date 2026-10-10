import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/**
 * The frame of a list row (DESIGN.md §12): at least 56 px tall, a 1 px `--border`
 * divider, no outline of its own. The rows of this folder are built from these parts and
 * go inside a `RowList`.
 */

/** The classes of the element that fills a row: a link, a button or a plain block. */
export const rowClasses = "flex min-h-14 w-full items-center justify-between gap-3 py-2 text-left";

/** What a row that can be tapped adds. */
export const rowPressClasses =
  "transition-colors duration-(--duration-fast) hover:bg-raised active:bg-raised";

export function RowList({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <ul data-slot="row-list" className={cn("flex flex-col", className)}>
      {children}
    </ul>
  );
}

export function RowItem({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <li data-slot="row" className={cn("border-b last:border-b-0", className)}>
      {children}
    </li>
  );
}

/**
 * Rows that are still loading: `count` of them, each with blocks where a title, the line
 * below it and a figure will be. It is its own list; put it where the `RowList` will be.
 */
export function RowSkeletons({ count, className }: { count: number; className?: string }) {
  return (
    <ul data-slot="row-skeletons" aria-hidden className={cn("flex flex-col", className)}>
      {Array.from({ length: count }, (_, index) => (
        <li key={index} className="border-b last:border-b-0">
          <div className={rowClasses}>
            <span className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </span>
            <Skeleton className="h-5 w-16 shrink-0" />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** The left of a row: a title, and a line below it in `caption`. */
export function RowText({
  title,
  below,
  wrap = false,
}: {
  title: ReactNode;
  below?: ReactNode;
  /** Let the title run over several lines instead of cutting it to one. */
  wrap?: boolean;
}) {
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className={cn("type-body", wrap ? "wrap-break-word" : "truncate")}>{title}</span>
      {below !== undefined && <span className="type-caption text-muted-foreground">{below}</span>}
    </span>
  );
}

/** The right of a row: a figure, and optionally something below it, right aligned. */
export function RowFigure({ children, below }: { children: ReactNode; below?: ReactNode }) {
  return (
    <span className="flex shrink-0 flex-col items-end gap-0.5 text-right">
      {children}
      {below !== undefined && <span className="type-caption text-muted-foreground">{below}</span>}
    </span>
  );
}
