"use client";

import type { ReactNode } from "react";

import { Amount, Missing } from "@/components/money";
import { ResponsiveSheet, SheetBody } from "@/components/sheet";
import type { LeaderboardEntry } from "@/lib/api/schema";
import { useConfig } from "@/lib/api/session";
import { EN_DASH, formatCount, formatRate, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { measureOf } from "./view";

export type PlayerSheetProps = {
  /** The row that was selected, or null while none has been. */
  player: LeaderboardEntry | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/**
 * The statistics of one player (DESIGN.md §13): what opens when a row, a podium place or
 * the pinned row of the leaderboard is selected. A bottom sheet below 768 px and a dialog
 * from 768 px, with the close cross.
 *
 * Everything it shows is a field of that row's `LeaderboardEntry`, as the API sent it:
 * the sheet asks the API for nothing and works nothing out. The rank is the one of the
 * list as it is shown, under its sort and its filters. "Tokens" is `credits_cr`, which
 * the API makes of its two parts: `balance_cr` in the wallet and `positions_value_cr` in
 * open positions. The three stand here with every digit, so the parts are seen to make
 * the whole. The degree is the level by its `name` from `/config` and the programme as sent.
 */
export function PlayerSheet({ player, open, onOpenChange }: PlayerSheetProps) {
  const { degrees, price_display_decimals } = useConfig();
  if (player === null) return null;

  const level = degrees.find((degree) => degree.level === player.degree_level)?.name;
  const winRate = formatRate(player.win_rate, price_display_decimals);
  const payout = formatRatio(player.avg_payout_ratio);

  return (
    <ResponsiveSheet
      open={open}
      onOpenChange={onOpenChange}
      title={player.username}
      description={
        <>
          {player.is_viewer && "You · "}
          {level !== undefined && `${level} · `}
          {player.degree_programme}
        </>
      }
    >
      <SheetBody>
        <dl data-slot="player-statistics" className="flex flex-col gap-2 rounded-md bg-background p-3">
          <Figure label="Rank in this list">{formatCount(player.rank)}</Figure>
          <Figure label={measureOf("credits").name}>
            <Amount value={player.credits_cr} full />
          </Figure>
          <Figure label="In the wallet" part>
            <Amount value={player.balance_cr} full />
          </Figure>
          <Figure label="In open positions" part>
            <Amount value={player.positions_value_cr} full />
          </Figure>
          <Figure label={measureOf("win_rate").name}>
            {winRate === EN_DASH ? <Missing /> : winRate}
          </Figure>
          <Figure label={measureOf("avg_payout_ratio").name}>
            {payout === EN_DASH ? <Missing /> : payout}
          </Figure>
          <Figure label={measureOf("trades").name}>{formatCount(player.trades)}</Figure>
          <Figure label="Resolved markets">{formatCount(player.settled_markets)}</Figure>
        </dl>
      </SheetBody>
    </ResponsiveSheet>
  );
}

/**
 * One figure with its name: the name gives way and wraps, the figure keeps its width.
 * `part` is one of the two parts of the figure above it, set in from the left.
 */
function Figure({
  label,
  part = false,
  children,
}: {
  label: string;
  part?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex min-h-6 items-center justify-between gap-3", part && "pl-4")}>
      <dt className="min-w-0 type-body-sm text-muted-foreground">{label}</dt>
      <dd className="flex shrink-0 items-center justify-end type-number">{children}</dd>
    </div>
  );
}
