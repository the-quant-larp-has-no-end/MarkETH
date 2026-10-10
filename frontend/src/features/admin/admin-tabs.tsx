"use client";

import { ScanQrCodeIcon } from "lucide-react";

import { type LinkTab, LinkTabs } from "@/components/controls";
import { Link } from "@/components/shell";
import { WIDE_QUERY } from "@/lib/design";
import { useMediaQuery } from "@/lib/hooks";
import { cn } from "@/lib/utils";

const TABS: readonly LinkTab[] = [
  { label: "Markets", href: "/admin" },
  { label: "Codes", href: "/admin/codes" },
  { label: "Risk", href: "/admin/risk" },
  { label: "Users", href: "/admin/users" },
];

/** The link to the Barista screen: an admin has no Scan tab and reaches the scanner here. */
function ScanLink({ className }: { className?: string }) {
  return (
    <Link
      href="/barista"
      className={cn(
        "inline-flex h-11 items-center gap-2 type-label whitespace-nowrap text-muted-foreground transition-colors duration-(--duration-base) hover:text-foreground",
        className,
      )}
    >
      <ScanQrCodeIcon aria-hidden className="size-5 shrink-0" />
      Scan vouchers
    </Link>
  );
}

/**
 * The four tabs of the admin area (DESIGN.md §13), each a link to its route, and the
 * "Scan vouchers" link to the Barista screen. It stands at the top of each of the four
 * screens, under the top bar. A row of tabs that is wider than the screen scrolls
 * sideways and keeps the current tab in sight.
 *
 * From 768 px the link sits beside the tabs. On a phone the row has no room for it, and
 * the scanner is the one thing an admin opens on a phone at the bar: there the link has a
 * line of its own under the tabs, in full view.
 */
export function AdminTabs({ className }: { className?: string }) {
  const wide = useMediaQuery(WIDE_QUERY);

  if (wide) {
    return (
      <LinkTabs label="Admin" tabs={TABS} className={className}>
        <ScanLink className="px-3" />
      </LinkTabs>
    );
  }

  return (
    <div data-slot="admin-tabs" className={cn("flex flex-col gap-1", className)}>
      <LinkTabs label="Admin" tabs={TABS} />
      <ScanLink className="self-start" />
    </div>
  );
}
