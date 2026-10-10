"use client";

import { rankLabel, Trophy } from "@/components/brand";
import { BeanDrop } from "@/components/feedback";
import { LeaderboardFigure } from "@/components/rows";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { LeaderboardEntry, LeaderboardSort } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

import { PODIUM_PLACES, type PodiumPlace, type PodiumRows } from "./view";

/** The class of the element that shows the viewer's own row, wherever that is drawn. */
export const VIEWER_ROW = "viewer-row";

/* First place stands in the middle, second on its left, third on its right. The list
   itself runs first, second, third, which is how it is read out. */
const COLUMN: Record<PodiumPlace, string> = { 1: "order-2", 2: "order-1", 3: "order-3" };
/* The steps: flat blocks of three heights. */
const STEP: Record<PodiumPlace, string> = { 1: "h-12", 2: "h-8", 3: "h-5" };
/* Three columns. On a phone the middle one is as wide as its figure, which is the largest
   of the three and must never run into its neighbours; the other two share the rest.
   From 768 px there is room for three equal columns. */
const COLUMNS =
  "grid grid-cols-[minmax(0,1fr)_minmax(6rem,auto)_minmax(0,1fr)] items-end gap-2 md:grid-cols-3";

/**
 * The podium of a "Highest first" list (DESIGN.md §13): three columns on one card for
 * the rows of `rank` 1, 2 and 3, each with its trophy, the rank as text, the username
 * and the figure of the sorted measure. The steps are flat `--raised` blocks with square
 * corners, standing on the lower edge of the card: pixel-art blocks, not rounded cards.
 *
 * A place nobody stands on is the outline of its step and nothing else. `arrival` lets
 * the beans drop once into the first-place figure (DESIGN.md §7).
 */
export function Podium({
  rows,
  sort,
  arrival,
}: {
  rows: PodiumRows;
  sort: LeaderboardSort;
  /** Set while the viewer opens the unfiltered "Tokens" list in first place, otherwise null. */
  arrival: string | null;
}) {
  return (
    <Card data-slot="podium" className="pb-0">
      <ol aria-label="Top three" className={COLUMNS}>
        {PODIUM_PLACES.map((place, index) => (
          <Place
            key={place}
            place={place}
            entry={rows[index] ?? null}
            sort={sort}
            arrival={place === 1 ? arrival : null}
          />
        ))}
      </ol>
    </Card>
  );
}

function Place({
  place,
  entry,
  sort,
  arrival,
}: {
  place: PodiumPlace;
  entry: LeaderboardEntry | null;
  sort: LeaderboardSort;
  arrival: string | null;
}) {
  if (entry === null) {
    return (
      <li aria-hidden data-slot="podium-place" data-empty className={cn("min-w-0", COLUMN[place])}>
        <div className={cn("border border-b-0 border-subtle", STEP[place])} />
      </li>
    );
  }

  const first = place === 1;
  const figure = <LeaderboardFigure entry={entry} sort={sort} size={first ? "lg" : "md"} />;

  return (
    <li
      data-slot="podium-place"
      className={cn(
        "flex min-w-0 flex-col items-center gap-1 text-center",
        COLUMN[place],
        entry.is_viewer && VIEWER_ROW,
      )}
    >
      <Trophy place={place} size={first ? 96 : 72} eager />
      <span className="type-caption text-muted-foreground">{rankLabel(place)}</span>
      {/* One line, as wide as the column and no wider: a long name is cut, and does not
          widen its column. */}
      <span title={entry.username} className="w-0 min-w-full truncate type-h3">
        {entry.username}
      </span>
      {entry.is_viewer && <span className="type-caption text-muted-foreground">You</span>}
      {first ? <BeanDrop arrival={arrival}>{figure}</BeanDrop> : figure}
      <div aria-hidden className={cn("mt-1 w-full bg-raised", STEP[place])} />
    </li>
  );
}

/** The podium while the list loads: the card, with blocks where the three places will be. */
export function PodiumSkeleton() {
  return (
    <Card data-slot="podium-skeleton" aria-hidden className="pb-0">
      <div className={COLUMNS}>
        {PODIUM_PLACES.map((place) => (
          <div key={place} className={cn("flex flex-col items-center gap-2", COLUMN[place])}>
            <Skeleton className={place === 1 ? "size-24" : "size-18"} />
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className={cn("w-full rounded-none", STEP[place])} />
          </div>
        ))}
      </div>
    </Card>
  );
}
