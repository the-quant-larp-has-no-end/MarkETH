"use client";

import { Trophy, type TrophyPlace } from "@/components/brand";
import { Amount } from "@/components/money";
import type { LeaderboardEntry, LeaderboardSort } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { EN_DASH, formatCount, formatRate, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { rowClasses, RowFigure, RowItem, RowText } from "./list-row";

/**
 * The figure of one measure of a leaderboard row (DESIGN.md §13), as the server sends
 * it. A player without a resolved market has no win rate and no payout ratio: the figure
 * is an en dash, read out as "none yet".
 */
export function LeaderboardFigure({
  entry,
  sort,
  size = "md",
  className,
}: {
  entry: LeaderboardEntry;
  /** The measure to show: the one the list is sorted by, or a column of the table. */
  sort: LeaderboardSort;
  /** `lg` is the first place of the podium. */
  size?: "md" | "lg";
  className?: string;
}) {
  const { price_display_decimals } = useConfig();
  const text = cn(size === "lg" ? "type-number-lg" : "type-number", className);

  if (sort === "credits")
    return <Amount value={entry.credits_cr} size={size} className={className} />;
  if (sort === "trades") return <span className={text}>{formatCount(entry.trades)}</span>;

  const figure =
    sort === "win_rate"
      ? formatRate(entry.win_rate, price_display_decimals)
      : formatRatio(entry.avg_payout_ratio);
  if (figure !== EN_DASH) return <span className={text}>{figure}</span>;

  return (
    <span className={text}>
      <span aria-hidden>{EN_DASH}</span>
      <span className="sr-only">none yet</span>
    </span>
  );
}

function podiumPlace(rank: number): TrophyPlace | null {
  return rank === 1 || rank === 2 || rank === 3 ? rank : null;
}

/**
 * One row of the leaderboard (DESIGN.md §12, §13): the rank, the username with the
 * degree programme below, and the figure of the measure the list is sorted by. The row
 * the API marks `is_viewer` has a `--raised` background and the label "You". Rank,
 * username and figure are shown as sent: a row is never reordered, and nobody's real
 * name or address appears here.
 *
 * `trophy` puts the 24 px trophy beside the username. It is for the pinned row of a
 * viewer who is on the podium of a "Highest first" list, and for nothing else.
 */
export function LeaderboardRow({
  entry,
  sort,
  trophy = false,
  className,
}: {
  entry: LeaderboardEntry;
  sort: LeaderboardSort;
  trophy?: boolean;
  className?: string;
}) {
  const place = trophy ? podiumPlace(entry.rank) : null;

  return (
    <RowItem className={cn(entry.is_viewer && "bg-raised", className)}>
      <div className={cn(rowClasses, "px-2")}>
        <span className="flex min-w-0 items-center gap-3">
          <span className="w-8 shrink-0 text-right type-number text-muted-foreground">
            <span className="sr-only">Rank </span>
            {formatCount(entry.rank)}
          </span>
          {place !== null && <Trophy place={place} size={24} />}
          <RowText
            title={
              <>
                {entry.username}
                {entry.is_viewer && (
                  <span className="ml-2 type-caption text-muted-foreground">You</span>
                )}
              </>
            }
            below={entry.degree_programme}
          />
        </span>
        <RowFigure>
          <LeaderboardFigure entry={entry} sort={sort} />
        </RowFigure>
      </div>
    </RowItem>
  );
}
