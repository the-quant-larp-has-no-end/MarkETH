import type { Metadata } from "next";
import { Suspense } from "react";

import { BroadcastStudioScreen } from "@/features/broadcast-studio";

export const metadata: Metadata = { title: "Live stream" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  return (
    <Suspense>
      <BroadcastStudioScreen marketId={id} />
    </Suspense>
  );
}
