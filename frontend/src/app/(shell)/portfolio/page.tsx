import type { Metadata } from "next";
import { Suspense } from "react";

import { PortfolioScreen } from "@/features/portfolio";

export const metadata: Metadata = { title: "Portfolio" };

export default function Page() {
  return (
    <Suspense>
      <PortfolioScreen />
    </Suspense>
  );
}
