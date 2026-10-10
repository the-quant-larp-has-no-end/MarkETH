"use client";

import { usePathname } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { ShellProvider, useShellStore } from "./shell-store";
import { TabBar } from "./tab-bar";

/** Follows the route the app is on, so the back arrow knows whether it has a way back. */
function RouteWatch() {
  const pathname = usePathname();
  const store = useShellStore();

  useEffect(() => {
    store.noteRoute(pathname);
  }, [pathname, store]);

  return null;
}

/**
 * The app shell (DESIGN.md §11): what stays while the routes change. It holds the tab
 * bar and the state the bars share; each screen brings its own top bar through `Screen`.
 * It is rendered only once the app knows who is looking, so everything inside may call
 * `useMe()` and `useConfig()`.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ShellProvider>
      <RouteWatch />
      <div data-slot="app-shell" className="flex min-h-dvh flex-col">
        {children}
      </div>
      <TabBar />
    </ShellProvider>
  );
}
