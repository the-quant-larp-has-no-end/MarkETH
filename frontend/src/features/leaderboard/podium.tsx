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

/* Three columns of equal width, 8 px apart, aligned at their lower edge: the steps stand
   on the lower edge of the card and first place stands highest. */
const COLUMNS = "grid grid-cols-3 items-end gap-2";
/* First place stands in the middle, second on its left, third on its right. The list
   itself runs first, second, third, which is how it is read out. */
const COLUMN: Record<PodiumPlace, string> = { 1: "order-2", 2: "order-1", 3: "order-3" };
/* The steps: flat blocks of three heights. */
const STEP: Record<PodiumPlace, string> = { 1: "h-12", 2: "h-8", 3: "h-5" };
/* The trophies: 96 px for first place and 72 px for the others, and never wider than the
   column, which on a narrow phone is less than 96 px. */
const TROPHY: Record<PodiumPlace, string> = { 1: "w-24", 2: "w-18", 3: "w-18" };

/**
 * The podium of a "Highest first" list (DESIGN.md §13): one card with three columns of
 * equal width for the rows of `rank` 1, 2 and 3. Each column holds its trophy, the rank
 * as text, the username on one line, "You" for the viewer, the figure of the sorted
 * measure on a line of its own, and its step: a flat `--raised` block with square
 * corners. Pixel-art blocks, not rounded cards.
 *
 * No text of a column is wider than the column. A name that is too long is cut with an
 * ellipsis and read whole; a figure that is too wide wraps inside its column and is never
 * cut. So nothing reaches into the next column, whatever a name or a balance is.
 *
 * A place somebody stands on is a button: it selects that player. A place nobody stands
 * on is the outline of its step and nothing else. `arrival` lets the beans drop once into
 * the first-place figure (DESIGN.md §7).
 */
export function Podium({
  rows,
  sort,
  wide,
  arrival,
  onSelect,
}: {
  rows: PodiumRows;
  sort: LeaderboardSort;
  /** From 768 px: the username in `h3`, the first-place figure in `number-lg`. */
  wide: boolean;
  /** Set while the viewer opens the unfiltered "Tokens" list in first place, otherwise null. */
  arrival: string | null;
  onSelect: (entry: LeaderboardEntry) => void;
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
            wide={wide}
            arrival={place === 1 ? arrival : null}
            onSelect={onSelect}
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
  wide,
  arrival,
  onSelect,
}: {
  place: PodiumPlace;
  entry: LeaderboardEntry | null;
  sort: LeaderboardSort;
  wide: boolean;
  arrival: string | null;
  onSelect: (entry: LeaderboardEntry) => void;
}) {
  if (entry === null) {
    return (
      <li aria-hidden data-slot="podium-place" data-empty className={cn("min-w-0", COLUMN[place])}>
        <div className={cn("border border-b-0 border-subtle", STEP[place])} />
      </li>
    );
  }

  const first = place === 1;
  const figure = <LeaderboardFigure entry={entry} sort={sort} size={first && wide ? "lg" : "md"} wrap />;

  return (
    <li
      data-slot="podium-place"
      className={cn("min-w-0", COLUMN[place], entry.is_viewer && VIEWER_ROW)}
    >
      <button
        type="button"
        aria-haspopup="dialog"
        onClick={() => onSelect(entry)}
        className={cn(
          "flex w-full min-w-0 flex-col items-center gap-1 rounded-sm text-center",
          "transition-opacity duration-(--duration-fast) hover:opacity-90 active:opacity-90",
        )}
      >
        <span className={cn("block aspect-square max-w-full", TROPHY[place])}>
          <Trophy place={place} size={first ? 96 : 72} eager className="size-full" />
        </span>
        <span className="type-caption text-muted-foreground">{rankLabel(place)}</span>
        <span title={entry.username} className="w-full truncate type-label md:type-h3">
          {entry.username}
        </span>
        {entry.is_viewer && <span className="type-caption text-muted-foreground">You</span>}
        {first ? (
          <BeanDrop arrival={arrival} className="max-w-full">
            {figure}
          </BeanDrop>
        ) : (
          figure
        )}
        <span
          aria-hidden
          data-slot="podium-step"
          className={cn("mt-1 block w-full bg-raised", STEP[place])}
        />
      </button>
    </li>
  );
}

/** The podium while the list loads: the card, with blocks where the three places will be. */
export function PodiumSkeleton() {
  return (
    <Card data-slot="podium-skeleton" aria-hidden className="pb-0">
      <div className={COLUMNS}>
        {PODIUM_PLACES.map((place) => (
          <div
            key={place}
            className={cn("flex min-w-0 flex-col items-center gap-2", COLUMN[place])}
          >
            <Skeleton className={cn("aspect-square max-w-full", TROPHY[place])} />
            <Skeleton className="h-5 w-2/3" />
            <Skeleton className={cn("w-full rounded-none", STEP[place])} />
          </div>
        ))}
      </div>
    </Card>
  );
}
