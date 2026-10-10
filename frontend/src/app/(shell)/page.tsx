import type { Metadata } from "next";
import { Suspense } from "react";

import { MarketsScreen } from "@/features/markets";

export const metadata: Metadata = { title: "Markets" };

export default function Page() {
  return (
    <Suspense>
      <MarketsScreen />
    </Suspense>
  );
}
