"use client";

import { EmptyState } from "@/components/feedback";
import { Screen } from "@/components/shell";

/**
 * Profile (`/profile`): DESIGN.md §13. It shows the three fields of the set-up screen
 * (`ProfileForm` of `@/features/session`). A stand-in until the screen is built: the name
 * and the props are final, the content is not.
 */
export function ProfileScreen() {
  return (
    <Screen top="inner" title="Profile" width="narrow">
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
