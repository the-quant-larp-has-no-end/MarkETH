"use client";

import { useEffect, useEffectEvent } from "react";

import { useConfig } from "@/lib/api/session";

/** The longest wait one timer of a browser holds: a little under 25 days. */
const LONGEST_TIMER_MS = 2_147_483_647;

/**
 * The earliest of the given close times, in epoch milliseconds, or null when there is
 * none. Times are instants, not amounts: they are read and ordered.
 */
function earliest(closeTimes: readonly string[]): number | null {
  let first: number | null = null;
  for (const closeAt of closeTimes) {
    const at = Date.parse(closeAt);
    if (!Number.isNaN(at) && (first === null || at < first)) first = at;
  }
  return first;
}

/**
 * Asks the API again when a market that is shown as open for trading reaches its
 * `close_at` (DESIGN.md §14, "Close time"). No event says that this moment has passed, so
 * a screen that shows such a market sets one timer for it.
 *
 * `closeTimes` are the `close_at` of the markets the screen shows with `trading_open`
 * true, and `ask` reads them again. The timer is for the earliest of them. It is set again
 * when that time is another one, which is what an answer brings, and it is gone when no
 * such market is left or the screen goes away.
 *
 * The timer asks and decides nothing: what the screen shows afterwards is the `status`
 * and the `trading_open` of the answer. The clock of the device may run ahead of the
 * server's, and an answer may not arrive. So for as long as the same market is still
 * shown as open for trading after its close time, the question is asked again every
 * `live_poll_fallback_seconds` of `/config`.
 */
export function useAskAtClose(closeTimes: readonly string[], ask: () => void): void {
  const { live_poll_fallback_seconds } = useConfig();
  const closeAt = earliest(closeTimes);
  const again = live_poll_fallback_seconds * 1000;
  const askNow = useEffectEvent(ask);

  useEffect(() => {
    if (closeAt === null) return;
    let timer: number | undefined;

    const waitFor = (wait: number) => {
      timer = window.setTimeout(passed, Math.min(Math.max(wait, 0), LONGEST_TIMER_MS));
    };
    const passed = () => {
      const left = closeAt - Date.now();
      // Early only where the wait was longer than one timer holds.
      if (left > 0) return waitFor(left);
      askNow();
      waitFor(again);
    };

    waitFor(closeAt - Date.now());
    return () => window.clearTimeout(timer);
  }, [closeAt, again]);
}
