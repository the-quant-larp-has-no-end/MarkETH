"use client";

import type { ReactNode } from "react";

import type { ApiError } from "@/lib/api/errors";

import { ErrorBanner } from "./banner";

/** What `useQuery` and `useInfiniteQuery` return, as far as this component looks. */
type Loadable<T> = {
  data: T | undefined;
  error: ApiError | null;
  refetch: () => unknown;
};

/**
 * Three of the four states every screen has (DESIGN.md §14): loading, error and content.
 * The fourth, empty, is the screen's to decide inside `children`.
 *
 *     <QueryState query={useQuery(positionsQuery)} skeleton={<PortfolioSkeleton />}>
 *       {(portfolio) => ...}
 *     </QueryState>
 *
 * While there is no data yet it shows `skeleton`; when the read failed, a banner with the
 * API's message and "Try again". Data that is on screen stays there while it is read
 * again and when reading it again fails: a stale figure is better than none.
 *
 * `onError` replaces the banner for an error the screen treats in its own words, inside
 * its frame. An error that replaces the whole screen is not for here: a market the API
 * answers `NOT_FOUND` for is `return <NotFoundScreen />` before the screen renders its
 * `Screen`, because the "not found" screen brings a frame of its own.
 */
export function QueryState<T>({
  query,
  skeleton,
  children,
  onError,
}: {
  query: Loadable<T>;
  skeleton: ReactNode;
  children: (data: T) => ReactNode;
  /** Return what to show for this error, or undefined for the banner. */
  onError?: (error: ApiError) => ReactNode | undefined;
}) {
  if (query.data !== undefined) return <>{children(query.data)}</>;

  if (query.error !== null) {
    const own = onError?.(query.error);
    if (own !== undefined) return <>{own}</>;
    return <ErrorBanner error={query.error} onRetry={() => void query.refetch()} />;
  }

  return <>{skeleton}</>;
}
