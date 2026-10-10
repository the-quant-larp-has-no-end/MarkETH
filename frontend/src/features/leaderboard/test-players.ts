import type { LeaderboardEntry } from "@/lib/api/schema";
import { leaderboardEntry } from "@/test/fixtures";

/**
 * Shorthand for the rows of `GET /leaderboard` in the tests of this feature: a row is
 * named by its rank and its username, and everything else is the fixture's. The list of
 * four with the viewer third is `leaderboard()` of `@/test/fixtures`.
 *
 * `credits_cr` is what the API makes of `balance_cr` and `positions_value_cr`, and a test
 * writes all three as the strings it expects on screen. Nothing here adds them up.
 */
export function player(
  rank: number,
  username: string,
  overrides: Partial<LeaderboardEntry> = {},
): LeaderboardEntry {
  return leaderboardEntry({ rank, username, ...overrides });
}

/** The viewer's own row, the one the API marks `is_viewer`, with a record of its own. */
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
