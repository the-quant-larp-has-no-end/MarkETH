"use client";

import { type RefObject, useEffect, useState } from "react";

/** How much of the element has to be in view for it to count as on screen. */
const SHARE_IN_VIEW = 0.5;

const hasObserver = typeof IntersectionObserver !== "undefined";

/** A length the browser has resolved to pixels, as the inset it takes off the viewport. */
function inset(length: string | undefined): string {
  return length !== undefined && /^\d+(\.\d+)?px$/.test(length) ? `-${length}` : "0px";
}

/**
 * Whether one element inside `container` can be seen: true or false once the browser has
 * said, null until then and while there is no such element. "Seen" means that at least
 * half of it lies between the fixed bars of the shell, not merely inside the window.
 *
 * The element is looked up by `selector` each time `placement` changes: pass a value that
 * changes whenever another element may match, and null while none does.
 *
 * `bars` is an element whose top and bottom padding are the room the top bar and the tab
 * bar take (`--top-bar-space`, `--tab-bar-space`): the browser resolves the two lengths,
 * safe areas included, and they are read from it as they are.
 */
export function useOnScreen({
  container,
  selector,
  placement,
  bars,
}: {
  container: RefObject<HTMLElement | null>;
  selector: string;
  placement: string | null;
  bars: RefObject<HTMLElement | null>;
}): boolean | null {
  const [visible, setVisible] = useState<boolean | null>(null);
  // What was seen of an element says nothing about the next one once it is gone.
  if (placement === null && visible !== null) setVisible(null);

  useEffect(() => {
    if (placement === null || !hasObserver) return;
    const element = container.current?.querySelector(selector);
    if (element === null || element === undefined) return;

    const covered = bars.current === null ? null : getComputedStyle(bars.current);
    const observer = new IntersectionObserver(
      (entries) => {
        const latest = entries[entries.length - 1];
        if (latest === undefined) return;
        setVisible(latest.isIntersecting && latest.intersectionRatio >= SHARE_IN_VIEW);
      },
      {
        rootMargin: `${inset(covered?.paddingTop)} 0px ${inset(covered?.paddingBottom)} 0px`,
        threshold: SHARE_IN_VIEW,
      },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [container, selector, placement, bars]);

  return placement === null ? null : visible;
}
