"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";

/** What the shell shows right now, beyond the route. */
export type ShellState = {
  /** A screen asked for the tab bar to be out of the way. */
  tabBarHidden: boolean;
  /** A route change was started and has not arrived yet. */
  navigating: boolean;
};

export type ShellStore = {
  subscribe(listener: () => void): () => void;
  getState(): ShellState;
  /** Hides the tab bar until the returned function is called. */
  hideTabBar(): () => void;
  /** Marks a route change as under way until the returned function is called. */
  beginNavigation(): () => void;
  /**
   * Notes the route the app is on, so "back" knows whether it has somewhere to go. Says
   * whether the app arrived there from another route: false for the route the page was
   * loaded on, and for the same route noted twice.
   */
  noteRoute(pathname: string): boolean;
  /** Whether one step back in the history stays inside the app. */
  canGoBack(): boolean;
};

const AT_REST: ShellState = { tabBarHidden: false, navigating: false };

export function createShellStore(): ShellStore {
  let hiding = 0;
  let navigating = 0;
  let route: string | null = null;
  let moved = false;
  let state = AT_REST;
  const listeners = new Set<() => void>();

  const update = () => {
    const next = { tabBarHidden: hiding > 0, navigating: navigating > 0 };
    if (next.tabBarHidden === state.tabBarHidden && next.navigating === state.navigating) return;
    state = next;
    for (const listener of listeners) listener();
  };

  /** A request that is taken back at most once, however often its release is called. */
  const hold = (change: (by: number) => void) => {
    change(1);
    update();
    let held = true;
    return () => {
      if (!held) return;
      held = false;
      change(-1);
      update();
    };
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getState: () => state,
    hideTabBar: () => hold((by) => (hiding += by)),
    beginNavigation: () => hold((by) => (navigating += by)),
    noteRoute(pathname) {
      // The same route noted twice (an effect that ran again) is no move.
      const arrived = route !== null && route !== pathname;
      if (arrived) moved = true;
      route = pathname;
      return arrived;
    },
    canGoBack() {
      // Where the browser can say whether an earlier entry of this tab belongs to the
      // app, ask it. Elsewhere: has the app changed its route since the page loaded?
      const browser = (window as { navigation?: { canGoBack?: unknown } }).navigation;
      if (typeof browser?.canGoBack === "boolean") return browser.canGoBack;
      return moved;
    },
  };
}

/** Outside a shell there is no tab bar to hide and no progress to show. */
const ShellContext = createContext<ShellStore>(createShellStore());

export function ShellProvider({ children }: { children: ReactNode }) {
  const [store] = useState(createShellStore);
  return <ShellContext.Provider value={store}>{children}</ShellContext.Provider>;
}

export function useShellStore(): ShellStore {
  return useContext(ShellContext);
}

export function useShellState(): ShellState {
  const store = useShellStore();
  return useSyncExternalStore(store.subscribe, store.getState, () => AT_REST);
}

/**
 * Takes the tab bar out of the way while `active` is true: while the trade sheet is open
 * and while the Barista scanner's camera is live (DESIGN.md §11).
 */
export function useHideTabBar(active: boolean): void {
  const store = useShellStore();
  useEffect(() => {
    if (!active) return;
    return store.hideTabBar();
  }, [active, store]);
}
