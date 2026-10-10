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
 * The sort row of the leaderboard (DESIGN.md §13): the four measures as chips, one of
 * them selected; at the right end the button that flips the order, named after the
 * order that is active, and "Filter" with a count while filters are set. Under the row,
 * each filter that is set as a chip that removes it.
 *
 * Nothing here touches a row of the list: each control hands on what to ask the API for.
 */
export function SortRow({
  sort,
  order,
  filters,
  onSort,
  onFlip,
  onFilter,
  onRemove,
}: {
  sort: LeaderboardSort;
  order: SortOrder;
  /** The filters that are set, as their chips. */
  filters: readonly SetFilter[];
  onSort: (sort: LeaderboardSort) => void;
  onFlip: () => void;
  /** Opens the filter sheet. */
  onFilter: () => void;
  onRemove: (names: readonly FilterName[]) => void;
}) {
  const OrderIcon = order === "desc" ? ArrowDownWideNarrowIcon : ArrowUpNarrowWideIcon;

  return (
    <div data-slot="sort-row" className="flex flex-col gap-3">
      <FilterChips label="Sort by" value={sort} onChange={onSort} chips={SORT_CHIPS}>
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
          {filters.length > 0 && (
            <>
              <Badge aria-hidden className="bg-foreground text-primary-foreground">
                {formatCount(filters.length)}
              </Badge>
              <span className="sr-only">, {formatCount(filters.length)} set</span>
            </>
          )}
        </Button>
      </FilterChips>

      {filters.length > 0 && (
        <ul aria-label="Filters that are set" className="flex flex-wrap gap-2">
          {filters.map((filter) => (
            // Every chip is in sight, and none is wider than the row.
            <li key={filter.id} className="flex max-w-full min-w-0">
              <RemovableChip onRemove={() => onRemove(filter.names)}>{filter.label}</RemovableChip>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
