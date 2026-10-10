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
  cell: (row: Row) => ReactNode;
};

function Head<Row>({ columns }: { columns: readonly Column<Row>[] }) {
  return (
    <TableHeader>
      <TableRow className="hover:bg-transparent">
        {columns.map((column) => (
          <TableHead key={column.header} scope="col" numeric={column.numeric}>
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
 * wrap and gets room for it, or something that keeps its line.
 */
const holds = <Row,>(column: Column<Row>) => ({
  numeric: column.numeric === true,
  nowrap: column.numeric !== true && column.wide !== true,
  className: cn(column.wide && "min-w-64"),
});

/**
 * A table of the admin area (DESIGN.md §13): rows 44 px tall in `body-sm`, designed for
 * 1024 px and scrolling sideways on anything narrower. The rows stand in the order they
 * arrive: nothing is sorted, filtered or added up here.
 *
 * With `rowHref` a row opens that address. The link itself is in the first column, where
 * the keyboard and a screen reader find it; a tap anywhere else on the row follows it.
 */
export function DataTable<Row>({
  label,
  columns,
  rows,
  rowKey,
  rowHref,
}: {
  /** What the table lists, for a screen reader. */
  label: string;
  columns: readonly Column<Row>[];
  rows: readonly Row[];
  rowKey: (row: Row) => string | number;
  rowHref?: (row: Row) => string;
}) {
  const router = useRouter();

  const follow = (href: string) => (event: MouseEvent<HTMLTableRowElement>) => {
    // The link and any button of the row act for themselves.
    if ((event.target as Element).closest("a, button") !== null) return;
    router.push(href);
  };

  return (
    <Table aria-label={label}>
      <Head columns={columns} />
      <TableBody>
        {rows.map((row) => {
          const href = rowHref?.(row);
          return (
            <TableRow
              key={rowKey(row)}
              onClick={href === undefined ? undefined : follow(href)}
              className={href === undefined ? "hover:bg-transparent" : "cursor-pointer"}
            >
              {columns.map((column, index) =>
                index === 0 && href !== undefined ? (
                  <TableCell
                    key={column.header}
                    {...holds(column)}
                    className={cn(holds(column).className, "py-0")}
                  >
                    <Link href={href} className="flex min-h-11 items-center py-2">
                      {column.cell(row)}
                    </Link>
                  </TableCell>
                ) : (
                  <TableCell key={column.header} {...holds(column)}>
                    {column.cell(row)}
                  </TableCell>
                ),
              )}
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
}: {
  columns: readonly Column<Row>[];
  rows?: number;
}) {
  return (
    <div data-slot="table-skeleton" role="status">
      <span className="sr-only">Loading</span>
      <Table aria-hidden>
        <Head columns={columns} />
        <TableBody>
          {Array.from({ length: rows }, (_, row) => (
            <TableRow key={row} className="hover:bg-transparent">
              {columns.map((column) => (
                <TableCell key={column.header} className={cn(column.wide && "min-w-64")}>
                  <Skeleton
                    className={cn(
                      "h-4",
                      column.wide ? "w-full" : "w-16",
                      column.numeric && "ml-auto",
                    )}
                  />
                </TableCell>
              ))}
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
