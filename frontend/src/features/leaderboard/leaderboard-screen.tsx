"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { EmptyState, LoadMore, QueryState } from "@/components/feedback";
import { LeaderboardRow, RowList, RowSkeletons } from "@/components/rows";
import { Screen } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { leaderboardQuery } from "@/lib/api/queries";
import type { LeaderboardEntry, LeaderboardSort } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { WIDE_QUERY } from "@/lib/design";
import { useMediaQuery } from "@/lib/hooks";

import { FilterSheet } from "./filter-sheet";
import { Podium, PodiumSkeleton, VIEWER_ROW } from "./podium";
import { RankingRows, RankingSkeleton, RankingTable } from "./ranking";
import { SortRow } from "./sort-row";
import { useLeaderboardView } from "./use-leaderboard-view";
import { useOnScreen } from "./use-on-screen";
import {
  arrange,
  type FilterName,
  type Filters,
  hasFigure,
  isPodiumRow,
  measureOf,
  requestOf,
  setFilters,
  viewerPlacement,
  withoutFilters,
} from "./view";

/**
 * An answer this young is the answer. "Show results" reads the list before the address
 * changes; the list that then opens on that address shows what was read and does not
 * ask for it a second time.
 */
const FRESH_MS = 1000;

/**
 * Leaderboard (`/leaderboard`): DESIGN.md §13. Polymarket's leaderboard, with the top
 * three turned into a podium. Top to bottom: the title and the line that states the
 * measure, the sort row with the filters that are set, the podium of a "Highest first"
 * list, every other row (a table from 768 px), and the viewer's own row pinned above the
 * tab bar whenever it is not on screen.
 *
 * The server ranks, sorts and filters. The sort, the order and every filter live in the
 * address and are the query of `GET /leaderboard`; the rows are shown in the order they
 * arrive, page after page, and nothing here reorders, filters, ranks or totals them.
 */
