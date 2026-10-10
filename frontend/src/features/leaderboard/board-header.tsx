"use client";

import { ArrowDownWideNarrowIcon, ArrowUpNarrowWideIcon } from "lucide-react";

import { FilterChips, RemovableChip } from "@/components/controls";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { LeaderboardSort, SortOrder } from "@/lib/api/schema";
import { formatCount } from "@/lib/format";

import { type FilterName, MEASURES, ORDER_NAMES, type SetFilter } from "./view";

const SORT_CHIPS = MEASURES.map((measure) => ({ value: measure.sort, label: measure.name }));

/**
 * The head of the leaderboard (DESIGN.md §13), top to bottom: the title row, the line
 * that states the measure, the sort chips and the filters that are set. Each part has a
 * row of its own, so nothing runs under anything else at any width.
 *
 * Nothing here touches a row of the list: each control hands on what to ask the API for.
 */

/**
 * The title row, 44 px tall: the title at the left, and at the right the button that
 * flips the order, named after the order that is active, and "Filter" with the number of
 * filters that are set. The title is never cut: where the three do not fit on one line
 * (a 320 px phone while filters are set) the two buttons stand on a line of their own
 * under it, at the left.
 */
export function TitleRow({
  order,
  filterCount,
  onFlip,
  onFilter,
}: {
  order: SortOrder;
  /** How many filters are set: the chips under the sort chips. */
  filterCount: number;
  onFlip: () => void;
  /** Opens the filter sheet. */
  onFilter: () => void;
}) {
  const OrderIcon = order === "desc" ? ArrowDownWideNarrowIcon : ArrowUpNarrowWideIcon;

  return (
    <div data-slot="title-row" className="flex min-h-11 flex-wrap items-center gap-2">
      <h1 className="flex-auto type-h1 whitespace-nowrap">Leaderboard</h1>
      <div data-slot="board-controls" className="flex shrink-0 items-center gap-2">
        <Button
          variant="secondary"
          size="icon"
          aria-label={ORDER_NAMES[order]}
          title={ORDER_NAMES[order]}
          onClick={onFlip}
        >
          <OrderIcon aria-hidden />
        </Button>
        <Button variant="secondary" size="compact" width="fit" onClick={onFilter}>
          Filter
          {filterCount > 0 && (
            <>
              <Badge aria-hidden className="bg-foreground text-primary-foreground">
                {formatCount(filterCount)}
              </Badge>
              <span className="sr-only">, {formatCount(filterCount)} set</span>
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

/**
 * The four measures as chips, one of them selected. Below 768 px they are a grid of four
 * equal columns across the content width, two by two on a screen under 344 px wide; from
 * 768 px each chip is as wide as its label and the four stand at the left. Nothing shares
 * their row, and they never scroll.
 */
export function SortChips({
  sort,
  wide,
  onSort,
}: {
  sort: LeaderboardSort;
  /** From 768 px. */
  wide: boolean;
  onSort: (sort: LeaderboardSort) => void;
}) {
  return (
    <div data-slot="sort-chips">
      <FilterChips
        label="Sort by"
        value={sort}
        onChange={onSort}
        chips={SORT_CHIPS}
        layout={wide ? "strip" : "grid"}
        className={wide ? undefined : "max-[344px]:grid-cols-2"}
      />
    </div>
  );
}

/**
 * The filters that are set, each as a chip that removes it. The chips wrap onto as many
 * lines as they need, and none is wider than the column: a programme with a long name is
 * cut with an ellipsis.
 */
export function SetFilters({
  filters,
  onRemove,
}: {
  filters: readonly SetFilter[];
  onRemove: (names: readonly FilterName[]) => void;
}) {
  if (filters.length === 0) return null;

  return (
    <ul aria-label="Filters that are set" className="flex flex-wrap gap-2">
      {filters.map((filter) => (
        <li key={filter.id} className="flex max-w-full min-w-0">
          <RemovableChip onRemove={() => onRemove(filter.names)}>{filter.label}</RemovableChip>
        </li>
      ))}
    </ul>
  );
}
