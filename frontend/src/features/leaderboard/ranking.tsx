"use client";

import { ArrowDownIcon, ArrowUpIcon } from "lucide-react";

import { LeaderboardFigure, LeaderboardRow, RowList, RowSkeletons } from "@/components/rows";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { LeaderboardEntry, LeaderboardSort, SortOrder } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

import { VIEWER_ROW } from "./podium";
import { MEASURES, ORDER_NAMES } from "./view";

/**
 * The rows of the leaderboard below 768 px (DESIGN.md §13): every row that is not on the
 * podium as a leaderboard row of 56 px, with the figure of the measure the list is
 * sorted by. The rows stand in the order they were handed in, which is the API's.
 */
export function RankingRows({
  rows,
  sort,
}: {
  rows: readonly LeaderboardEntry[];
  sort: LeaderboardSort;
}) {
  return (
    <RowList>
      {rows.map((entry, index) => (
        // A player can show on two pages while balances move: the place in the list is
        // the one thing that is unique to a row.
        <LeaderboardRow
          key={index}
          entry={entry}
          sort={sort}
          className={entry.is_viewer ? VIEWER_ROW : undefined}
        />
      ))}
    </RowList>
  );
}

/**
 * The same rows from 768 px: a table with all four measures as columns. A tap on a
 * column heading sorts by it, a second tap flips the order, and the heading carries
 * `aria-sort`. Sorting is a request: the heading only says what to ask for.
 */
export function RankingTable({
  rows,
  sort,
  order,
  onSort,
  onFlip,
}: {
  rows: readonly LeaderboardEntry[];
  sort: LeaderboardSort;
  order: SortOrder;
  onSort: (sort: LeaderboardSort) => void;
  onFlip: () => void;
}) {
  const Arrow = order === "desc" ? ArrowDownIcon : ArrowUpIcon;

  return (
    <Table aria-label="Ranking">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead scope="col" className="w-12 text-right">
            Rank
          </TableHead>
          <TableHead scope="col">Player</TableHead>
          {MEASURES.map((measure) => {
            const sorted = measure.sort === sort;
            return (
              <TableHead
                key={measure.sort}
                scope="col"
                aria-sort={sorted ? (order === "desc" ? "descending" : "ascending") : "none"}
                // The heading is its button: 44 px tall, 8 px from the next one, its text
                // in line with the figures below it.
                className="px-1 text-right"
              >
                <button
                  type="button"
                  title={sorted ? ORDER_NAMES[order] : undefined}
                  onClick={() => (sorted ? onFlip() : onSort(measure.sort))}
                  className={cn(
                    "inline-flex h-11 w-full items-center justify-end gap-1 rounded-sm px-2",
                    "transition-colors duration-(--duration-fast) hover:text-foreground",
                    // The table scrolls sideways and cuts off what stands outside it: the
                    // focus outline is drawn inside the button.
                    "focus-visible:-outline-offset-2",
                    sorted && "text-foreground",
                  )}
                >
                  {measure.name}
                  {sorted && <Arrow aria-hidden className="size-4 shrink-0" />}
                </button>
              </TableHead>
            );
          })}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((entry, index) => (
          <TableRow
            key={index}
            className={cn("h-14", entry.is_viewer && [VIEWER_ROW, "bg-raised"])}
          >
            <TableCell className="text-right type-number text-muted-foreground">
              {formatCount(entry.rank)}
            </TableCell>
            <TableCell className="w-full max-w-0">
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="truncate type-body">
                  {entry.username}
                  {entry.is_viewer && (
                    <span className="ml-2 type-caption text-muted-foreground">You</span>
                  )}
                </span>
                <span
                  title={entry.degree_programme}
                  className="truncate type-caption text-muted-foreground"
                >
                  {entry.degree_programme}
                </span>
              </span>
            </TableCell>
            {MEASURES.map((measure) => (
              <TableCell key={measure.sort} className="text-right">
                <LeaderboardFigure entry={entry} sort={measure.sort} />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Rows that are still loading: five of them, and the word for a screen reader. */
export function RankingSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div data-slot="ranking-skeleton" role="status">
      <span className="sr-only">Loading</span>
      <RowSkeletons count={count} />
    </div>
  );
}
