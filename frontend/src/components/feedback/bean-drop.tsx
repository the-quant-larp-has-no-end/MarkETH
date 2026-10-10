"use client";

import { type ReactNode, useEffect, useState } from "react";

import { TokenIcon, type TokenIconSize } from "@/components/brand";
import { BEAN_DROP_COUNT, BEAN_DROP_MS } from "@/lib/design";
import { usePrefersReducedMotion } from "@/lib/hooks";
import { cn } from "@/lib/utils";

/* Each bean falls for a part of the 600 ms and starts a little after the one before. */
const FALL_MS = BEAN_DROP_MS * 0.6;
const STAGGER_MS = (BEAN_DROP_MS - FALL_MS) / (BEAN_DROP_COUNT - 1);
/* Where along the figure each bean comes down, as a share of its width. */
const LANES = ["20%", "65%", "40%", "85%", "5%"] as const;

export type BeanDropProps = {
  /**
   * What identifies one arrival of tokens, or null when there is none. The drop plays
   * once each time this becomes a new value, and once on mount when it is set then.
   * Keep it null on page load: tokens booked while nobody was looking simply show.
   */
  arrival: string | number | null;
  /**
   * The figure the beans fall into. As a function it is told whether they are still
   * falling, so it can show the figure from before until they have landed:
   * `(falling) => <Amount value={falling ? before : balance_cr} />`.
   */
  children: ReactNode | ((falling: boolean) => ReactNode);
  className?: string;
};

/**
 * The bean drop (DESIGN.md §7): when tokens arrive in front of the user, a few token
 * icons fall into the figure that grows, over 600 ms, and then the figure updates. It is
 * for exactly these: the welcome tokens, a redeemed code, a payout that comes while its
 * market is on screen, and a viewer who opens the leaderboard in first place.
 *
 * With reduced motion nothing falls and the figure updates at once.
 */
export function BeanDrop({ arrival, children, className }: BeanDropProps) {
  const reduced = usePrefersReducedMotion();
  const [landed, setLanded] = useState<string | number | null>(null);
  const falling = arrival !== null && !reduced && landed !== arrival;

  useEffect(() => {
    if (!falling) return;
    const timer = window.setTimeout(() => setLanded(arrival), BEAN_DROP_MS);
    return () => window.clearTimeout(timer);
  }, [falling, arrival]);

  return (
    // As wide as the figure and no wider, so the beans fall into it and not beside it.
    <span data-slot="bean-drop" className={cn("relative inline-flex w-fit", className)}>
      {typeof children === "function" ? children(falling) : children}
      {falling && (
        <span
          aria-hidden
          key={arrival}
          className="pointer-events-none absolute inset-x-0 top-0 h-full motion-reduce:hidden"
        >
          {LANES.slice(0, BEAN_DROP_COUNT).map((left, index) => (
            <TokenIcon
              key={left}
              className="absolute top-1/2 -mt-2 animate-bean-drop"
              // Position and timing of one bean; the movement itself is in globals.css.
              style={{
                left,
                animationDuration: `${FALL_MS}ms`,
                animationDelay: `${index * STAGGER_MS}ms`,
              }}
            />
          ))}
        </span>
      )}
    </span>
  );
}

/**
 * The bean bounce (DESIGN.md §7): the token icon of the trade receipt hops once, 200 ms,
 * when it appears. With reduced motion it stands still.
 */
export function BeanBounce({ size = 32, className }: { size?: TokenIconSize; className?: string }) {
  return <TokenIcon size={size} className={cn("motion-safe:animate-bean-bounce", className)} />;
}
