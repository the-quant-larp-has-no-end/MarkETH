"use client";

import type { ReactNode } from "react";

import { EmptyState } from "@/components/feedback";
import { NotFoundScreen, Screen } from "@/components/shell";
import { useMe } from "@/lib/api/session";

const NOT_BUILT = "This screen is not built yet.";

/**
 * The admin area (DESIGN.md §13): for role ADMIN; anyone else sees the "not found"
 * screen, on every route under `/admin`. The check is for the look of it only: the API
 * decides what an admin may do. These are stand-ins until the screens are built: the
 * names and the props are final, the content is not.
 */
export function AdminLayout({ children }: { children: ReactNode }) {
  const { role } = useMe();
  if (role !== "ADMIN") return <NotFoundScreen />;
  return <>{children}</>;
}

/** Admin: the proposal queue and the markets (`/admin`). */
export function AdminMarketsScreen() {
  return (
    <Screen top="main" title="Admin">
      <EmptyState>{NOT_BUILT}</EmptyState>
    </Screen>
  );
}

export type AdminMarketScreenProps = {
  /** The `[id]` of the route `/admin/markets/[id]`. */
  marketId: string;
};

/** Admin: one market and its actions (`/admin/markets/[id]`). Its back arrow leads to Admin. */
export function AdminMarketScreen({ marketId }: AdminMarketScreenProps) {
  return (
    <Screen top="inner" backTo="/admin">
      <h1 className="sr-only">Market</h1>
      <EmptyState>{NOT_BUILT}</EmptyState>
      <span hidden data-market-id={marketId} />
    </Screen>
  );
}

/** Admin: code batches (`/admin/codes`). */
export function AdminCodesScreen() {
  return (
    <Screen top="main" title="Codes">
      <EmptyState>{NOT_BUILT}</EmptyState>
    </Screen>
  );
}

/** Admin: the risk panel (`/admin/risk`). */
export function AdminRiskScreen() {
  return (
    <Screen top="main" title="Risk">
      <EmptyState>{NOT_BUILT}</EmptyState>
    </Screen>
  );
}
