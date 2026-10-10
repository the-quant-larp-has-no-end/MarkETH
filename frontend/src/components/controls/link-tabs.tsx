"use client";

import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useRef } from "react";

import { Link } from "@/components/shell/link";
import { activeTabClasses, tabClasses, tabListClasses } from "@/components/ui/tabs";
import { keepInSight } from "@/lib/keep-in-sight";
import { cn } from "@/lib/utils";

export type LinkTab = {
  label: string;
  href: string;
  /** Whether a path belongs to this tab. By default: the path is `href`. */
  matches?: (pathname: string) => boolean;
};

/**
 * Tabs that are links (DESIGN.md §13, Admin): the look of `Tabs`, each tab a route of
 * its own, the current one marked with `aria-current`. Use `Tabs` when the views share
 * one route (Portfolio: Positions and History).
 *
 * A row that is wider than the screen scrolls sideways and keeps the current tab in
 * sight (DESIGN.md §12): only the row moves, never the page.
 */
export function LinkTabs({
  label,
  tabs,
  className,
  children,
}: {
  /** What the tabs switch between, for a screen reader: "Admin". */
  label: string;
  tabs: readonly LinkTab[];
  className?: string;
  /** A link that sits beside the tabs at the end of the row. */
  children?: ReactNode;
}) {
  const pathname = usePathname();
  const row = useRef<HTMLElement>(null);

  useEffect(() => {
    keepInSight(row.current, row.current?.querySelector('[aria-current="page"]'));
  }, [pathname]);

  return (
    <nav
      ref={row}
      aria-label={label}
      data-slot="link-tabs"
      className={cn(tabListClasses, className)}
    >
      {tabs.map((tab) => {
        const active = tab.matches ? tab.matches(pathname) : pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(tabClasses, active && activeTabClasses)}
          >
            {tab.label}
          </Link>
        );
      })}
      {children !== undefined && <div className="ml-auto flex items-center">{children}</div>}
    </nav>
  );
}
