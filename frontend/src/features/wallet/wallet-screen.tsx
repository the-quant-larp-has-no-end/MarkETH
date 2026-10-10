"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

/**
 * Wallet (`/wallet`): DESIGN.md §13. A stand-in until the screen is built: the name and the
 * props are final, the content is not.
 */
export function WalletScreen() {
  return (
    <Screen top="main" title="Wallet">
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
