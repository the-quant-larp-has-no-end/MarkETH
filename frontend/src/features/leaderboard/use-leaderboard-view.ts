"use client";

import { usePathname, useSearchParams } from "next/navigation";

import { queryOf, readView, type View } from "./view";

/**
 * The view of the leaderboard, kept in the address (DESIGN.md §11, §13): `view` is what
 * the query string says, and `show` puts another view there. Each change is a step in
 * the history, so the back button returns to the view before it, and the address of any
 * view can be shared.
 *
 * The address is written with the browser's own History API, which the router follows
 * (`useSearchParams` sees the change) without asking the server for the route again. A
 * chip answers at once, and the page does not scroll: the chip stays under the finger.
 */
export function useLeaderboardView(): { view: View; show: (next: View) => void } {
  const search = useSearchParams();
  const pathname = usePathname();
  const view = readView(search);

  const show = (next: View) => {
    const query = queryOf(next);
    if (query === queryOf(view)) return;
    window.history.pushState(null, "", query === "" ? pathname : `${pathname}?${query}`);
  };

  return { view, show };
}
