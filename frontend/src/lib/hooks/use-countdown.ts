"use client";

import { useCallback, useSyncExternalStore } from "react";

import { type ApiError, retryAfterSeconds } from "../api/errors";

/** How often a running countdown looks at the clock. It renders only when a second passes. */
const LOOK_EVERY_MS = 250;

/**
 * Whole seconds left until `deadline` (epoch milliseconds), counting down to 0 and never
 * reaching it early. 0 when there is no deadline or it has passed.
 */
export function useSecondsUntil(deadline: number | null): number {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (deadline === null) return () => {};
      const timer = window.setInterval(() => {
        onChange();
        if (Date.now() >= deadline) window.clearInterval(timer);
      }, LOOK_EVERY_MS);
      return () => window.clearInterval(timer);
    },
    [deadline],
  );

  return useSyncExternalStore(
    subscribe,
    () => (deadline === null ? 0 : Math.max(0, Math.ceil((deadline - Date.now()) / 1000))),
    () => 0,
  );
}

/**
 * How long an action stays disabled after it was answered `RATE_LIMITED` (DESIGN.md §14):
 * the seconds left of the error's `details.retry_after_seconds`, counted from when the
 * answer arrived. 0 for any other error and for none.
 */
export function useRetryAfter(error: ApiError | null | undefined): number {
  const seconds = error ? retryAfterSeconds(error) : null;
  return useSecondsUntil(error && seconds !== null ? error.receivedAt + seconds * 1000 : null);
}
