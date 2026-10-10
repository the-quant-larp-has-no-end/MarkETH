"use client";

import { useEffect, useRef, useState } from "react";

/**
 * `value`, passed on at most once per `intervalMs`: the first change at once, later ones
 * when the interval is over, always ending on the latest. The live price is announced to
 * a screen reader through this (DESIGN.md §15).
 *
 * The interval runs from the last change that was passed on, not from the moment the
 * component appeared: a price that moves for the first time is said without waiting.
 */
export function useThrottledValue<T>(value: T, intervalMs: number): T {
  const [passed, setPassed] = useState(value);
  const passedAt = useRef<number | null>(null);

  useEffect(() => {
    // What stands is the value: there is nothing to pass on, and nothing to count from.
    if (Object.is(value, passed)) return;

    const wait =
      passedAt.current === null ? 0 : Math.max(0, passedAt.current + intervalMs - Date.now());
    const timer = window.setTimeout(() => {
      passedAt.current = Date.now();
      setPassed(value);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [value, passed, intervalMs]);

  return passed;
}
