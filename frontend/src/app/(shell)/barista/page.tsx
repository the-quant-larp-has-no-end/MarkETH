import type { Metadata } from "next";
import { Suspense } from "react";

import { BaristaScreen } from "@/features/barista";

export const metadata: Metadata = { title: "Scan" };

export default function Page() {
  return (
    <Suspense>
      <BaristaScreen />
    </Suspense>
  );
}
