"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useRef, useState } from "react";

/**
 * A way out of the page that was held back and waits for the user's answer. A link says
 * where it leads and which link it was, so that the focus can go back to it.
 */
export type Leaving = { by: "link"; href: string; from: HTMLAnchorElement } | { by: "back" };

/*
 * The guard takes steps in the history itself: it puts an entry of its own on top and
 * takes it off again. A step back it asked for must not be read as the user's. Each one
 * is counted here when it is asked for and marked when it arrives, by a listener that is
 * registered before any guard's own and therefore hears the event first.
 */
let ownStepsBack = 0;
const ownSteps = new WeakSet<Event>();
let watching = false;

function watchOwnSteps(): void {
  if (watching) return;
  watching = true;
  window.addEventListener("popstate", (event) => {
    if (ownStepsBack === 0) return;
    ownStepsBack -= 1;
    ownSteps.add(event);
  });
}

function stepBack(): void {
  ownStepsBack += 1;
  window.history.back();
}

/** Puts an entry for the page as it is on top of the history, and says where that is. */
function putOwnEntry(): string {
  window.history.pushState(window.history.state, "", window.location.href);
  return window.location.href;
}

/**
 * While `active`, leaving the page asks first (DESIGN.md §13, Codes: the codes of a new
 * batch cannot be shown again). There are three ways out of a page, and each is held:
 *
 * - Closing the tab, reloading or going to another site: the browser asks, in its own
 *   words (`beforeunload`).
 * - A link inside the app: the tap is held back and `leaving` says where it wanted to go.
 * - The back button: the guard keeps an entry of its own on top of the history, so one
 *   step back stays on the page, and `leaving` says that the user wants to go back.
 *
 * The screen shows its confirmation while `leaving` is set, and answers with `stay()` or
 * `leave()`, which then goes where the user wanted to. A link that opens a new tab, and
 * one that is a download, leave the page as it is and pass.
 */
export function useLeaveGuard(active: boolean): {
  leaving: Leaving | null;
  stay: () => void;
  leave: () => void;
} {
  const router = useRouter();
  const pathname = usePathname();
  const [leaving, setLeaving] = useState<Leaving | null>(null);
  /** Whether the guard's own entry is the current one of the history, and where it was put. */
  const ownEntry = useRef<string | null>(null);

  const onClick = useEffectEvent((event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0) return;
    // With a modifier the link opens beside this page, which stays as it is.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!(anchor instanceof HTMLAnchorElement)) return;
    if (anchor.hasAttribute("download")) return;
    if (anchor.target !== "" && anchor.target !== "_self") return;

    const to = new URL(anchor.href, window.location.href);
    // Another site unloads the page, and the browser asks about that itself.
    if (to.origin !== window.location.origin) return;

    event.preventDefault();
    event.stopPropagation();
    // A link to the page one is on leads nowhere.
    if (to.pathname === pathname) return;
    setLeaving({ by: "link", href: to.pathname + to.search + to.hash, from: anchor });
  });

  const onPopState = useEffectEvent((event: PopStateEvent) => {
    if (ownSteps.has(event)) return;
    // The user stepped back, off the guard's entry: the page is still this one.
    ownEntry.current = null;
    setLeaving({ by: "back" });
  });

  useEffect(() => {
    if (!active) return;
    watchOwnSteps();

    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // What browsers looked at before they took `preventDefault` for an answer.
      event.returnValue = "";
    };
    const click = (event: MouseEvent) => onClick(event);
    const popState = (event: PopStateEvent) => onPopState(event);

    ownEntry.current = putOwnEntry();
    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("popstate", popState);
    document.addEventListener("click", click, true);

    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("popstate", popState);
      document.removeEventListener("click", click, true);
      // Take the guard's entry off again, unless the page has moved on from it.
      if (ownEntry.current !== null && ownEntry.current === window.location.href) stepBack();
      ownEntry.current = null;
    };
  }, [active]);

  return {
    leaving: active ? leaving : null,
    stay() {
      // After a step back the guard's entry is gone; the page is guarded again with another.
      // It is put here, on the user's tap, and not when the step back arrived: a browser
      // skips over an entry that a page added without the user having touched the page.
      if (leaving?.by === "back" && active) ownEntry.current = putOwnEntry();
      setLeaving(null);
    },
    leave() {
      const way = leaving;
      setLeaving(null);
      if (way === null) return;
      if (way.by === "link") {
        // The new address takes the place of the guard's entry.
        ownEntry.current = null;
        router.replace(way.href);
      } else {
        // The first step back only took the guard's entry off; this is the one that was meant.
        stepBack();
      }
    },
  };
}
