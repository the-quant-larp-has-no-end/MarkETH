"use client";

import * as React from "react";
import { Label as LabelPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/** The label above a field: `caption`, `--muted-foreground` (DESIGN.md §12). */
function Label({ className, ...props }: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn("type-caption text-muted-foreground select-none", className)}
      {...props}
    />
  );
}

export { Label };
