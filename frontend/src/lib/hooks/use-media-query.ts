"use client";

import { useCallback, useSyncExternalStore } from "react";

import { REDUCED_MOTION_QUERY } from "../design";

/**
 * Whether a media query matches, kept current. False on the server and until the page
 * has hydrated, so a layout starts as the phone layout (DESIGN.md §2, principle 1).
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Whether the visitor asked for less movement (DESIGN.md §7). */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery(REDUCED_MOTION_QUERY);
}
