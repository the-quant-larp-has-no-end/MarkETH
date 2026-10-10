"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

/**
 * Portfolio (`/portfolio`): DESIGN.md §13. A stand-in until the screen is built: the name
 * and the props are final, the content is not.
 */
export function PortfolioScreen() {
  return (
    <Screen top="main" title="Portfolio">
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
