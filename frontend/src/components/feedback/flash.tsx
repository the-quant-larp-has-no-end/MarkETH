"use client";

import { type ReactNode, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Flashes behind a figure when it changes (DESIGN.md §7): the background is `--raised`
 * for 600 ms and fades. The flash is neutral, not green or red, so a busy market does
 * not strobe colour. It does not play for the first value, and not with reduced motion.
 *
 * `watch` is what a change is measured by: the value as it is shown.
 */
export function Flash({
  watch,
  children,
  className,
}: {
  watch: string | number;
  children: ReactNode;
  className?: string;
}) {
  const [seen, setSeen] = useState({ value: watch, changes: 0 });
  if (seen.value !== watch) setSeen({ value: watch, changes: seen.changes + 1 });

  return (
    <span
      // A new key starts the animation again for every change.
      key={seen.changes}
      data-slot="flash"
      className={cn("rounded-sm", seen.changes > 0 && "motion-safe:animate-price-flash", className)}
    >
      {children}
    </span>
  );
}
