"use client";

import * as React from "react";
import { CheckIcon } from "lucide-react";
import { Checkbox as CheckboxPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * The box of a checkbox (DESIGN.md §12): 20 px, `--radius-sm`, a 1 px `--border-strong`
 * outline on `--background`; ticked, a `--foreground` fill with a `--primary-foreground`
 * check. The tap target is the row around it, which `CheckboxField` of
 * `@/components/form` builds: the box and its text, at least 44 px tall.
 */
function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "peer flex size-5 shrink-0 items-center justify-center rounded-sm border border-border-strong bg-background",
        "transition-[background-color,border-color] duration-(--duration-fast)",
        "data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
        "aria-invalid:border-no",
        "disabled:cursor-not-allowed disabled:border-subtle disabled:data-[state=checked]:bg-subtle",
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator data-slot="checkbox-indicator">
        <CheckIcon aria-hidden className="size-4" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export { Checkbox };