export function LeaderboardScreen() {
  const { degrees } = useConfig();
  const { view, show } = useLeaderboardView();
  const { sort, order } = view;
  const wide = useMediaQuery(WIDE_QUERY);
  const board = useInfiniteQuery({ ...leaderboardQuery(requestOf(view)), staleTime: FRESH_MS });
  const [filtering, setFiltering] = useState(false);
  const rankingRef = useRef<HTMLDivElement>(null);
  const barsRef = useRef<HTMLSpanElement>(null);

  const filters = setFilters(view.filters, degrees);
  const pages = board.data?.pages;
  const entries = pages?.flatMap((page) => page.items) ?? [];
  // Every page carries the viewer's row; the page read last is the freshest word on it.
  const viewer = pages?.[pages.length - 1]?.viewer ?? null;
  const arrangement = arrange(entries, sort, order);

  // The viewer's row is pinned whenever it is not on screen: when it is not among the
  // rows that are loaded, and when it is but has been scrolled away. Until the browser
  // has said which, the row stays as it was, so nothing blinks.
  const placement = viewerPlacement(arrangement);
  const seen = useOnScreen({
    container: rankingRef,
    selector: `.${VIEWER_ROW}`,
    placement: placement === null ? null : `${wide ? "table" : "rows"}:${placement}`,
    bars: barsRef,
  });
  const [wasPinned, setWasPinned] = useState(false);
  const pinned = viewer !== null && (placement === null || (seen === null ? wasPinned : !seen));
  if (pinned !== wasPinned) setWasPinned(pinned);

  // The bean drop plays once for a viewer who opens the unfiltered "Tokens" list in
  // first place (DESIGN.md §7), and not again when they come back to that list.
  const inFirstPlace =
    sort === "credits" && order === "desc" && filters.length === 0 && viewer?.rank === 1;
  const [drop, setDrop] = useState({ due: inFirstPlace, done: false });
  if (drop.due !== inFirstPlace) setDrop({ due: inFirstPlace, done: drop.done || !inFirstPlace });

  const sortBy = (next: LeaderboardSort) => show({ ...view, sort: next });
  const flip = () => show({ ...view, order: order === "desc" ? "asc" : "desc" });
  const remove = (names: readonly FilterName[]) =>
    show({ ...view, filters: withoutFilters(view.filters, names) });
  const apply = (next: Filters) => {
    show({ ...view, filters: next });
    setFiltering(false);
  };

  // In a "Highest first" list the players without a figure come last: when the first
  // row has none, nobody has.
  const first = entries[0];
  const nobodyRanked = order === "desc" && first !== undefined && !hasFigure(first, sort);

  return (
    <Screen
      top="main"
      width="narrow"
      pinned={
        pinned && viewer !== null ? (
          <YourRow entry={viewer} sort={sort} trophy={isPodiumRow(viewer, sort, order)} />
        ) : null
      }
    >
      {/* Measures the room the fixed bars take, for `useOnScreen`. It shows nothing. */}
      <span
        ref={barsRef}
        aria-hidden
        className="pointer-events-none invisible fixed size-0 pt-(--top-bar-space) pb-(--tab-bar-space)"
      />

      <header className="flex flex-col gap-1">
        <h1 className="type-h1">Leaderboard</h1>
        <p aria-live="polite" className="type-caption text-muted-foreground">
          {measureOf(sort).caption}
        </p>
      </header>

      <SortRow
        sort={sort}
        order={order}
        filters={filters}
        onSort={sortBy}
        onFlip={flip}
        onFilter={() => setFiltering(true)}
        onRemove={remove}
      />

      <QueryState
        query={board}
        skeleton={
          <div className="flex flex-col gap-4">
            {order === "desc" && <PodiumSkeleton />}
            <RankingSkeleton />
          </div>
        }
      >
        {() => {
          // Without filters the list always holds at least the viewer.
          if (entries.length === 0 && filters.length > 0) {
            return (
              <EmptyState
                action={
                  <Button variant="secondary" width="fit" onClick={() => apply({})}>
                    Clear all
                  </Button>
                }
              >
                Nobody matches these filters.
              </EmptyState>
            );
          }

          return (
            <div ref={rankingRef} data-slot="ranking" className="flex flex-col gap-4">
              {nobodyRanked && (
                <p className="type-body-sm text-muted-foreground">
                  Nobody here has a resolved market yet. The podium is yours to take.
                </p>
              )}
              {arrangement.podium !== null && (
                <Podium
                  rows={arrangement.podium}
                  sort={sort}
                  arrival={inFirstPlace && !drop.done ? "first-place" : null}
                />
              )}
              {arrangement.rows.length > 0 &&
                (wide ? (
                  <RankingTable
                    rows={arrangement.rows}
                    sort={sort}
                    order={order}
                    onSort={sortBy}
                    onFlip={flip}
                  />
                ) : (
                  <RankingRows rows={arrangement.rows} sort={sort} />
                ))}
              <LoadMore query={board} skeleton={<RowSkeletons count={1} />} />
            </div>
          );
        }}
      </QueryState>

      <FilterSheet
        open={filtering}
        onOpenChange={setFiltering}
        view={view}
        rejected={board.error?.code === "VALIDATION_ERROR" ? board.error : null}
        onApply={apply}
      />
    </Screen>
  );
}

/**
 * The viewer's own row, as it is pinned above the tab bar: the `viewer` row of the
 * answer, which is the viewer's place under the same sort and filters wherever it ranks.
 * One of the three podium rows carries its trophy beside the username.
 */
function YourRow({
  entry,
  sort,
  trophy,
}: {
  entry: LeaderboardEntry;
  sort: LeaderboardSort;
  trophy: boolean;
}) {
  return (
    <section aria-label="Your rank" data-slot="your-row">
      <RowList>
        <LeaderboardRow entry={entry} sort={sort} trophy={trophy} className="rounded-md" />
      </RowList>
    </section>
  );
}
