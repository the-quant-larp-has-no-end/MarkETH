"use client";

import { useEffect, useState } from "react";

/**
 * How far below the screen the observer looks, in CSS pixels: further than any page is
 * long. See `useScrolledPast`.
 */
const REACH_BELOW_PX = 1_000_000;

/**
 * Whether the page has been scrolled past `element`: it has left the screen at the top,
 * under the sticky top bar. False while it is in view and while it has not been reached
 * yet. Market detail pins its outcome buttons to the bottom of a phone's screen once this
 * is true (DESIGN.md §13).
 *
 * The observer watches everything from the lower edge of the top bar downwards, the part
 * of the page below the screen included. An element that has not been reached yet then
 * counts as seen, like one that is on screen, and stops counting only when it has left at
 * the top. That makes a jump a change the observer reports: a restored scroll position or
 * the End key takes the page from before the element to past it without ever showing it,
 * and an observer that watched the screen alone would see "not on screen" before and
 * after, and say nothing.
 *
 * `element` comes from a callback ref (`useState`), so that the hook starts watching when
 * the element appears.
 */
export function useScrolledPast(element: Element | null): boolean {
  const [seen, setSeen] = useState<{ element: Element; past: boolean } | null>(null);

  useEffect(() => {
    if (element === null || typeof IntersectionObserver === "undefined") return;

    // The top bar lies over the top of the page: what is under it has been passed.
    const bar = document.querySelector("[data-slot=top-bar]");
    const covered = bar === null ? 0 : Math.max(0, Math.round(bar.getBoundingClientRect().bottom));

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries.at(-1);
        if (entry === undefined) return;
        const top = entry.rootBounds?.top ?? covered;
        setSeen({
          element,
          past: !entry.isIntersecting && entry.boundingClientRect.bottom <= top,
        });
      },
      { rootMargin: `-${covered}px 0px ${REACH_BELOW_PX}px 0px` },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);

  // What was seen of another element says nothing about this one.
  return seen !== null && seen.element === element && seen.past;
}
