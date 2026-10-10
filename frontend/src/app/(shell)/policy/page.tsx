import type { Metadata } from "next";
import { Suspense } from "react";

import { PolicyScreen } from "@/features/policy";

export const metadata: Metadata = { title: "Usage policy" };

export default function Page() {
  return (
    <Suspense>
      <PolicyScreen />
    </Suspense>
  );
}
