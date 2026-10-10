"use client";

import { useRouter } from "next/navigation";
import type { MouseEvent, ReactNode } from "react";

import { Link } from "@/components/shell";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConfig } from "@/lib/api/session";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A probability as the API sends it, as a percentage with the decimals of `/config`. */
export function Chance({ value }: { value: number }) {
  const { price_display_decimals } = useConfig();
  return <>{formatPercent(value, price_display_decimals)}</>;
}

export type Column<Row> = {
  /** The column's heading. */
  header: string;
  /** Keep the heading for screen readers only: a column of buttons that speak for themselves. */
  hideHeader?: boolean;
  /** A column of figures: right aligned, in the number style. */
  numeric?: boolean;
  /** The column that may wrap and gets room for it: the question of a market. */
  wide?: boolean;
  /**
   * A column whose lines stay one line each and are cut with an ellipsis. Such columns
   * share the room the others leave; a cell gives each of its lines `truncate`.
   */
  clip?: boolean;
  /** Classes of the column's cells: the least room a column that wraps needs (`min-w-40`). */
  className?: string;
  cell: (row: Row) => ReactNode;
};

/** The marker of the button that opens a row, by which a tap elsewhere on the row finds it. */
const ROW_OPENER = "row-opener";

/** What every cell and heading of a table has at its sides: 12 px, or 8 px where room is short. */
const sides = (dense: boolean) => (dense ? "px-2" : undefined);

function Head<Row>({ columns, dense }: { columns: readonly Column<Row>[]; dense: boolean }) {
  return (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        {columns.map((column) => (
          <TableHead
            key={column.header}
            scope="col"
            numeric={column.numeric}
            className={sides(dense)}
          >
            {column.hideHeader === true ? (
              <span className="sr-only">{column.header}</span>
            ) : (
              column.header
            )}
          </TableHead>
        ))}
      </TableRow>
    </TableHeader>
  );
}

/**
 * What a cell of a column holds (DESIGN.md §12, Tables): figures, the one text that may
 * wrap and gets room for it, lines that are cut, or something that keeps its line.
 */
const holds = <Row,>(column: Column<Row>, dense: boolean) => ({
  numeric: column.numeric === true,
  clip: column.clip === true,
  nowrap: column.numeric !== true && column.wide !== true && column.clip !== true,
  className: cn(
    sides(dense),
    // A question keeps room for several words a line, also where the table scrolls.
    column.wide && "min-w-64",
    // Two columns of cut lines get a share each of the room the others leave, and keep
    // enough of it on a phone, where the table scrolls sideways, to stay readable.
    column.clip && "w-1/2 min-w-36",
    column.className,
  ),
});

/** What stands in the first cell of a row that can be opened: one target, as tall as the row. */
const openerClasses = "flex min-h-11 w-full flex-col items-start justify-center py-2";

/**
 * A table of the admin area (DESIGN.md §13): rows at least 44 px tall in `body-sm`,
 * designed for 1024 px and scrolling sideways on anything narrower. The rows stand in the
 * order they arrive: nothing is sorted, filtered or added up here.
 *
 * A row can be opened in one of two ways. With `rowHref` it leads to that address, and
 * the link itself is in the first column, where the keyboard and a screen reader find it.
 * With `onRowOpen` it calls back, from a button in the first column; `canOpen` says which
 * rows do. Either way a tap anywhere else on the row does the same.
 *
 * `dense` gives the cells 8 px at their sides in place of 12 px: the risk table, which
 * has ten columns to fit into 1024 px.
 */
export function DataTable<Row>({
  label,
  columns,
  rows,
  rowKey,
  rowHref,
  onRowOpen,
  canOpen,
  dense = false,
}: {
  /** What the table lists, for a screen reader. */
  label: string;
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string | number;
  rowHref?: (row: Row) => string;
  /** Opens a row. `opener` is its button, which takes the focus back afterwards. */
  onRowOpen?: (row: Row, opener: HTMLButtonElement | null) => void;
  /** Whether `onRowOpen` is offered for this row. Without it, for every row. */
  canOpen?: (row: Row) => boolean;
  dense?: boolean;
}) {
  const router = useRouter();

  const follow = (href: string) => (event: MouseEvent<HTMLTableRowElement>) => {
    // The link and any button of the row act for themselves.
    if ((event.target as Element).closest("a, button") !== null) return;
    router.push(href);
  };
  const open = (row: Row) => (event: MouseEvent<HTMLTableRowElement>) => {
    if ((event.target as Element).closest("a, button") !== null) return;
    onRowOpen?.(
      row,
      event.currentTarget.querySelector<HTMLButtonElement>(`[data-slot=${ROW_OPENER}]`),
    );
  };

  return (
    <Table aria-label={label}>
      <Head columns={columns} dense={dense} />
      <TableBody>
        {rows.map((row) => {
          const href = rowHref?.(row);
          const opens = href === undefined && onRowOpen !== undefined && (canOpen?.(row) ?? true);
          return (
            <TableRow
              key={rowKey(row)}
              onClick={href !== undefined ? follow(href) : opens ? open(row) : undefined}
              className={href !== undefined || opens ? "cursor-pointer" : "hover:bg-transparent"}
            >
              {columns.map((column, index) => {
                const cell = holds(column, dense);
                if (index === 0 && href !== undefined) {
                  return (
                    <TableCell key={column.header} {...cell} className={cn(cell.className, "py-0")}>
                      <Link href={href} className={openerClasses}>
                        {column.cell(row)}
                      </Link>
                    </TableCell>
                  );
                }
                if (index === 0 && opens) {
                  return (
                    <TableCell key={column.header} {...cell} className={cn(cell.className, "py-0")}>
                      <button
                        type="button"
                        data-slot={ROW_OPENER}
                        aria-haspopup="dialog"
                        // The cell cuts what leaves it, and a ring around the button would
                        // leave it: the ring is drawn inside.
                        className={cn(openerClasses, "text-left focus-visible:-outline-offset-2")}
                        onClick={(event) => onRowOpen?.(row, event.currentTarget)}
                      >
                        {column.cell(row)}
                      </button>
                    </TableCell>
                  );
                }
                return (
                  <TableCell key={column.header} {...cell}>
                    {column.cell(row)}
                  </TableCell>
                );
              })}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/** The same table while its rows load: the headings, and blocks where the cells will be. */
export function DataTableSkeleton<Row>({
  columns,
  rows = 3,
  dense = false,
}: {
  columns: readonly Column<Row>[];
  rows?: number;
  dense?: boolean;
}) {
  return (
    <div data-slot="table-skeleton" role="status">
      <span className="sr-only">Loading</span>
      <Table aria-hidden>
        <Head columns={columns} dense={dense} />
        <TableBody>
          {Array.from({ length: rows }, (_, row) => (
            <TableRow key={row} className="hover:bg-transparent">
              {columns.map((column) => {
                const roomy = column.wide === true || column.clip === true;
                return (
                  <TableCell key={column.header} className={holds(column, dense).className}>
                    <Skeleton
                      className={cn("h-4", roomy ? "w-full" : "w-16", column.numeric && "ml-auto")}
                    />
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** An empty table is one plain sentence (DESIGN.md §13): "No proposals waiting." */
export function EmptyLine({ children }: { children: string }) {
  return (
    <p data-slot="empty-line" className="py-3 type-body-sm text-muted-foreground">
      {children}
    </p>
  );
}
