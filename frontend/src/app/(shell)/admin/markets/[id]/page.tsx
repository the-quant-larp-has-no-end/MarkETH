import type { Metadata } from "next";
import { Suspense } from "react";

import { AdminMarketScreen } from "@/features/admin";

export const metadata: Metadata = { title: "Market" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  return (
    <Suspense>
      <AdminMarketScreen marketId={id} />
    </Suspense>
  );
}
