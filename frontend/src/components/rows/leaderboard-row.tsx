"use client";

import { Trophy, type TrophyPlace } from "@/components/brand";
import { Amount, Missing } from "@/components/money";
import type { LeaderboardEntry, LeaderboardSort } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { EN_DASH, formatCount, formatRate, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RowItem, rowPressClasses } from "./list-row";

/* A figure that may be wider than the column it stands in gives way inside it: the amount
   goes under its token icon, and breaks where it has to. Nothing is cut. */
const amountWrapClasses =
  "max-w-full flex-wrap justify-center gap-y-0 whitespace-normal text-center [&>[aria-hidden]]:min-w-0 [&>[aria-hidden]]:wrap-anywhere";
const textWrapClasses = "max-w-full wrap-anywhere text-center";

/**
 * The figure of one measure of a leaderboard row (DESIGN.md §13), as the server sends
 * it. A player without a resolved market has no win rate and no payout ratio: the figure
 * is an en dash, read out as "none yet".
 *
 * In a row and in a table the figure stands on one line and keeps its width. With `wrap`
 * it is never wider than what holds it: the figure of a podium place, whose column is
 * narrow on a phone.
 */
export function LeaderboardFigure({
  entry,
  sort,
  size = "md",
  wrap = false,
  className,
}: {
  entry: LeaderboardEntry;
  /** The measure to show: the one the list is sorted by, or a column of the table. */
  sort: LeaderboardSort;
  /** `lg` is the first place of the podium from 768 px. */
  size?: "md" | "lg";
  wrap?: boolean;
  className?: string;
}) {
  const { price_display_decimals } = useConfig();
  const text = cn(
    size === "lg" ? "type-number-lg" : "type-number",
    wrap ? textWrapClasses : "whitespace-nowrap",
    className,
  );

  if (sort === "credits") {
    return (
      <Amount
        value={entry.credits_cr}
        size={size}
        className={cn(wrap && amountWrapClasses, className)}
      />
    );
  }
  if (sort === "trades") return <span className={text}>{formatCount(entry.trades)}</span>;

  const figure =
    sort === "win_rate"
      ? formatRate(entry.win_rate, price_display_decimals)
      : formatRatio(entry.avg_payout_ratio);
  return figure === EN_DASH ? <Missing className={text} /> : <span className={text}>{figure}</span>;
}

function podiumPlace(rank: number): TrophyPlace | null {
  return rank === 1 || rank === 2 || rank === 3 ? rank : null;
}

/* The rank in a column of 32 px, which a rank of five digits widens; 12 px; the text,
   which gives way; 12 px; the figure, which keeps its width. */
const COLUMNS = "grid-cols-[minmax(2rem,auto)_minmax(0,1fr)_auto]";
const COLUMNS_WITH_TROPHY = "grid-cols-[minmax(2rem,auto)_1.5rem_minmax(0,1fr)_auto]";

/**
 * One row of the leaderboard (DESIGN.md §12, §13), at least 56 px tall: the rank, the
 * username on one line with the degree programme below on up to two, and at the right
 * the figure of the measure the list is sorted by. A name that is too long is cut with an
 * ellipsis; the figure never is, and the text stays 12 px away from it. The row the API
 * marks `is_viewer` has a `--raised` background and the label "You" beside the username.
 * Rank, username and figure are shown as sent: a row is never reordered, and nobody's
 * real name or address appears here.
 *
 * With `onSelect` the row is a button, named by what it shows: a tap, Enter or Space
 * selects the player, and the screen opens that player's statistics.
 *
 * `pinned` is the viewer's row in the bar at the foot of the screen: 56 px tall with
 * `--radius-md`, the programme on one line. `trophy` puts the 24 px trophy between the
 * rank and the username. It is for the pinned row of a viewer who is on the podium of a
 * "Highest first" list, and for nothing else.
 */
export function LeaderboardRow({
  entry,
  sort,
  trophy = false,
  pinned = false,
  onSelect,
  className,
}: {
  entry: LeaderboardEntry;
  sort: LeaderboardSort;
  trophy?: boolean;
  pinned?: boolean;
  onSelect?: () => void;
  className?: string;
}) {
  const place = trophy ? podiumPlace(entry.rank) : null;
  const frame = cn(
    "grid min-h-14 w-full items-center gap-x-3 p-2 text-left",
    place === null ? COLUMNS : COLUMNS_WITH_TROPHY,
    pinned && "rounded-md",
  );

  const parts = (
    <>
      <span className="text-right type-number whitespace-nowrap text-muted-foreground">
        <span className="sr-only">Rank </span>
        {formatCount(entry.rank)}
      </span>
      {place !== null && <Trophy place={place} size={24} />}
      <span className="flex min-w-0 flex-col">
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
          className={cn(
            "type-caption text-muted-foreground",
            pinned ? "truncate" : "line-clamp-2 wrap-break-word",
          )}
        >
          {entry.degree_programme}
        </span>
      </span>
      <LeaderboardFigure entry={entry} sort={sort} />
    </>
  );

  return (
    <RowItem className={cn(entry.is_viewer && "bg-raised", pinned && "rounded-md", className)}>
      {onSelect === undefined ? (
        <div className={frame}>{parts}</div>
      ) : (
        <button
          type="button"
          aria-haspopup="dialog"
          onClick={onSelect}
          className={cn(frame, rowPressClasses)}
        >
          {parts}
        </button>
      )}
    </RowItem>
  );
}
