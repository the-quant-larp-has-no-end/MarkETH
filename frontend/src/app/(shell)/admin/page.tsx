import type { Metadata } from "next";
import { Suspense } from "react";

import { AdminMarketsScreen } from "@/features/admin";

export const metadata: Metadata = { title: "Admin" };

export default function Page() {
  return (
    <Suspense>
      <AdminMarketsScreen />
    </Suspense>
  );
}
