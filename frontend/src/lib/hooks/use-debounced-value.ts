"use client";

import { useEffect, useState } from "react";

/**
 * `value`, once it has stopped changing for `delayMs`. The trade sheet asks for a quote
 * `quote_debounce_ms` after the last change of its input (DESIGN.md §13).
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);

  return settled;
}
