"use client";

import { Illustration } from "@/components/brand";

import { useShellState } from "./shell-store";

/**
 * The Polybahn loader (DESIGN.md §3, §7): a route change that takes longer than 300 ms
 * shows a 2 px `--foreground` progress line under the top bar, with the Polybahn car
 * riding its front edge. It never blocks input. The delay and the run are CSS
 * (`animate-route-progress`); with reduced motion the car is not shown and the line
 * stands still.
 */
export function RouteProgress() {
  const { navigating } = useShellState();
  if (!navigating) return null;

  return (
    <div
      aria-hidden
      data-slot="route-progress"
      className="pointer-events-none absolute inset-x-0 top-full h-0.5 overflow-x-clip print:hidden"
    >
      <div className="relative h-full w-full animate-route-progress bg-foreground">
        <Illustration
          name="polybahn"
          className="absolute right-0 bottom-full size-5 text-foreground motion-reduce:hidden"
        />
      </div>
    </div>
  );
}
