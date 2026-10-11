"use client";

import { useEffect, useState } from "react";

/**
 * How far below the screen the observer looks, in CSS pixels: further than any page is
 * long. See `useScrolledPast`.
 */
const REACH_BELOW_PX = 1_000_000;

/**
 * What lies over the top of the page and stays there while the page scrolls: the top bar,
 * and right under it the video box of a live stream while its viewer watches on a phone
 * (`MarketBroadcast` of `@/features/broadcast`).
 */
const STICKS_AT_THE_TOP = "[data-slot=top-bar], [data-slot=live-stream-box]";

/**
 * The part of an element that counts as all of it. An engine that rounds says a little
 * less than 1 of an element that is on screen whole.
 */
const WHOLE = 0.99;

/** How far down from the top of the screen the page is covered, in CSS pixels. */
function coveredAtTheTop(): number {
  let covered = 0;
  for (const cover of document.querySelectorAll(STICKS_AT_THE_TOP)) {
    covered = Math.max(covered, Math.round(cover.getBoundingClientRect().bottom));
  }
  return covered;
}

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
 * With `orBelow` the answer is true whenever the element is out of view, above or below:
 * whenever it is not on screen whole. Market detail asks that while its viewer watches
 * the live stream on a phone, where the outcome buttons are on screen at every scroll
 * position (DESIGN.md §13, "Watching on a phone"). The observer then watches the screen
 * alone, from the lower edge of what sticks at its top, the video box, to its lower edge.
 * A jump is no trouble there: "out of view" is the answer before it and after it.
 *
 * What sticks at the top is measured when the observer is made, and again when the window
 * changes its size: the video box follows the width and the height of the screen, so a
 * phone that is turned moves its lower edge. When `orBelow` changes, the answer of before
 * stands until the observer has looked, so that nothing pinned goes and comes back.
 *
 * `element` comes from a callback ref (`useState`), so that the hook starts watching when
 * the element appears.
 */
export function useScrolledPast(
  element: Element | null,
  { orBelow = false }: { orBelow?: boolean } = {},
): boolean {
  const [seen, setSeen] = useState<{ element: Element; past: boolean } | null>(null);

  useEffect(() => {
    if (element === null || typeof IntersectionObserver === "undefined") return;

    let observer: IntersectionObserver | undefined;
    const watch = () => {
      observer?.disconnect();
      // What sticks lies over the top of the page: what is under it has been passed.
      const covered = coveredAtTheTop();

      observer = new IntersectionObserver(
        (entries) => {
          const entry = entries.at(-1);
          if (entry === undefined) return;
          const top = entry.rootBounds?.top ?? covered;
          const past = orBelow
            ? entry.intersectionRatio < WHOLE
            : !entry.isIntersecting && entry.boundingClientRect.bottom <= top;
          // The same answer once more, after the window changed its size, is no news.
          setSeen((was) =>
            was?.element === element && was.past === past ? was : { element, past },
          );
        },
        orBelow
          ? { rootMargin: `-${covered}px 0px 0px 0px`, threshold: WHOLE }
          : { rootMargin: `-${covered}px 0px ${REACH_BELOW_PX}px 0px` },
      );
      observer.observe(element);
    };

    watch();
    window.addEventListener("resize", watch);
    return () => {
      window.removeEventListener("resize", watch);
      observer?.disconnect();
    };
  }, [element, orBelow]);

  // What was seen of another element says nothing about this one.
  return seen !== null && seen.element === element && seen.past;
}
