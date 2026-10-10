import type { Metadata } from "next";
import { Suspense } from "react";

import { ProposeScreen } from "@/features/propose";

export const metadata: Metadata = { title: "Propose a market" };

export default function Page() {
  return (
    <Suspense>
      <ProposeScreen />
    </Suspense>
  );
}
