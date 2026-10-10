import type { Metadata } from "next";
import { Suspense } from "react";

import { AdminRiskScreen } from "@/features/admin";

export const metadata: Metadata = { title: "Risk" };

export default function Page() {
  return (
    <Suspense>
      <AdminRiskScreen />
    </Suspense>
  );
}
