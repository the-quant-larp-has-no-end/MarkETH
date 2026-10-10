"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

/**
 * Propose market (`/propose`): DESIGN.md §13. A stand-in until the screen is built: the
 * name and the props are final, the content is not.
 */
export function ProposeScreen() {
  return (
    <Screen top="inner" title="Propose a market" width="narrow">
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
