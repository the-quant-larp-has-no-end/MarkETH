"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

/**
 * Leaderboard (`/leaderboard`): DESIGN.md §13. A stand-in until the screen is built: the
 * name and the props are final, the content is not.
 */
export function LeaderboardScreen() {
  return (
    <Screen top="main">
      <h1 className="type-h1">Leaderboard</h1>
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
