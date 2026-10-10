import type { LeaderboardEntry, LeaderboardResponse } from "@/lib/api/schema";
import { leaderboardEntry } from "@/test/fixtures";

/**
 * Rows of `GET /leaderboard` for the tests of this feature. Every row carries the two
 * parts of its tokens, which the statistics of a player show: `credits_cr` is what the
 * API makes of `balance_cr` and `positions_value_cr`, and a test writes all three as the
 * strings it expects on screen. Nothing here adds them up.
 */
export function player(
  rank: number,
  username: string,
  overrides: Partial<LeaderboardEntry> = {},
): LeaderboardEntry {
  return {
    ...leaderboardEntry(),
    credits_cr: "1204.5",
    balance_cr: "1000",
    positions_value_cr: "204.5",
    rank,
    username,
    ...overrides,
  };
}

/** The viewer's own row: the one the API marks `is_viewer`. */
export function you(rank: number, overrides: Partial<LeaderboardEntry> = {}): LeaderboardEntry {
  return player(rank, "anna_m", {
    is_viewer: true,
    credits_cr: "240",
    balance_cr: "200",
    positions_value_cr: "40",
    trades: 4,
    ...overrides,
  });
}

/** Four ranked players; the viewer is third and has no resolved market yet. */
export function ranking(overrides: Partial<LeaderboardResponse> = {}): LeaderboardResponse {
  const viewer = you(3, { settled_markets: 0, win_rate: null, avg_payout_ratio: null });
  return {
    items: [
      player(1, "polyterrasse"),
      player(2, "cab_coffee", {
        credits_cr: "980",
        balance_cr: "980",
        positions_value_cr: "0",
        degree_level: "MASTER",
        degree_programme: "MSc Data Science",
      }),
      viewer,
      player(4, "mensa_fan", {
        credits_cr: "120.25",
        balance_cr: "100",
        positions_value_cr: "20.25",
        trades: 1,
      }),
    ],
    next_cursor: null,
    viewer,
    ...overrides,
  };
}
