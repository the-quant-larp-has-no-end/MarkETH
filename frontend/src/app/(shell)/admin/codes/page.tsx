import type { Metadata } from "next";
import { Suspense } from "react";

import { AdminCodesScreen } from "@/features/admin";

export const metadata: Metadata = { title: "Codes" };

export default function Page() {
  return (
    <Suspense>
      <AdminCodesScreen />
    </Suspense>
  );
}
