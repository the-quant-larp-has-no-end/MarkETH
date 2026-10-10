"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

import { TopBar } from "./top-bar";

const WIDTHS = {
  /** DESIGN.md §6: up to 720 px from 768 px, up to 1080 px from 1024 px. */
  default: "md:max-w-[45rem] lg:max-w-[67.5rem]",
  /** Stays a 720 px column at every width: a form, a list of rows. */
  narrow: "md:max-w-[45rem]",
} as const;

export type ScreenProps = {
  /**
   * The top bar (DESIGN.md §11). `main`: the logo with name. `inner`: a back arrow and
   * the title.
   */
  top: "main" | "inner";
  /**
   * The name of the screen, and its one `h1`: shown in the top bar of an inner screen,
   * read out but not shown on a main one. Leave it out when the screen shows its own
   * `h1` (the question on Market detail, "Leaderboard").
   */
  title?: string;
  /** Inner screens: where the back arrow goes when the screen was opened directly. */
  backTo?: string;
  width?: keyof typeof WIDTHS;
  /**
   * No gutters, no column, no gaps: the content fills the screen below the top bar (the
   * Barista scanner).
   */
  bleed?: boolean;
  /**
   * Something that stays at the bottom of the screen, above the tab bar, while the
   * content scrolls: the outcome buttons of Market detail once the user has scrolled
   * past them. It is as wide as the content column. Pass null while it is not shown.
   */
  pinned?: ReactNode;
  className?: string;
  children: ReactNode;
};

/**
 * The frame every screen renders first: the top bar, then the content column with its
 * 16 px gutters and 24 px between sections, clear of the tab bar. A screen is
 * `<Screen top="main" title="Wallet">…</Screen>` and nothing around it: no screen puts
 * one inside another.
 */
export function Screen({
  top,
  title,
  backTo,
  width = "default",
  bleed = false,
  pinned,
  className,
  children,
}: ScreenProps) {
  const hasPinned = pinned !== undefined && pinned !== null && pinned !== false;

  return (
    <>
      <TopBar variant={top} title={top === "inner" ? title : undefined} backTo={backTo} />
      <main
        data-slot="screen"
        className={cn(
          "flex w-full flex-1 flex-col",
          !bleed && ["mx-auto gap-6 px-4 pt-4", WIDTHS[width]],
          // The tab bar lies over the bottom of the page; what is pinned sits right on it.
          bleed || hasPinned ? "pb-(--tab-bar-space)" : "pb-[calc(var(--tab-bar-space)+1.5rem)]",
          className,
        )}
      >
        {top === "main" && title !== undefined && <h1 className="sr-only">{title}</h1>}
        {children}
        {hasPinned && (
          <div
            data-slot="pinned"
            className={cn(
              "sticky bottom-(--tab-bar-space) z-30 mt-auto border-t bg-card py-3 print:hidden",
              !bleed && "-mx-4 px-4",
            )}
          >
            {pinned}
          </div>
        )}
      </main>
    </>
  );
}
