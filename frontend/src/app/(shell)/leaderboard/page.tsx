import type { Metadata } from "next";
import { Suspense } from "react";

import { LeaderboardScreen } from "@/features/leaderboard";

export const metadata: Metadata = { title: "Leaderboard" };

export default function Page() {
  return (
    <Suspense>
      <LeaderboardScreen />
    </Suspense>
  );
}
