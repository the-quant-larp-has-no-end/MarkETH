import { DESKTOP_QUERY, REDUCED_MOTION_QUERY, WIDE_QUERY } from "@/lib/design";

/**
 * `window.matchMedia` for tests, which jsdom does not have.
 *
 * A test starts as a phone whose owner asked for reduced motion: no width query matches,
 * so sheets are bottom sheets and the tab bar is at the bottom, and nothing animates, so
 * a figure never waits for a bean to land. Change that for the test at hand:
 *
 *     setViewport("wide");                       // 768 px and up: dialogs, tabs in the top bar
 *     setMediaQuery(REDUCED_MOTION_QUERY, false) // let the animations run
 */

const matching = new Map<string, boolean>();
const listeners = new Map<string, Set<() => void>>();

function defaults(): void {
  matching.clear();
  matching.set(REDUCED_MOTION_QUERY, true);
}

/** Makes one media query match or not, and tells whoever listens. */
export function setMediaQuery(query: string, matches: boolean): void {
  matching.set(query, matches);
  for (const listener of listeners.get(query) ?? []) listener();
}

/** The three layouts of DESIGN.md §6: below 768 px, from 768 px, from 1024 px. */
export function setViewport(viewport: "phone" | "wide" | "desktop"): void {
  setMediaQuery(WIDE_QUERY, viewport !== "phone");
  setMediaQuery(DESKTOP_QUERY, viewport === "desktop");
}

/** Run before every test (`src/test/setup.ts`). */
export function resetMedia(): void {
  defaults();
  listeners.clear();
}

export function installMatchMedia(): void {
  defaults();
  window.matchMedia = (query: string): MediaQueryList => {
    const add = (listener: () => void) => {
      let set = listeners.get(query);
      if (set === undefined) {
        set = new Set();
        listeners.set(query, set);
      }
      set.add(listener);
    };
    const remove = (listener: () => void) => {
      listeners.get(query)?.delete(listener);
    };
    return {
      media: query,
      get matches() {
        return matching.get(query) ?? false;
      },
      onchange: null,
      addEventListener: (_type: string, listener: () => void) => add(listener),
      removeEventListener: (_type: string, listener: () => void) => remove(listener),
      addListener: add,
      removeListener: remove,
      dispatchEvent: () => false,
    } as unknown as MediaQueryList;
  };
}
