import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The table of DESIGN.md §12: the tables of the admin area, and the ranking of the
 * Leaderboard from 768 px. A heading row 44 px tall in `caption`, 1 px `--border`
 * dividers between the rows, no outline, rows at least 44 px tall.
 *
 * A column says what it holds, cell by cell:
 *
 * - Text is the default: left aligned, and it gives way by wrapping.
 * - `clip`: text that has to stay one line gives way by being cut with an ellipsis.
 * - `numeric`: figures are right aligned, in `number`, as wide as the widest of them, and
 *   never shrink.
 * - `nowrap`: what is neither (a date, a badge, a button) keeps its line and its width.
 *
 * A table that is still wider than its column scrolls sideways inside its own box; the
 * page never does.
 */
function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn("w-full caption-bottom type-body-sm", className)}
        {...props}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead data-slot="table-header" className={cn("[&_tr]:border-b", className)} {...props} />;
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  );
}

/**
 * A row: at least 44 px tall. `tall` is the 56 px row of the Leaderboard. A row that can
 * be tapped shows it on hover; give one that cannot `hover:bg-transparent`.
 */
function TableRow({
  className,
  tall = false,
  ...props
}: React.ComponentProps<"tr"> & { tall?: boolean }) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors duration-(--duration-fast) hover:bg-raised",
        tall ? "h-14" : "h-11",
        className,
      )}
      {...props}
    />
  );
}

/** What a cell holds, which says how its column behaves when room is short. */
type Holds = {
  /** Figures: right aligned, in `number`, as wide as the widest, never shrinking. */
  numeric?: boolean;
  /** One line that keeps its width: a date, a badge, a button. */
  nowrap?: boolean;
  /**
   * One line of text that is cut with an ellipsis. Its column takes the room the others
   * leave: give two such columns a share each (`w-1/2`). A cell with two lines gives each
   * line `truncate` itself.
   */
  clip?: boolean;
};

function TableHead({
  className,
  numeric = false,
  ...props
}: React.ComponentProps<"th"> & Pick<Holds, "numeric">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "h-11 px-3 align-middle type-caption whitespace-nowrap text-muted-foreground",
        numeric ? "text-right" : "text-left",
        className,
      )}
      {...props}
    />
  );
}

function TableCell({
  className,
  numeric = false,
  nowrap = false,
  clip = false,
  ...props
}: React.ComponentProps<"td"> & Holds) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-3 py-2 align-middle",
        numeric && "w-px text-right type-number whitespace-nowrap",
        nowrap && "w-px whitespace-nowrap",
        clip && "max-w-0 truncate",
        !numeric && !nowrap && !clip && "wrap-break-word",
        className,
      )}
      {...props}
    />
  );
}

function TableCaption({ className, ...props }: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-3 type-caption text-muted-foreground", className)}
      {...props}
    />
  );
}

export { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow };
