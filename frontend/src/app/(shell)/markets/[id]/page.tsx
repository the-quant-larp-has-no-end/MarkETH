import type { Metadata } from "next";
import { Suspense } from "react";

import { MarketDetailScreen } from "@/features/market-detail";

export const metadata: Metadata = { title: "Market" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  return (
    <Suspense>
      <MarketDetailScreen marketId={id} />
    </Suspense>
  );
}
