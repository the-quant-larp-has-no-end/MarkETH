"use client";

import * as React from "react";
import { Toggle as TogglePrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * A button that is on or off, with `aria-pressed`. It has no look of its own: the outcome
 * toggle and the chips give it one for `data-[state=on]` and `data-[state=off]`.
 */
export const toggleClasses = cn(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap select-none",
  "transition-[background-color,border-color,color] duration-(--duration-fast)",
  "disabled:pointer-events-none",
  "[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-5",
);

function Toggle({ className, ...props }: React.ComponentProps<typeof TogglePrimitive.Root>) {
  return (
    <TogglePrimitive.Root data-slot="toggle" className={cn(toggleClasses, className)} {...props} />
  );
}

export { Toggle };
