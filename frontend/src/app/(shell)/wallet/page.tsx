import type { Metadata } from "next";
import { Suspense } from "react";

import { WalletScreen } from "@/features/wallet";

export const metadata: Metadata = { title: "Wallet" };

export default function Page() {
  return (
    <Suspense>
      <WalletScreen />
    </Suspense>
  );
}
