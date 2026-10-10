"use client";

import { useEffect, useRef, useState } from "react";

/**
 * `value`, passed on at most once per `intervalMs`: the first change at once, later ones
 * when the interval is over, always ending on the latest. The live price is announced to
 * a screen reader through this (DESIGN.md §15).
 */
export function useThrottledValue<T>(value: T, intervalMs: number): T {
  const [passed, setPassed] = useState(value);
  const passedAt = useRef(0);

  useEffect(() => {
    const wait = Math.max(0, passedAt.current + intervalMs - Date.now());
    const timer = window.setTimeout(() => {
      passedAt.current = Date.now();
      setPassed(value);
    }, wait);
    return () => window.clearTimeout(timer);
  }, [value, intervalMs]);

  return passed;
}
