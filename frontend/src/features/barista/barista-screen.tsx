"use client";

import { EmptyState } from "@/components/feedback";
import { NotFoundScreen, Screen } from "@/components/shell";
import { useMe } from "@/lib/api/session";

/**
 * Barista (`/barista`): DESIGN.md §13. For roles BARISTA and ADMIN; anyone else sees the
 * "not found" screen. The check here is for the look of it only: the API decides who may
 * redeem a voucher. A stand-in until the screen is built: the name and the props are
 * final, the content is not.
 */
export function BaristaScreen() {
  const { role } = useMe();
  if (role !== "BARISTA" && role !== "ADMIN") return <NotFoundScreen />;

  return (
    <Screen top="main" title="Scan">
      <EmptyState>This screen is not built yet.</EmptyState>
    </Screen>
  );
}
