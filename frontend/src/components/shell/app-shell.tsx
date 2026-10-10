"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { keys } from "@/lib/api/keys";

import { ShellProvider, useShellStore } from "./shell-store";
import { TabBar } from "./tab-bar";

/**
 * Follows the route the app is on. The back arrow learns from it whether it has a way
 * back, and every arrival on another screen reads `GET /me` again (DESIGN.md §14, "Live
 * data"): payouts, refunds and monthly tokens are booked between visits, and the balance
 * in the top bar follows them. The route the page was loaded on reads nothing here:
 * start-up has just done that.
 */
function RouteWatch() {
  const pathname = usePathname();
  const store = useShellStore();
  const client = useQueryClient();

  useEffect(() => {
    if (!store.noteRoute(pathname)) return;
    // A screen that reads `/me` itself as it opens (the Wallet) shares the one request.
    void client.invalidateQueries({ queryKey: keys.me, exact: true }, { cancelRefetch: false });
  }, [pathname, store, client]);

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
