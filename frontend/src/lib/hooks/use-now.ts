"use client";

import { useCallback, useSyncExternalStore } from "react";

const noSubscription = () => () => {};

/**
 * The current time in epoch milliseconds, moving forward in steps of `stepMs`. It lets a
 * component show something that depends on the clock (a relative deadline, a countdown)
 * and still render the same thing twice in a row. Null on the server, where no clock is
 * the visitor's.
 *
 * With `enabled` false nothing ticks and the component is not rendered again.
 */
export function useNow(stepMs: number, enabled = true): number | null {
  const subscribe = useCallback(
    (onTick: () => void) => {
      const timer = window.setInterval(onTick, stepMs);
      return () => window.clearInterval(timer);
    },
    [stepMs],
  );
  return useSyncExternalStore(
    enabled ? subscribe : noSubscription,
    () => Math.floor(Date.now() / stepMs) * stepMs,
    () => null,
  );
}
