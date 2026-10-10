"use client";

import { usePathname } from "next/navigation";

import { useMe } from "@/lib/api/session";
import { WIDE_QUERY } from "@/lib/design";
import { useMediaQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import { Link } from "./link";
import { useShellState } from "./shell-store";
import { tabsFor } from "./tabs";

/**
 * The tab bar (DESIGN.md §11): 64 px tall plus the bottom safe area, `--card`, a 1 px
 * `--border` on top, fixed. Each tab is an icon above a `caption` label; the active one
 * is `--foreground`, the others `--muted-foreground`, and there is no coloured
 * indicator. From 768 px the tabs sit in the top bar instead (`TopTabs`), so this
 * renders nothing there.
 */
export function TabBar() {
  const pathname = usePathname();
  const { role } = useMe();
  const { tabBarHidden } = useShellState();
  const wide = useMediaQuery(WIDE_QUERY);
  if (wide || tabBarHidden) return null;

  return (
    <nav
      aria-label="Main"
      data-slot="tab-bar"
      // Its height, hairline and safe area included, is --tab-bar-space: what the
      // content and anything pinned keep clear of.
      className="fixed inset-x-0 bottom-0 z-40 h-(--tab-bar-space) border-t bg-card pb-safe print:hidden"
    >
      <ul className="flex h-full items-stretch">
        {tabsFor(role).map((tab) => {
          const active = tab.matches(pathname);
          return (
            <li key={tab.href} className="flex min-w-0 flex-1">
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-w-0 flex-1 flex-col items-center justify-center gap-1 type-caption",
                  "transition-colors duration-(--duration-base)",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                <tab.icon aria-hidden className="size-6 shrink-0" />
                <span className="max-w-full truncate">{tab.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** The same tabs as a row of `label` links: what the top bar shows from 768 px. */
export function TopTabs({ className }: { className?: string }) {
  const pathname = usePathname();
  const { role } = useMe();

  return (
    <nav
      aria-label="Main"
      data-slot="top-tabs"
      className={cn("min-w-0 overflow-x-auto", className)}
    >
      <ul className="flex items-stretch">
        {tabsFor(role).map((tab) => {
          const active = tab.matches(pathname);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex h-11 items-center px-3 type-label whitespace-nowrap",
                  "transition-colors duration-(--duration-base) hover:text-foreground",
                  active ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
