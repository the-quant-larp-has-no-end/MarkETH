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

import { SetFilters, SortChips, TitleRow } from "./board-header";
import { FilterSheet } from "./filter-sheet";
import { PlayerSheet } from "./player-sheet";
import { Podium, PodiumSkeleton, VIEWER_ROW } from "./podium";
import { RankingRows, RankingSkeleton, RankingTable } from "./ranking";
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
 * The room the content column keeps at its foot while the viewer's row is pinned
 * (DESIGN.md §13): as much as the bar is tall, and 16 px more, so the last row ends 16 px
 * above the bar. The bar is 73 px (a 1 px line, 8 px, the row of 56 px, 8 px) and stands
 * on the tab bar; from 768 px there is none, and the bar adds the safe area itself.
 */
const ROOM_FOR_YOUR_ROW =
  "pb-[calc(var(--tab-bar-space)+5.5rem+1px)] md:pb-[calc(5.5rem+1px+env(safe-area-inset-bottom))]";

/**
 * Leaderboard (`/leaderboard`): DESIGN.md §13. Polymarket's leaderboard, with the top
 * three turned into a podium. One column at every width, top to bottom: the title row,
 * the line that states the measure, the sort chips, the filters that are set, the podium
 * of a "Highest first" list, every other row (a table from 768 px), and the viewer's own
 * row in a bar at the foot of the screen whenever it is not in sight.
 *
 * The server ranks, sorts and filters. The sort, the order and every filter live in the
 * address and are the query of `GET /leaderboard`; the rows are shown in the order they
 * arrive, page after page, and nothing here reorders, filters, ranks or totals them.
 *
 * A row, a podium place and the pinned row are buttons: each opens the statistics of its
 * player, which are that row's fields and ask the API for nothing.
 */
export function LeaderboardScreen() {
  const { degrees } = useConfig();
  const { view, show } = useLeaderboardView();
  const { sort, order } = view;
  const wide = useMediaQuery(WIDE_QUERY);
  const board = useInfiniteQuery({ ...leaderboardQuery(requestOf(view)), staleTime: FRESH_MS });
  const [filtering, setFiltering] = useState(false);
  // Who was selected, by username, and whether the statistics are open. The figures are
  // read from the rows each time: the sheet holds no copy of them.
  const [selected, setSelected] = useState<string | null>(null);
  const [showingPlayer, setShowingPlayer] = useState(false);
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
  const select = (entry: LeaderboardEntry) => {
    setSelected(entry.username);
    setShowingPlayer(true);
  };

  // The selected player's row as the list holds it now; the pinned row is the `viewer`
  // of the answer, which stands in for a row that is not loaded.
  const player =
    selected === null
      ? null
      : (entries.find((entry) => entry.username === selected) ??
        (viewer?.username === selected ? viewer : null));

  // In a "Highest first" list the players without a figure come last: when the first
  // row has none, nobody has.
  const first = entries[0];
  const nobodyRanked = order === "desc" && first !== undefined && !hasFigure(first, sort);

  return (
    <Screen top="main" width="narrow" className={pinned ? ROOM_FOR_YOUR_ROW : undefined}>
      {/* Measures the room the fixed bars take, for `useOnScreen`. It shows nothing. */}
      <span
        ref={barsRef}
        aria-hidden
        className="pointer-events-none invisible fixed size-0 pt-(--top-bar-space) pb-(--tab-bar-space)"
      />

      <div data-slot="leaderboard" className="flex flex-col">
        <header className="flex flex-col gap-1">
          <TitleRow
            order={order}
            filterCount={filters.length}
            onFlip={flip}
            onFilter={() => setFiltering(true)}
          />
          <p
            aria-live="polite"
            data-slot="measure-line"
            className="type-caption text-muted-foreground"
          >
            {measureOf(sort).caption}
          </p>
        </header>

        <div className="mt-4">
          <SortChips sort={sort} wide={wide} onSort={sortBy} />
        </div>
        {filters.length > 0 && (
          <div className="mt-3">
            <SetFilters filters={filters} onRemove={remove} />
          </div>
        )}

        <div className="mt-4">
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
                      wide={wide}
                      arrival={inFirstPlace && !drop.done ? "first-place" : null}
                      onSelect={select}
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
                        onSelect={select}
                      />
                    ) : (
                      <RankingRows rows={arrangement.rows} sort={sort} onSelect={select} />
                    ))}
                  <LoadMore query={board} skeleton={<RowSkeletons count={1} />} />
                </div>
              );
            }}
          </QueryState>
        </div>
      </div>

      {pinned && viewer !== null && (
        <YourRow
          entry={viewer}
          sort={sort}
          trophy={isPodiumRow(viewer, sort, order)}
          onSelect={() => select(viewer)}
        />
      )}

      <FilterSheet
        open={filtering}
        onOpenChange={setFiltering}
        view={view}
        rejected={board.error?.code === "VALIDATION_ERROR" ? board.error : null}
        onApply={apply}
      />
      <PlayerSheet
        player={player}
        open={showingPlayer && player !== null}
        onOpenChange={setShowingPlayer}
      />
    </Screen>
  );
}

/**
 * The viewer's own row, pinned (DESIGN.md §13): the `viewer` row of the answer, which is
 * the viewer's place under the same sort and filters wherever it ranks. It stands in a
 * bar across the whole width of the window, on the tab bar below 768 px and at the lower
 * edge of the window from 768 px: `--card` with a 1 px line on top and 8 px above and
 * below the row, 73 px tall, plus the safe area where no tab bar lies under it. Inside
 * the bar the row stands in the content column. One of the three podium rows carries its
 * trophy.
 *
 * Rows pass under the bar while the list scrolls, as they pass under the tab bar; the
 * screen keeps room at its foot, so the bar never rests on the last one.
 */
function YourRow({
  entry,
  sort,
  trophy,
  onSelect,
}: {
  entry: LeaderboardEntry;
  sort: LeaderboardSort;
  trophy: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      data-slot="your-row"
      className="fixed inset-x-0 bottom-(--tab-bar-space) z-30 border-t bg-card py-2 md:pb-[calc(0.5rem+env(safe-area-inset-bottom))] print:hidden"
    >
      <section aria-label="Your rank" className="mx-auto w-full px-4 md:max-w-[45rem]">
        <RowList>
          <LeaderboardRow entry={entry} sort={sort} trophy={trophy} pinned onSelect={onSelect} />
        </RowList>
      </section>
    </div>
  );
}
