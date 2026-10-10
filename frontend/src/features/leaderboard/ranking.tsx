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
 * podium as a leaderboard row of at least 56 px, with the figure of the measure the list
 * is sorted by. The rows stand in the order they were handed in, which is the API's.
 * Each row is a button that selects its player.
 */
export function RankingRows({
  rows,
  sort,
  onSelect,
}: {
  rows: readonly LeaderboardEntry[];
  sort: LeaderboardSort;
  onSelect: (entry: LeaderboardEntry) => void;
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
          onSelect={() => onSelect(entry)}
          className={entry.is_viewer ? VIEWER_ROW : undefined}
        />
      ))}
    </RowList>
  );
}

/* The cells of the ranking stand 16 px apart: 8 px at either side of each. */
const CELL = "px-2";

/**
 * The same rows from 768 px: a table of 56 px rows with all four measures as columns.
 * "Rank" is 56 px wide, "Player" takes what the others leave and cuts the username and
 * the programme with an ellipsis, one line each; the four figures are right aligned,
 * each as wide as its widest figure.
 *
 * A tap on the heading of a measure sorts by it, a second tap flips the order, and the
 * heading carries `aria-sort`. Sorting is a request: the heading only says what to ask for.
 *
 * A tap anywhere on a row selects its player. The username is the row's button, which is
 * what the keyboard and a screen reader reach: its click is the row's.
 */
export function RankingTable({
  rows,
  sort,
  order,
  onSort,
  onFlip,
  onSelect,
}: {
  rows: readonly LeaderboardEntry[];
  sort: LeaderboardSort;
  order: SortOrder;
  onSort: (sort: LeaderboardSort) => void;
  onFlip: () => void;
  onSelect: (entry: LeaderboardEntry) => void;
}) {
  const Arrow = order === "desc" ? ArrowDownIcon : ArrowUpIcon;

  return (
    <Table aria-label="Ranking">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {/* 56 px, also beside a column that asks for all the width there is. */}
          <TableHead scope="col" numeric className={cn("w-14 min-w-14", CELL)}>
            Rank
          </TableHead>
          <TableHead scope="col" className={CELL}>
            Player
          </TableHead>
          {MEASURES.map((measure) => {
            const sorted = measure.sort === sort;
            return (
              <TableHead
                key={measure.sort}
                scope="col"
                numeric
                aria-sort={sorted ? (order === "desc" ? "descending" : "ascending") : "none"}
                // The heading is its button, 44 px tall, which brings the 8 px at either
                // side itself: its text ends in line with the figures below it.
                className="px-0"
              >
                <button
                  type="button"
                  title={sorted ? ORDER_NAMES[order] : undefined}
                  onClick={() => (sorted ? onFlip() : onSort(measure.sort))}
                  className={cn(
                    "inline-flex h-11 w-full items-center justify-end gap-1 rounded-sm",
                    CELL,
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
            tall
            onClick={() => onSelect(entry)}
            className={cn("cursor-pointer", entry.is_viewer && [VIEWER_ROW, "bg-raised"])}
          >
            <TableCell numeric className={cn(CELL, "text-muted-foreground")}>
              {formatCount(entry.rank)}
            </TableCell>
            <TableCell className={cn("w-full max-w-0", CELL)}>
              <button
                type="button"
                aria-haspopup="dialog"
                className="flex w-full min-w-0 flex-col rounded-sm text-left focus-visible:-outline-offset-2"
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span title={entry.username} className="truncate type-body">
                    {entry.username}
                  </span>
                  {entry.is_viewer && (
                    <span className="shrink-0 type-caption text-muted-foreground">You</span>
                  )}
                </span>
                <span
                  title={entry.degree_programme}
                  className="truncate type-caption text-muted-foreground"
                >
                  {entry.degree_programme}
                </span>
              </button>
            </TableCell>
            {MEASURES.map((measure) => (
              <TableCell key={measure.sort} numeric className={CELL}>
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
